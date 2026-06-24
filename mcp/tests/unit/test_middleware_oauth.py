# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Unit tests for middleware/oauth.py — OAuthDiscoveryMiddleware.

Uses a minimal Starlette app wrapped with the middleware and the synchronous
TestClient, which avoids spinning up a real server.
"""

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.oauth import OAuthDiscoveryMiddleware, _PROTECTED_RESOURCE_PATHS


def _make_client() -> TestClient:
    """Build a TestClient with OAuthDiscoveryMiddleware and a catch-all route."""

    def fallthrough(request):
        return PlainTextResponse("inner")

    app = Starlette(
        routes=[Route("/{path:path}", fallthrough)],
        middleware=[Middleware(OAuthDiscoveryMiddleware)],
    )
    return TestClient(app, raise_server_exceptions=True)


# ── Discovery paths ───────────────────────────────────────────────────────────


def test_oauth_discovery_returns_200_for_well_known():
    """GET /.well-known/oauth-protected-resource returns HTTP 200."""
    resp = _make_client().get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200


def test_oauth_discovery_returns_200_for_mcp_subpath():
    """GET /.well-known/oauth-protected-resource/mcp returns HTTP 200."""
    resp = _make_client().get("/.well-known/oauth-protected-resource/mcp")
    assert resp.status_code == 200


def test_oauth_discovery_body_has_resource_field():
    """Response JSON includes 'resource' pointing to /mcp."""
    resp = _make_client().get("/.well-known/oauth-protected-resource")
    data = resp.json()
    assert "resource" in data
    assert data["resource"].endswith("/mcp")


def test_oauth_discovery_bearer_methods():
    """bearer_methods_supported is ['header']."""
    resp = _make_client().get("/.well-known/oauth-protected-resource")
    assert resp.json()["bearer_methods_supported"] == ["header"]


def test_oauth_discovery_has_documentation_link():
    """resource_documentation field points to the MCP spec."""
    resp = _make_client().get("/.well-known/oauth-protected-resource")
    assert "modelcontextprotocol.io" in resp.json()["resource_documentation"]


def test_oauth_discovery_cache_control_no_store():
    """Cache-Control: no-store is set so clients never cache credentials."""
    resp = _make_client().get("/.well-known/oauth-protected-resource")
    assert resp.headers.get("cache-control") == "no-store"


def test_oauth_discovery_mcp_subpath_same_body():
    """Both discovery paths return the same 'resource' value."""
    client = _make_client()
    r1 = client.get("/.well-known/oauth-protected-resource").json()
    r2 = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert r1["resource"] == r2["resource"]


# ── Pass-through ──────────────────────────────────────────────────────────────


def test_oauth_non_discovery_path_passes_through():
    """Other paths are forwarded to the inner application."""
    resp = _make_client().get("/mcp")
    assert resp.text == "inner"


def test_oauth_root_path_passes_through():
    """The root path is forwarded (not intercepted)."""
    resp = _make_client().get("/")
    assert resp.text == "inner"


# ── Constants ─────────────────────────────────────────────────────────────────


def test_protected_paths_set_is_complete():
    """Both RFC 9728 variants are present in the constant set."""
    assert "/.well-known/oauth-protected-resource" in _PROTECTED_RESOURCE_PATHS
    assert "/.well-known/oauth-protected-resource/mcp" in _PROTECTED_RESOURCE_PATHS


def test_protected_paths_set_has_no_extra_entries():
    """The set should contain exactly two paths to minimise the interception surface."""
    assert len(_PROTECTED_RESOURCE_PATHS) == 2
