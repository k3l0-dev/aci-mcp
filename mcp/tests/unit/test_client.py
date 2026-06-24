# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for apic/client.py — ApicClient with a simulated httpx transport.

All tests use FakeHTTPClient to avoid any network calls.  Each test controls
exactly which responses (or exceptions) the fake transport returns, so we can
exercise every branch: happy path, re-auth, timeouts, malformed JSON, etc.
"""

from unittest.mock import MagicMock

import httpx
import pytest
from apic.client import ApicClient
from exceptions import ApicAuthError, ApicConnectionError, ApicResponseError
from tests.conftest import apic_login_response, apic_response, make_imdata_objects


# ── Fake HTTP transport ───────────────────────────────────────────────────────


class _MockResponse:
    """Minimal httpx.Response stand-in."""

    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self.is_success = 200 <= status_code < 300
        self._body = body

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://test/"),
                response=MagicMock(status_code=self.status_code),
            )


class FakeHTTPClient:
    """Queue-based httpx.AsyncClient replacement.

    Each call to post() or get() pops the next item from `_queue`.
    Items can be _MockResponse instances (success) or Exception subclasses
    (raised directly to simulate network errors).

    Exposes `requests` for asserting what URLs / params were called.
    """

    def __init__(self, *responses):
        self._queue = list(responses)
        self.requests: list[dict] = []
        self.cookies = httpx.Cookies()
        self.timeout = 30.0

    def _next(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def post(self, url: str, **kwargs):
        return self._next("POST", url, **kwargs)

    async def get(self, url: str, **kwargs):
        return self._next("GET", url, **kwargs)

    async def aclose(self) -> None:
        pass


def _make_client(*responses) -> ApicClient:
    """Build an ApicClient wired to a FakeHTTPClient."""
    client = ApicClient("10.0.0.1", "admin", "secret")
    client._client = FakeHTTPClient(*responses)
    return client


# ── authenticate() ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_authenticate_success_stores_cookie():
    client = _make_client(_MockResponse(200, apic_login_response("tok-xyz")))
    await client.authenticate()
    assert client._client.cookies.get("APIC-cookie") == "tok-xyz"


@pytest.mark.asyncio
async def test_authenticate_401_raises_apic_auth_error():
    client = _make_client(_MockResponse(401, {}))
    with pytest.raises(ApicAuthError) as exc_info:
        await client.authenticate()
    assert exc_info.value.host == "10.0.0.1"
    assert exc_info.value.status == 401


@pytest.mark.asyncio
async def test_authenticate_403_raises_apic_auth_error():
    client = _make_client(_MockResponse(403, {}))
    with pytest.raises(ApicAuthError) as exc_info:
        await client.authenticate()
    assert exc_info.value.status == 403


@pytest.mark.asyncio
async def test_authenticate_timeout_raises_apic_connection_error():
    client = _make_client(httpx.TimeoutException("timed out"))
    with pytest.raises(ApicConnectionError) as exc_info:
        await client.authenticate()
    assert exc_info.value.host == "10.0.0.1"
    assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_authenticate_connect_error_raises_apic_connection_error():
    client = _make_client(httpx.ConnectError("connection refused"))
    with pytest.raises(ApicConnectionError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_malformed_json_raises_apic_response_error():
    """APIC returns 200 but imdata token path is wrong."""
    client = _make_client(_MockResponse(200, {"imdata": [{"unexpected": {}}]}))
    with pytest.raises(ApicResponseError):
        await client.authenticate()


@pytest.mark.asyncio
async def test_authenticate_empty_imdata_raises_apic_response_error():
    client = _make_client(_MockResponse(200, {"imdata": []}))
    with pytest.raises(ApicResponseError):
        await client.authenticate()


# ── query_class() — happy path ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_returns_parsed_objects():
    objects = make_imdata_objects(
        "fvBD",
        [
            {"dn": "uni/tn-OT/BD-servers", "name": "servers"},
            {"dn": "uni/tn-OT/BD-clients", "name": "clients"},
        ],
    )
    client = _make_client(_MockResponse(200, apic_response(objects)))
    results = await client.query_class("fvBD", {})
    assert len(results) == 2
    assert all(r["_class"] == "fvBD" for r in results)
    assert {r["name"] for r in results} == {"servers", "clients"}


@pytest.mark.asyncio
async def test_query_class_empty_imdata_returns_empty_list():
    client = _make_client(_MockResponse(200, apic_response([])))
    results = await client.query_class("fvBD", {})
    assert results == []


@pytest.mark.asyncio
async def test_query_class_embeds_children_when_requested():
    objects = make_imdata_objects(
        "fvBD",
        [{"dn": "uni/tn-OT/BD-mgmt", "name": "mgmt"}],
        children_map={
            "uni/tn-OT/BD-mgmt": [
                {
                    "fvSubnet": {
                        "attributes": {
                            "ip": "10.0.0.1/24",
                            "dn": "uni/tn-OT/BD-mgmt/subnet-[10.0.0.1/24]",
                        }
                    }
                }
            ]
        },
    )
    client = _make_client(_MockResponse(200, apic_response(objects)))
    results = await client.query_class("fvBD", {}, include_children=["fvSubnet"])
    assert len(results) == 1
    children = results[0].get("_children", [])
    assert len(children) == 1
    assert children[0]["_class"] == "fvSubnet"
    assert children[0]["ip"] == "10.0.0.1/24"


# ── query_class() — re-authentication ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_re_auths_on_401_and_retries():
    """First call returns 401 → re-auth → second call returns data."""
    objects = make_imdata_objects(
        "fvBD", [{"dn": "uni/tn-OT/BD-servers", "name": "servers"}]
    )
    client = _make_client(
        _MockResponse(401, {}),  # first query → 401
        _MockResponse(200, apic_login_response()),  # re-authenticate
        _MockResponse(200, apic_response(objects)),  # retry query → success
    )
    results = await client.query_class("fvBD", {})
    assert len(results) == 1
    assert results[0]["name"] == "servers"


@pytest.mark.asyncio
async def test_query_class_persistent_401_after_reauth_raises_apic_auth_error():
    """First call 401 → re-auth succeeds → second call still 401 → error."""
    client = _make_client(
        _MockResponse(401, {}),  # first query → 401
        _MockResponse(200, apic_login_response()),  # re-authenticate
        _MockResponse(401, {}),  # retry query → still 401
    )
    with pytest.raises(ApicAuthError) as exc_info:
        await client.query_class("fvBD", {})
    assert "re-authentication" in str(exc_info.value)


# ── query_class() — network errors ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_timeout_raises_apic_connection_error():
    client = _make_client(httpx.TimeoutException("timed out"))
    with pytest.raises(ApicConnectionError) as exc_info:
        await client.query_class("fvBD", {})
    assert "timed out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_query_class_connect_error_raises_apic_connection_error():
    client = _make_client(httpx.ConnectError("no route to host"))
    with pytest.raises(ApicConnectionError):
        await client.query_class("fvBD", {})


# ── query_class() — malformed APIC responses ─────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_missing_imdata_key_raises_apic_response_error():
    """APIC returns 200 but response body has no 'imdata' key."""
    client = _make_client(_MockResponse(200, {"totalCount": "0"}))
    with pytest.raises(ApicResponseError) as exc_info:
        await client.query_class("fvBD", {})
    assert "imdata" in str(exc_info.value)


# ── query_class() — URL and parameter construction ────────────────────────────


@pytest.mark.asyncio
async def test_query_class_uses_class_endpoint_when_no_scope_dn():
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {})
    url = client._client.requests[0]["url"]
    assert "/api/class/fvBD.json" in url


@pytest.mark.asyncio
async def test_query_class_uses_mo_subtree_endpoint_with_scope_dn():
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {}, scope_dn="uni/tn-OT")
    url = client._client.requests[0]["url"]
    assert "/api/mo/uni/tn-OT.json" in url


@pytest.mark.asyncio
async def test_query_class_passes_limit_as_page_size():
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {}, limit=42)
    params = client._client.requests[0].get("params", {})
    assert params.get("page-size") == "42"


@pytest.mark.asyncio
async def test_query_class_sets_filter_param_from_filters():
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {"name": "servers"})
    params = client._client.requests[0].get("params", {})
    assert "query-target-filter" in params
    assert 'eq(fvBD.name,"servers")' in params["query-target-filter"]


@pytest.mark.asyncio
async def test_query_class_combines_filters_and_filter_expr():
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class(
        "fvBD", {"name": "srv"}, filter_expr='wcard(fvBD.dn,"uni/tn-OT")'
    )
    params = client._client.requests[0].get("params", {})
    filt = params.get("query-target-filter", "")
    assert filt.startswith("and(")
    assert 'wcard(fvBD.dn,"uni/tn-OT")' in filt
    assert 'eq(fvBD.name,"srv")' in filt


@pytest.mark.asyncio
async def test_query_class_sets_rsp_subtree_params_for_children():
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {}, include_children=["fvSubnet", "fvRsCtx"])
    params = client._client.requests[0].get("params", {})
    assert params.get("rsp-subtree") == "children"
    assert "fvSubnet" in params.get("rsp-subtree-class", "")
    assert "fvRsCtx" in params.get("rsp-subtree-class", "")
