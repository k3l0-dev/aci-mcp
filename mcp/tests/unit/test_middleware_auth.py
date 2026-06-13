"""Unit tests for middleware.auth.ApiKeyMiddleware and helpers."""


from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware.auth import ApiKeyMiddleware, _extract_token, _is_valid, load_api_keys


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _echo(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _app(api_keys: frozenset[str]) -> Starlette:
    app = Starlette(routes=[Route("/mcp", _echo)])
    app.add_middleware(ApiKeyMiddleware, api_keys=api_keys)
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


# ── _extract_token ────────────────────────────────────────────────────────────


def test_extract_token_from_bearer_header():
    from starlette.testclient import TestClient

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
    """'Authorization: Bearer ' (with trailing space but no token) extracts ''."""
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


# ── ApiKeyMiddleware — no-op mode (empty keys) ────────────────────────────────


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
    assert resp.headers["WWW-Authenticate"] == "Bearer"


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
