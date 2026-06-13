# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
"""
middleware/auth.py

API-key authentication middleware for the MCP HTTP server.

Validates every incoming request against a set of pre-shared bearer tokens
loaded from MCP_API_KEYS (comma-separated list in .env or environment).

Accepted header forms:
  Authorization: Bearer <token>
  X-API-Key: <token>

When MCP_API_KEYS is empty or unset the middleware is a no-op and a startup
warning is emitted by the caller.  This allows unauthenticated local dev
while making production misconfiguration visible.

Timing safety: all comparisons use hmac.compare_digest to prevent
timing-oracle attacks on token values.
"""

import hmac
import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("aci-mcp.auth")

_BEARER_PREFIX = "Bearer "
_UNAUTHORIZED = JSONResponse(
    {"error": "Unauthorized", "detail": "A valid API key is required."},
    status_code=401,
    headers={"WWW-Authenticate": "Bearer"},
)


def load_api_keys() -> frozenset[str]:
    """Read MCP_API_KEYS from the environment and return a frozenset of valid tokens."""
    raw = os.environ.get("MCP_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX):]
    return request.headers.get("X-API-Key") or None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that validates API key tokens on every request.

    Pass api_keys as a frozenset of valid tokens.  An empty frozenset
    disables authentication (no-op mode for local development).
    """

    def __init__(self, app, *, api_keys: frozenset[str]) -> None:
        super().__init__(app)
        self._keys = api_keys

    async def dispatch(self, request: Request, call_next):
        if not self._keys:
            return await call_next(request)

        token = _extract_token(request)
        if token is None or not _is_valid(token, self._keys):
            logger.warning(
                "Rejected unauthenticated request: %s %s from %s",
                request.method,
                request.url.path,
                request.client.host if request.client else "unknown",
            )
            return _UNAUTHORIZED

        return await call_next(request)


def _is_valid(token: str, keys: frozenset[str]) -> bool:
    """Constant-time membership test across all keys."""
    token_bytes = token.encode()
    return any(hmac.compare_digest(token_bytes, k.encode()) for k in keys)
