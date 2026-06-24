# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for middleware/health.py — HealthMiddleware ASGI interface.

Tests drive the middleware directly via raw ASGI scope/receive/send, without
spinning up a full Starlette or httpx stack.  This exercises the response
path (lines 35-39) and the pass-through path.
"""

import json

import pytest
from middleware.health import HealthMiddleware, _HEALTH_BODY, _HEALTH_HEADERS


async def _call(mw: HealthMiddleware, scope: dict) -> list[dict]:
    """Invoke middleware and collect every ASGI send message in a list."""
    sent: list[dict] = []

    async def mock_send(message: dict) -> None:
        sent.append(message)

    await mw(scope, None, mock_send)
    return sent


# ── /health path ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_returns_200():
    """GET /health returns HTTP 200."""
    mw = HealthMiddleware(lambda *_: None)
    sent = await _call(mw, {"type": "http", "path": "/health"})
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_health_body_is_status_ok():
    """GET /health body is {"status":"ok"}."""
    mw = HealthMiddleware(lambda *_: None)
    sent = await _call(mw, {"type": "http", "path": "/health"})
    body = json.loads(sent[1]["body"])
    assert body == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_sends_response_start_then_body():
    """Two ASGI messages in the correct order: response.start then response.body."""
    mw = HealthMiddleware(lambda *_: None)
    sent = await _call(mw, {"type": "http", "path": "/health"})
    assert len(sent) == 2
    assert sent[0]["type"] == "http.response.start"
    assert sent[1]["type"] == "http.response.body"


@pytest.mark.asyncio
async def test_health_response_has_json_content_type():
    """GET /health sets Content-Type: application/json."""
    mw = HealthMiddleware(lambda *_: None)
    sent = await _call(mw, {"type": "http", "path": "/health"})
    headers = dict(sent[0]["headers"])
    assert headers[b"content-type"] == b"application/json"


@pytest.mark.asyncio
async def test_health_inner_app_not_called():
    """GET /health is short-circuited — the inner app is never invoked."""
    called: list[bool] = []

    async def inner(scope, receive, send) -> None:
        called.append(True)

    mw = HealthMiddleware(inner)
    await _call(mw, {"type": "http", "path": "/health"})
    assert not called


# ── pass-through ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_health_path_forwarded():
    """Non-/health HTTP paths are forwarded to the inner app."""
    forwarded: list[str] = []

    async def inner(scope, receive, send) -> None:
        forwarded.append(scope["path"])

    mw = HealthMiddleware(inner)
    await mw({"type": "http", "path": "/mcp"}, None, None)
    assert forwarded == ["/mcp"]


@pytest.mark.asyncio
async def test_websocket_scope_forwarded():
    """WebSocket scopes are forwarded even if the path is /health."""
    forwarded: list[str] = []

    async def inner(scope, receive, send) -> None:
        forwarded.append(scope["type"])

    mw = HealthMiddleware(inner)
    await mw({"type": "websocket", "path": "/health"}, None, None)
    assert forwarded == ["websocket"]


@pytest.mark.asyncio
async def test_lifespan_scope_forwarded():
    """Lifespan scopes are forwarded to the inner app."""
    forwarded: list[str] = []

    async def inner(scope, receive, send) -> None:
        forwarded.append(scope["type"])

    mw = HealthMiddleware(inner)
    await mw({"type": "lifespan"}, None, None)
    assert forwarded == ["lifespan"]


# ── constants ─────────────────────────────────────────────────────────────────


def test_health_body_constant_is_valid_json():
    """_HEALTH_BODY constant parses to {"status":"ok"}."""
    assert json.loads(_HEALTH_BODY) == {"status": "ok"}


def test_health_headers_constant_has_correct_content_length():
    """Content-Length header matches actual body length."""
    headers = dict(_HEALTH_HEADERS)
    assert int(headers[b"content-length"]) == len(_HEALTH_BODY)
