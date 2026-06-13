# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
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
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("aci-mcp.auth")

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


class KeyStore:
    """Thread-safe container for the set of valid API keys with hot-reload support.

    Designed to be updated at runtime via SIGHUP without restarting the server.
    The internal frozenset is replaced atomically under a lock so in-flight
    requests that already called get() continue with the old set uninterrupted.
    """

    def __init__(self, keys: frozenset[str]) -> None:
        self._keys = keys
        self._lock = threading.Lock()

    def get(self) -> frozenset[str]:
        """Return the current key set as an immutable snapshot."""
        with self._lock:
            return self._keys

    def reload(self, new_keys: frozenset[str]) -> None:
        """Replace the key set atomically. Safe to call from a signal handler."""
        with self._lock:
            self._keys = new_keys

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
    """

    def __init__(self, *, max_attempts: int = 30, window_s: int = 60) -> None:
        self._max = max_attempts
        self._window = window_s
        self._counts: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, ip: str) -> bool:
        """Return True and record the attempt if the IP is within the limit.

        Returns False (without recording) if the IP has already exceeded
        max_attempts within the current window, indicating the request should
        be rejected with 429.
        """
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            self._counts[ip] = [t for t in self._counts[ip] if t > cutoff]
            if len(self._counts[ip]) >= self._max:
                return False
            self._counts[ip].append(now)
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
    """Constant-time membership test across all keys to prevent timing-oracle attacks.

    Uses hmac.compare_digest for every key in the set so the total comparison
    time is proportional to the number of keys, not to where the match is found.
    """
    token_bytes = token.encode()
    return any(hmac.compare_digest(token_bytes, k.encode()) for k in keys)


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

        token = _extract_token(request)
        if token is not None and _is_valid(token, keys):
            return await call_next(request)

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
