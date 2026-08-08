# Internals: Auth Middleware

`mcp/src/niwashi_mcp/middleware/auth.py` — API key authentication for every incoming HTTP request.

For a broader view of the full middleware stack (HealthMiddleware, OAuthDiscoveryMiddleware, and how they compose), see [middleware.md](middleware.md).

This layer is unchanged in 2.0: it authenticates HTTP requests and knows nothing about the ACI object model or where it comes from.

---

## Overview

`ApiKeyMiddleware` is a Starlette `BaseHTTPMiddleware` that validates bearer tokens. It is the innermost layer of the middleware stack, applied after health and discovery middleware.

Key components in `auth.py`:

| Symbol | Role |
|---|---|
| `KeyStore` | Thread-safe, hot-reloadable container for the set of valid API keys |
| `RateLimiter` | Fixed-window per-IP counter that returns 429 after threshold |
| `_authenticate(token, keys)` | Pure auth function — raises `AuthenticationError` (no HTTP concerns) |
| `_extract_token(request)` | Reads bearer token from headers |
| `_is_valid(token, keys)` | Constant-time multi-key comparison via `hmac.compare_digest` |
| `load_api_keys()` | Reads `MCP_API_KEYS` from environment |

---

## Token extraction

Two accepted header forms, checked in priority order:

| Header | Format | Priority |
|---|---|---|
| `Authorization` | `Bearer <token>` | First |
| `X-API-Key` | `<token>` | Fallback, whenever `Authorization` doesn't start with `Bearer` (with the trailing space that separates it from the token) — that includes it being absent, empty, or using a different scheme (e.g. `Basic ...`) |

When `Authorization: Bearer` is present but the token is invalid, `X-API-Key` is **not** consulted — the `Bearer` prefix and its separating space are what decide which header is read, independent of whether the extracted token then turns out to be valid.

---

## Authentication logic

The auth check lives in `_authenticate()` — a pure function with no HTTP concerns:

```python
def _authenticate(token: str | None, keys: frozenset[str]) -> None:
    if token is None or not _is_valid(token, keys):
        raise AuthenticationError("missing or invalid API key")
```

`dispatch()` calls this inside `try/except AuthenticationError` and converts the exception to the HTTP response:

```python
try:
    _authenticate(_extract_token(request), keys)
except AuthenticationError:
    ip = request.client.host if request.client else "unknown"
    if not self._limiter.is_allowed(ip):
        logger.warning("Rate limit exceeded: %s", ip)
        return _TOO_MANY_REQUESTS
    logger.warning("Rejected unauthenticated request: %s %s from %s", ...)
    return _build_unauthorized(request)
return await call_next(request)
```

---

## Timing-safe comparison

```python
def _is_valid(token: str, keys: frozenset[str]) -> bool:
    token_bytes = token.encode()
    return any(hmac.compare_digest(token_bytes, k.encode()) for k in keys)
```

`hmac.compare_digest` runs in constant time relative to the compared values' length — no single comparison leaks a partial byte-wise match via timing. This is what actually defeats byte-by-byte key discovery.

**Correction:** the loop itself is not position-independent. `any()` short-circuits on the first match, so a valid token matching an early key (in `frozenset` iteration order) returns faster than one matching a late key. For an *invalid* token — the brute-force-guessing case this guards against — every key is compared every time, so timing is constant across guesses. The position-dependent timing only shows up once a token is already valid, at which point an attacker gains little from it: they already hold a working key.

---

## 401 response

```json
{"error": "Unauthorized", "detail": "A valid API key is required."}
```

```http
HTTP/1.1 401 Unauthorized
Content-Type: application/json
WWW-Authenticate: Bearer resource_metadata="https://mcp.yourdomain.com/.well-known/oauth-protected-resource"
```

The `WWW-Authenticate` header includes a `resource_metadata` URL per [RFC 9728 §5.1](https://www.rfc-editor.org/rfc/rfc9728#section-5.1). MCP-compliant clients use this URL to find the OAuth discovery document without guessing paths.

---

## Rate limiting

`RateLimiter` uses a fixed-window per-IP strategy. Defaults: 30 failed attempts per 60-second window.

- Only failed auth attempts consume budget — successful requests do not
- A blocked IP receives `429 Too Many Requests` with `Retry-After: 60`
- Different IPs are tracked independently
- The window rolls over automatically (old entries evicted via `time.monotonic`)

---

## KeyStore

```python
class KeyStore:
    def get(self) -> frozenset[str]: ...      # atomic snapshot read
    def reload(self, new_keys: frozenset[str]) -> None: ...  # atomic replacement
    def __bool__(self) -> bool: ...           # True when keys present
    def __len__(self) -> int: ...
```

When `KeyStore` is empty, the middleware is a no-op — all requests pass through. This is dev mode. Auth is enabled as soon as the store contains at least one key.

`reload()` replaces the key set atomically under a `threading.Lock`. In-flight requests that already called `get()` continue with their snapshot uninterrupted. The only caller is the `SIGHUP` handler registered in `_serve()` — see [middleware.md](middleware.md#sighup-hot-reload) for the rotation procedure.

---

## MCP_API_KEYS format

```python
def load_api_keys() -> frozenset[str]:
    raw = os.environ.get("MCP_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())
```

| `MCP_API_KEYS` value | Result |
|---|---|
| Not set / empty | `frozenset()` — auth disabled |
| `"token1"` | One valid token |
| `"token1,token2"` | Two independent tokens |
| `" token1 , token2 "` | Whitespace stripped |
| `"token1,,token2,"` | Empty segments ignored |

Comparison is always case-sensitive.

`load_api_keys()` reads the process environment, nothing else. The `.env` file is loaded into that environment by `_serve()` before the `KeyStore` is built, and again on every `SIGHUP` — so the file's location is what decides which keys the server ends up with. `main.py` resolves it once, at import:

| Order | Candidate |
|---|---|
| 1 | `$NIWASHI_MCP_ENV_FILE`, used verbatim when set |
| 2 | `./.env` in the current working directory |
| 3 | `<repo>/.env`, only when the process is running from a verified git checkout |
| 4 | `~/.config/niwashi-mcp/.env` |

`NIWASHI_MCP_ENV_FILE` wins outright when set, whether or not the file it names exists; otherwise the first of the remaining candidates that exists is used, and `./.env` is the fallback when none does. Since 2.0 the server is normally installed rather than run from a checkout, so candidates 1 and 4 are the ones that matter in production — a missing `.env` is not an error, and an unset `MCP_API_KEYS` disables authentication silently apart from the startup warning below. See [configuration/settings.md](../configuration/settings.md) for the full variable list.

---

## Unauthenticated paths

These paths bypass token validation entirely:

| Pattern | Why |
|---|---|
| `/.well-known/*` (prefix) | OAuth discovery — must be accessible before a token exists |
| `/register` (exact) | Dynamic client registration (RFC 7591) |

`/health` is handled by `HealthMiddleware` before this layer runs — it never reaches `ApiKeyMiddleware`.

---

## No-op mode (development)

When `MCP_API_KEYS` is unset or empty, the middleware passes all requests through without validation. A warning is logged at startup:

```text
WARNING  niwashi-mcp  MCP_API_KEYS is not set — server is running WITHOUT authentication.
         Set MCP_API_KEYS in .env before deploying to production.
```
