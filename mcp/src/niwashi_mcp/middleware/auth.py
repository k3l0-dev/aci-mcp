# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
middleware/auth.py

API-key authentication middleware for the MCP HTTP server.

Validates every incoming request against a set of pre-shared bearer tokens
loaded from MCP_API_KEYS (comma-separated list in .env or environment).

Accepted header forms:
  Authorization: Bearer <token>
  X-API-Key: <token>

When the KeyStore is empty the middleware is a no-op and a startup
warning is emitted by the caller.  This allows unauthenticated local dev
while making production misconfiguration visible.

Key features:
  KeyStore      — thread-safe, hot-reloadable key container (SIGHUP-friendly)
  RateLimiter   — fixed-window per-IP limiter; returns 429 after threshold
  WWW-Authenticate — includes resource_metadata URL per RFC 9728 so clients
                     find the OAuth discovery endpoint without guessing

Timing safety: all comparisons use hmac.compare_digest to prevent
timing-oracle attacks on token values.
"""

import hmac
import logging
import os
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from niwashi_mcp.exceptions import AuthenticationError

logger = logging.getLogger("niwashi-mcp.auth")

_BEARER_PREFIX = "Bearer "

# MCP 2025-03-26: clients probe these endpoints before attempting auth.
# Blocking them prevents OAuth discovery and breaks spec-compliant clients.
_UNAUTHENTICATED_PREFIXES = ("/.well-known/",)
_UNAUTHENTICATED_PATHS = frozenset({"/register"})

_TOO_MANY_REQUESTS = JSONResponse(
    {
        "error": "Too Many Requests",
        "detail": "Too many failed authentication attempts. Try again later.",
    },
    status_code=429,
    headers={"Retry-After": "60"},
)

# Ceiling on how many source addresses the rate limiter tracks at once.
#
# The table is keyed by whatever address the peer presents, and it is written
# by requests that carry no valid credential — so an unauthenticated caller
# chooses how many keys it gains. Measured at 206 bytes per address, 200,000
# distinct sources retain 39.3 MiB; the real bound is the address space, which
# for IPv6 means no bound at all. Under a container memory limit that ends as a
# SIGKILL, and since CPython does not read cgroup limits it arrives as exit 137
# with no traceback and no log line — the server is simply gone.
#
# 4096 entries costs roughly 4.5 MiB in the worst case (every tracked address
# holding a full window of attempts) and sits orders of magnitude above any
# real population reaching this code, which only ever sees requests that have
# already failed authentication.
_MAX_TRACKED_IPS = 4096


class KeyStore:
    """Thread-safe container for the set of valid API keys with hot-reload support.

    Designed to be updated at runtime via SIGHUP without restarting the server.
    The internal frozenset is replaced atomically under a lock so in-flight
    requests that already called get() continue with the old set uninterrupted.

    `auth_required` closes a fail-open path. An empty key set disables the
    middleware entirely (see ApiKeyMiddleware.dispatch), which is the intended
    behaviour on loopback but a silent removal of all authentication on a
    routable bind. Startup already refuses that combination — but the SIGHUP
    path could re-create it afterwards, since a reload only has to produce an
    empty set: a truncated .env, a file caught mid-rotation, an unmounted
    secret volume, or a mistyped key in `kubectl create secret` all do. The
    refusal lives here rather than in the signal handler so it holds for every
    call site, present and future, and is testable without an HTTP layer.
    """

    def __init__(self, keys: frozenset[str], *, auth_required: bool = False) -> None:
        self._keys = keys
        self._auth_required = auth_required
        self._lock = threading.Lock()

    def get(self) -> frozenset[str]:
        """Return the current key set as an immutable snapshot."""
        with self._lock:
            return self._keys

    def reload(self, new_keys: frozenset[str]) -> bool:
        """Replace the key set atomically. Safe to call from a signal handler.

        Returns:
            True when the new set was applied. False when it was refused
            because it is empty and this deployment requires authentication —
            the previous keys are kept, and the caller is expected to report it
            loudly. Refusing beats applying: a reload that silently unlocks
            every tool is the failure nobody notices until it is used.
        """
        with self._lock:
            if self._auth_required and not new_keys:
                return False
            self._keys = new_keys
            return True

    def __bool__(self) -> bool:
        with self._lock:
            return bool(self._keys)

    def __len__(self) -> int:
        with self._lock:
            return len(self._keys)


class RateLimiter:
    """Fixed-window per-IP rate limiter for failed authentication attempts.

    Tracks the timestamps of recent failed attempts for each IP address.
    Once an IP exceeds max_attempts within window_s seconds, subsequent
    requests return immediately until the window rolls over.

    Uses time.monotonic() to avoid sensitivity to wall-clock adjustments.
    Thread-safe via a single lock; the critical section is O(window-size) list
    comprehension, which is negligible in practice.

    Two properties worth stating, because both are easy to over-read:

    * The tracking table is **bounded** — see _MAX_TRACKED_IPS. Entries expire
      on a sweep, and a hard ceiling backs it up. Without both, the map grows
      with the number of distinct source addresses that ever failed
      authentication, which an unauthenticated caller chooses freely.
    * "Per-IP" means per *peer* address, which is only per-client when the
      server is reached directly. Behind a reverse proxy the peer is the proxy
      for every request, so the window becomes global rather than per-client.
      That is stricter, not laxer — the brute-force ceiling still holds — but
      it is not per-client isolation, and nothing here reads X-Forwarded-For
      (trusting it unvalidated would let a caller mint a fresh budget on every
      request, which is worse than the imprecision).
    """

    def __init__(
        self,
        *,
        max_attempts: int = 30,
        window_s: int = 60,
        max_tracked_ips: int = _MAX_TRACKED_IPS,
    ) -> None:
        self._max = max_attempts
        self._window = window_s
        self._max_tracked = max_tracked_ips
        self._counts: dict[str, list[float]] = {}
        self._next_sweep = 0.0
        self._lock = threading.Lock()

    def _sweep(self, cutoff: float) -> None:
        """Drop every address whose whole window has expired. Caller holds the lock.

        Pruning used to happen only for the address being looked at, so an
        address seen once kept its key forever — the list emptied, the entry
        stayed. Sweeping the whole table once per window makes the retained set
        "addresses that failed within the last window" instead of "addresses
        that have ever failed".
        """
        self._counts = {
            ip: hits for ip, hits in self._counts.items() if hits and hits[-1] > cutoff
        }

    def is_allowed(self, ip: str) -> bool:
        """Return True and record the attempt if the IP is within the limit.

        Returns False (without recording) if the IP has already exceeded
        max_attempts within the current window, indicating the request should
        be rejected with 429 — or if the tracking table is full of addresses
        that are all still inside their window. That second case is a refusal
        under pressure rather than an eviction, deliberately: evicting the
        oldest entry would hand an attacker who sprays addresses a fresh budget
        on every one of them, which is the property the limiter exists to deny.
        It can only ever affect a request that has already failed to
        authenticate.
        """
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            if now >= self._next_sweep:
                self._sweep(cutoff)
                self._next_sweep = now + self._window

            hits = [t for t in self._counts.get(ip, ()) if t > cutoff]
            if len(hits) >= self._max:
                self._counts[ip] = hits
                return False

            if ip not in self._counts and len(self._counts) >= self._max_tracked:
                self._sweep(cutoff)
                if len(self._counts) >= self._max_tracked:
                    return False

            hits.append(now)
            self._counts[ip] = hits
            return True


def load_api_keys() -> frozenset[str]:
    """Read MCP_API_KEYS from the environment and return a frozenset of valid tokens."""
    raw = os.environ.get("MCP_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def _extract_token(request: Request) -> str | None:
    """Extract the bearer token from Authorization or X-API-Key headers.

    Checks Authorization: Bearer <token> first, then falls back to X-API-Key.
    Returns None if neither header is present or the Authorization header does
    not use the Bearer scheme.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX):]
    return request.headers.get("X-API-Key") or None


def _build_unauthorized(request: Request) -> JSONResponse:
    """Build a 401 response with a resource_metadata hint in WWW-Authenticate.

    RFC 9728 requires the 401 response to advertise the URL of the OAuth
    Protected Resource Metadata document so clients find it in one round-trip
    rather than probing multiple /.well-known/ candidates.
    """
    base = str(request.base_url).rstrip("/")
    metadata_url = f"{base}/.well-known/oauth-protected-resource"
    return JSONResponse(
        {"error": "Unauthorized", "detail": "A valid API key is required."},
        status_code=401,
        headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'},
    )


def _is_valid(token: str, keys: frozenset[str]) -> bool:
    """Membership test using constant-time comparisons to prevent a timing oracle
    on any single key.

    Uses hmac.compare_digest for each key so no individual comparison leaks a
    partial byte-wise match via timing. Note: any() short-circuits on the
    first match, so total loop time is NOT independent of match position — a
    valid token matching an early key in iteration order returns faster than
    one matching a late key. For an invalid token (the brute-force-guessing
    case this guards against), every key is compared every time, so timing is
    constant across guesses; the guarantee that matters — resisting
    byte-by-byte key discovery — comes from compare_digest itself, not from
    loop-level uniformity.
    """
    token_bytes = token.encode()
    return any(hmac.compare_digest(token_bytes, k.encode()) for k in keys)


def _authenticate(token: str | None, keys: frozenset[str]) -> None:
    """Raise AuthenticationError if the token is absent or does not match any key.

    Pure function — no HTTP concerns. Callers that embed this logic directly
    (e.g. tests, WebSocket handlers) can catch AuthenticationError without
    inspecting HTTP response status codes.
    """
    if token is None or not _is_valid(token, keys):
        raise AuthenticationError("missing or invalid API key")


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates API key tokens on every request.

    Accepts a KeyStore for hot-reloadable keys and a RateLimiter that caps
    the number of failed attempts per IP per time window.

    When the KeyStore is empty the middleware passes all requests through
    without validation (dev mode). Auth is enabled as soon as the store
    contains at least one key — no restart needed after a SIGHUP reload.
    """

    def __init__(
        self,
        app,
        *,
        key_store: KeyStore,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(app)
        self._store = key_store
        self._limiter = rate_limiter or RateLimiter()

    async def dispatch(self, request: Request, call_next):
        """Validate the bearer token; apply rate limiting on failures."""
        keys = self._store.get()
        if not keys:
            return await call_next(request)

        path = request.url.path
        if path in _UNAUTHENTICATED_PATHS or any(
            path.startswith(p) for p in _UNAUTHENTICATED_PREFIXES
        ):
            return await call_next(request)

        try:
            _authenticate(_extract_token(request), keys)
        except AuthenticationError:
            ip = request.client.host if request.client else "unknown"
            if not self._limiter.is_allowed(ip):
                logger.warning("Rate limit exceeded: %s", ip)
                return _TOO_MANY_REQUESTS
            logger.warning(
                "Rejected unauthenticated request: %s %s from %s",
                request.method,
                request.url.path,
                ip,
            )
            return _build_unauthorized(request)

        return await call_next(request)
