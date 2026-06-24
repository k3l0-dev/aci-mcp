# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
middleware/health.py

Lightweight ASGI middleware that serves GET /health without authentication.
Must be placed as the outermost middleware so Docker and load-balancer
health checks are never blocked by auth or discovery middleware.
"""

import json

from starlette.types import ASGIApp, Receive, Scope, Send

_HEALTH_BODY = json.dumps({"status": "ok"}).encode()
_HEALTH_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_HEALTH_BODY)).encode()),
]


class HealthMiddleware:
    """ASGI middleware — responds to GET /health with 200 {status: ok}.

    Intercepts /health before any authentication or discovery middleware
    so container orchestrators and reverse proxies can probe liveness
    without a bearer token.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle /health directly; forward all other requests downstream."""
        if scope["type"] == "http" and scope.get("path") == "/health":
            await send({"type": "http.response.start", "status": 200, "headers": _HEALTH_HEADERS})
            await send({"type": "http.response.body", "body": _HEALTH_BODY})
            return
        await self.app(scope, receive, send)
