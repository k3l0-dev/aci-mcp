# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for middleware.auth — ApiKeyMiddleware, KeyStore, RateLimiter, helpers."""

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.auth import (
    ApiKeyMiddleware,
    KeyStore,
    RateLimiter,
    _extract_token,
    _is_valid,
    load_api_keys,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _app(api_keys: frozenset[str], rate_limiter: RateLimiter | None = None) -> Starlette:
    """Build a minimal Starlette app with the auth middleware for testing."""
    app = Starlette(routes=[
        Route("/mcp", _echo),
        Route("/.well-known/oauth-protected-resource", _echo),
        Route("/.well-known/oauth-authorization-server", _echo),
        Route("/.well-known/openid-configuration", _echo),
        Route("/register", _echo, methods=["POST"]),
    ])
    app.add_middleware(
        ApiKeyMiddleware,
        key_store=KeyStore(api_keys),
        rate_limiter=rate_limiter,
    )
    return app


# ── load_api_keys ─────────────────────────────────────────────────────────────


def test_load_api_keys_returns_empty_when_unset(monkeypatch):
    monkeypatch.delenv("MCP_API_KEYS", raising=False)
    assert load_api_keys() == frozenset()


def test_load_api_keys_single_key(monkeypatch):
    monkeypatch.setenv("MCP_API_KEYS", "abc123")
    assert load_api_keys() == frozenset({"abc123"})


def test_load_api_keys_multiple_keys(monkeypatch):
    monkeypatch.setenv("MCP_API_KEYS", "key1,key2,key3")
    assert load_api_keys() == frozenset({"key1", "key2", "key3"})


def test_load_api_keys_strips_whitespace(monkeypatch):
    monkeypatch.setenv("MCP_API_KEYS", " key1 , key2 ")
    assert load_api_keys() == frozenset({"key1", "key2"})


def test_load_api_keys_ignores_empty_segments(monkeypatch):
    monkeypatch.setenv("MCP_API_KEYS", "key1,,key2,")
    assert load_api_keys() == frozenset({"key1", "key2"})


# ── KeyStore ──────────────────────────────────────────────────────────────────


def test_key_store_get_returns_initial_keys():
    ks = KeyStore(frozenset({"a", "b"}))
    assert ks.get() == frozenset({"a", "b"})


def test_key_store_reload_replaces_keys():
    ks = KeyStore(frozenset({"old"}))
    ks.reload(frozenset({"new1", "new2"}))
    assert ks.get() == frozenset({"new1", "new2"})


def test_key_store_bool_false_when_empty():
    assert not KeyStore(frozenset())


def test_key_store_bool_true_when_populated():
    assert KeyStore(frozenset({"k"}))


def test_key_store_len():
    ks = KeyStore(frozenset({"a", "b", "c"}))
    assert len(ks) == 3


def test_key_store_reload_updates_len():
    ks = KeyStore(frozenset({"a"}))
    ks.reload(frozenset({"x", "y"}))
    assert len(ks) == 2


# ── RateLimiter ───────────────────────────────────────────────────────────────


def test_rate_limiter_allows_within_limit():
    limiter = RateLimiter(max_attempts=3, window_s=60)
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True
    assert limiter.is_allowed("1.2.3.4") is True


def test_rate_limiter_blocks_after_threshold():
    limiter = RateLimiter(max_attempts=3, window_s=60)
    limiter.is_allowed("1.2.3.4")
    limiter.is_allowed("1.2.3.4")
    limiter.is_allowed("1.2.3.4")
    assert limiter.is_allowed("1.2.3.4") is False


def test_rate_limiter_different_ips_are_independent():
    limiter = RateLimiter(max_attempts=2, window_s=60)
    limiter.is_allowed("1.1.1.1")
    limiter.is_allowed("1.1.1.1")
    assert limiter.is_allowed("1.1.1.1") is False
    assert limiter.is_allowed("2.2.2.2") is True


def test_rate_limiter_window_evicts_old_entries():
    """Entries outside the window are evicted and no longer count toward the limit."""
    import time

    limiter = RateLimiter(max_attempts=2, window_s=1)
    limiter.is_allowed("ip")
    limiter.is_allowed("ip")
    assert limiter.is_allowed("ip") is False
    time.sleep(1.1)
    # Window has rolled over — IP should be allowed again
    assert limiter.is_allowed("ip") is True


# ── _extract_token ────────────────────────────────────────────────────────────


def test_extract_token_from_bearer_header():
    captured = {}

    async def handler(request: Request) -> JSONResponse:
        captured["token"] = _extract_token(request)
        return JSONResponse({})

    app = Starlette(routes=[Route("/", handler)])
    client = TestClient(app, raise_server_exceptions=True)
    client.get("/", headers={"Authorization": "Bearer mytoken"})
    assert captured["token"] == "mytoken"


def test_extract_token_from_x_api_key_header():
    captured = {}

    async def handler(request: Request) -> JSONResponse:
        captured["token"] = _extract_token(request)
        return JSONResponse({})

    app = Starlette(routes=[Route("/", handler)])
    client = TestClient(app, raise_server_exceptions=True)
    client.get("/", headers={"X-API-Key": "mytoken"})
    assert captured["token"] == "mytoken"


def test_extract_token_returns_none_when_no_headers():
    captured = {}

    async def handler(request: Request) -> JSONResponse:
        captured["token"] = _extract_token(request)
        return JSONResponse({})

    app = Starlette(routes=[Route("/", handler)])
    client = TestClient(app, raise_server_exceptions=True)
    client.get("/")
    assert captured["token"] is None


def test_extract_token_bearer_prefix_only_returns_empty_string():
    """'Authorization: Bearer ' (trailing space, no token) extracts ''."""
    captured = {}

    async def handler(request: Request) -> JSONResponse:
        captured["token"] = _extract_token(request)
        return JSONResponse({})

    app = Starlette(routes=[Route("/", handler)])
    client = TestClient(app, raise_server_exceptions=True)
    client.get("/", headers={"Authorization": "Bearer "})
    assert captured["token"] == ""


# ── _is_valid ─────────────────────────────────────────────────────────────────


def test_is_valid_correct_key():
    assert _is_valid("secret", frozenset({"secret"})) is True


def test_is_valid_wrong_key():
    assert _is_valid("wrong", frozenset({"secret"})) is False


def test_is_valid_one_of_many():
    assert _is_valid("key2", frozenset({"key1", "key2", "key3"})) is True


def test_is_valid_empty_keys_always_false():
    assert _is_valid("any", frozenset()) is False


# ── ApiKeyMiddleware — no-op mode (empty KeyStore) ────────────────────────────


def test_no_auth_when_keys_empty():
    client = TestClient(_app(frozenset()))
    resp = client.get("/mcp")
    assert resp.status_code == 200


# ── ApiKeyMiddleware — enforcement mode ───────────────────────────────────────


KEYS = frozenset({"valid-key-1", "valid-key-2"})


def test_valid_bearer_token_passes():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"Authorization": "Bearer valid-key-1"})
    assert resp.status_code == 200


def test_valid_x_api_key_passes():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"X-API-Key": "valid-key-2"})
    assert resp.status_code == 200


def test_second_key_also_valid():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"Authorization": "Bearer valid-key-2"})
    assert resp.status_code == 200


def test_missing_header_returns_401():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp")
    assert resp.status_code == 401


def test_wrong_token_returns_401():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401


def test_empty_bearer_returns_401():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"Authorization": "Bearer "})
    assert resp.status_code == 401


def test_401_response_has_www_authenticate_header():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp")
    assert "WWW-Authenticate" in resp.headers
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_401_www_authenticate_includes_resource_metadata():
    """WWW-Authenticate must include resource_metadata URL per RFC 9728."""
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp")
    www_auth = resp.headers.get("WWW-Authenticate", "")
    assert 'resource_metadata="' in www_auth
    assert "/.well-known/oauth-protected-resource" in www_auth


def test_401_response_body_is_json():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp")
    body = resp.json()
    assert "error" in body
    assert "detail" in body


def test_case_sensitive_token():
    """Token comparison must be exact — case matters."""
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"Authorization": "Bearer VALID-KEY-1"})
    assert resp.status_code == 401


def test_partial_token_not_accepted():
    client = TestClient(_app(KEYS))
    resp = client.get("/mcp", headers={"Authorization": "Bearer valid-key"})
    assert resp.status_code == 401


def test_bearer_header_takes_precedence_over_x_api_key():
    """When both headers present, Bearer token is used (and must be valid)."""
    client = TestClient(_app(KEYS))
    resp = client.get(
        "/mcp",
        headers={"Authorization": "Bearer valid-key-1", "X-API-Key": "wrong"},
    )
    assert resp.status_code == 200


def test_invalid_bearer_with_valid_x_api_key_returns_401():
    """If Authorization header is present but invalid, X-API-Key is not fallback."""
    client = TestClient(_app(KEYS))
    resp = client.get(
        "/mcp",
        headers={"Authorization": "Bearer wrong", "X-API-Key": "valid-key-1"},
    )
    assert resp.status_code == 401


# ── ApiKeyMiddleware — rate limiting ──────────────────────────────────────────


def test_rate_limit_returns_429_after_threshold():
    """After max_attempts failures from the same IP, respond with 429."""
    limiter = RateLimiter(max_attempts=2, window_s=60)
    client = TestClient(_app(KEYS, rate_limiter=limiter))
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp").status_code == 429


def test_rate_limit_429_has_retry_after_header():
    limiter = RateLimiter(max_attempts=1, window_s=60)
    client = TestClient(_app(KEYS, rate_limiter=limiter))
    client.get("/mcp")
    resp = client.get("/mcp")
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_rate_limit_does_not_affect_valid_requests():
    """Successful auth does not consume rate-limit budget."""
    limiter = RateLimiter(max_attempts=2, window_s=60)
    client = TestClient(_app(KEYS, rate_limiter=limiter))
    # Two valid requests — should never hit the limiter
    assert client.get("/mcp", headers={"Authorization": "Bearer valid-key-1"}).status_code == 200
    assert client.get("/mcp", headers={"Authorization": "Bearer valid-key-1"}).status_code == 200
    assert client.get("/mcp", headers={"Authorization": "Bearer valid-key-1"}).status_code == 200


# ── ApiKeyMiddleware — KeyStore hot-reload ────────────────────────────────────


def test_key_store_reload_takes_effect_on_next_request():
    """After reload(), new keys are accepted and old keys are rejected."""
    ks = KeyStore(frozenset({"old-key"}))
    app = Starlette(routes=[Route("/mcp", _echo)])
    app.add_middleware(ApiKeyMiddleware, key_store=ks)
    client = TestClient(app)

    assert client.get("/mcp", headers={"Authorization": "Bearer old-key"}).status_code == 200
    assert client.get("/mcp", headers={"Authorization": "Bearer new-key"}).status_code == 401

    ks.reload(frozenset({"new-key"}))

    assert client.get("/mcp", headers={"Authorization": "Bearer new-key"}).status_code == 200
    assert client.get("/mcp", headers={"Authorization": "Bearer old-key"}).status_code == 401


# ── ApiKeyMiddleware — unauthenticated paths (OAuth discovery) ────────────────


def test_well_known_oauth_protected_resource_bypasses_auth():
    """MCP OAuth discovery endpoint must be accessible without a token."""
    client = TestClient(_app(KEYS))
    resp = client.get("/.well-known/oauth-protected-resource")
    assert resp.status_code == 200


def test_well_known_oauth_authorization_server_bypasses_auth():
    client = TestClient(_app(KEYS))
    resp = client.get("/.well-known/oauth-authorization-server")
    assert resp.status_code == 200


def test_well_known_openid_configuration_bypasses_auth():
    client = TestClient(_app(KEYS))
    resp = client.get("/.well-known/openid-configuration")
    assert resp.status_code == 200


def test_register_endpoint_bypasses_auth():
    """Dynamic client registration must be accessible without a token."""
    client = TestClient(_app(KEYS))
    resp = client.post("/register")
    assert resp.status_code == 200


def test_well_known_subpath_bypasses_auth():
    """Any /.well-known/* subpath is exempt, not just specific known ones."""
    client = TestClient(_app(KEYS))
    # 404 from the router is acceptable — middleware did not block it
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code != 401


def test_protected_path_still_requires_auth_after_well_known_bypass():
    """Bypassing /.well-known/ must not affect protection of /mcp."""
    client = TestClient(_app(KEYS))
    assert client.get("/mcp").status_code == 401
