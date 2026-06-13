# Copyright (c) 2026 Khalid El-Ouiali — Monark AIOPS SRL. All rights reserved.
"""
middleware/oauth.py

OAuth 2.0 Protected Resource Metadata middleware (RFC 9728) for the MCP HTTP server.

MCP 2025-03-26 spec-compliant clients (e.g. OpenCode, Claude Desktop) probe
/.well-known/oauth-protected-resource before attempting authentication. Without
a valid JSON response here the client fails with a JSON parse error on the "Not
Found" HTML body returned by FastMCP.

This server uses pre-shared Bearer tokens — there is no OAuth authorization
server. The metadata response tells clients:
  - Which resource URL is protected (/mcp)
  - That Bearer tokens via Authorization header are accepted
  - That no OAuth authorization flow exists (no 'authorization_servers' field)

A client that reads this metadata should prompt the user for a token rather
than attempting an OAuth redirect flow.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

# Paths defined by RFC 9728 and the MCP 2025-03-26 spec.
# Both the generic and the MCP-specific variant are handled.
_PROTECTED_RESOURCE_PATHS = frozenset({
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
})


class OAuthDiscoveryMiddleware(BaseHTTPMiddleware):
    """Intercept OAuth discovery requests and return RFC 9728 Protected Resource Metadata.

    Must be placed BEFORE ApiKeyMiddleware in the middleware stack so it
    intercepts discovery paths before any token validation occurs.

    All non-discovery paths pass through to the next middleware unchanged.
    """

    async def dispatch(self, request: Request, call_next):
        """Return RFC 9728 JSON for discovery paths; pass everything else through."""
        if request.url.path in _PROTECTED_RESOURCE_PATHS:
            base = str(request.base_url).rstrip("/")
            return JSONResponse(
                {
                    "resource": f"{base}/mcp",
                    "bearer_methods_supported": ["header"],
                    "resource_documentation": (
                        "https://modelcontextprotocol.io/specification/"
                        "2025-03-26/basic/authentication"
                    ),
                },
                headers={"Cache-Control": "no-store"},
            )
        return await call_next(request)
