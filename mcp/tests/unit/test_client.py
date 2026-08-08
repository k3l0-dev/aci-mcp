# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Unit tests for apic/client.py — ApicClient with a simulated httpx transport.

All tests use FakeHTTPClient to avoid any network calls.  Each test controls
exactly which responses (or exceptions) the fake transport returns, so we can
exercise every branch: happy path, re-auth, timeouts, malformed JSON, etc.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from niwashi_mcp.apic.client import _MAX_OBJECTS, _MAX_PAGES, ApicClient
from niwashi_mcp.exceptions import (
    ApicAuthError,
    ApicConnectionError,
    ApicError,
    ApicRequestError,
    ApicResponseError,
)
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


def _make_client(*responses, retry_attempts: int = 3) -> ApicClient:
    """Build an ApicClient wired to a FakeHTTPClient.

    retry_backoff_base=0 so retry tests never sleep for real; retry_attempts
    defaults to the production default (3) but is overridable per test.
    """
    client = ApicClient(
        "10.0.0.1", "admin", "secret",
        retry_attempts=retry_attempts, retry_backoff_base=0.0,
    )
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
    result = await client.query_class("fvBD", {})
    assert len(result.objects) == 2
    assert all(r["_class"] == "fvBD" for r in result.objects)
    assert {r["name"] for r in result.objects} == {"servers", "clients"}
    assert result.total_available == 2
    assert result.complete is True


@pytest.mark.asyncio
async def test_query_class_empty_imdata_returns_empty_list():
    client = _make_client(_MockResponse(200, apic_response([])))
    result = await client.query_class("fvBD", {})
    assert result.objects == []
    assert result.total_available == 0


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
    result = await client.query_class("fvBD", {}, include_children=["fvSubnet"])
    assert len(result.objects) == 1
    children = result.objects[0].get("_children", [])
    assert len(children) == 1
    assert children[0]["_class"] == "fvSubnet"
    assert children[0]["ip"] == "10.0.0.1/24"


# ── query_class() — QueryResult / totalCount parsing ──────────────────────────


@pytest.mark.asyncio
async def test_query_class_total_available_parses_total_count():
    """total_available reflects the APIC-reported totalCount, which can be
    larger than the number of objects actually returned on this page."""
    objects = make_imdata_objects(
        "fvBD", [{"dn": "uni/tn-OT/BD-servers", "name": "servers"}]
    )
    body = {"totalCount": "250", "imdata": objects}
    client = _make_client(_MockResponse(200, body))
    result = await client.query_class("fvBD", {})
    assert len(result.objects) == 1
    assert result.total_available == 250
    assert result.complete is True


@pytest.mark.asyncio
async def test_query_class_total_available_falls_back_to_object_count():
    """A missing/non-numeric totalCount falls back to the number of objects
    actually parsed, rather than raising."""
    objects = make_imdata_objects(
        "fvBD", [{"dn": "uni/tn-OT/BD-servers", "name": "servers"}]
    )
    body = {"imdata": objects}
    client = _make_client(_MockResponse(200, body))
    result = await client.query_class("fvBD", {})
    assert result.total_available == 1


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
    result = await client.query_class("fvBD", {})
    assert len(result.objects) == 1
    assert result.objects[0]["name"] == "servers"


@pytest.mark.asyncio
async def test_query_class_persistent_401_after_reauth_raises_apic_auth_error():
    """First call 401 → re-auth succeeds → second call still 401 → error.

    Raised immediately, not absorbed into the transient-status retry budget —
    a bad credential won't fix itself on a second outer attempt."""
    client = _make_client(
        _MockResponse(401, {}),  # first query → 401
        _MockResponse(200, apic_login_response()),  # re-authenticate
        _MockResponse(401, {}),  # retry query → still 401
    )
    with pytest.raises(ApicAuthError) as exc_info:
        await client.query_class("fvBD", {})
    assert "re-authentication" in str(exc_info.value)
    assert len(client._client.requests) == 3


# ── query_class() — network errors ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_timeout_raises_apic_connection_error():
    """A timeout on every attempt exhausts the retry budget (3 by default)."""
    client = _make_client(*[httpx.TimeoutException("timed out")] * 3)
    with pytest.raises(ApicConnectionError) as exc_info:
        await client.query_class("fvBD", {})
    assert "timed out" in str(exc_info.value)
    assert len(client._client.requests) == 3


@pytest.mark.asyncio
async def test_query_class_connect_error_raises_apic_connection_error():
    """A connect error on every attempt exhausts the retry budget."""
    client = _make_client(*[httpx.ConnectError("no route to host")] * 3)
    with pytest.raises(ApicConnectionError):
        await client.query_class("fvBD", {})
    assert len(client._client.requests) == 3


# ── query_class() — transient-status retry ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500, 502, 503, 504])
async def test_query_class_retries_transient_status_then_succeeds(status):
    """A single transient failure is retried and the second attempt succeeds."""
    client = _make_client(
        _MockResponse(status, {}),
        _MockResponse(200, apic_response([])),
    )
    result = await client.query_class("fvBD", {})
    assert result.objects == []
    assert len(client._client.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 500, 502, 503, 504])
async def test_query_class_exhausts_retries_on_persistent_transient_status(status):
    """A transient status that never recovers is raised after the full budget."""
    client = _make_client(*[_MockResponse(status, {})] * 3)
    with pytest.raises(ApicRequestError) as exc_info:
        await client.query_class("fvBD", {})
    assert exc_info.value.status == status
    assert len(client._client.requests) == 3


@pytest.mark.asyncio
async def test_query_class_retries_on_connect_error_then_succeeds():
    client = _make_client(
        httpx.ConnectError("connection refused"),
        _MockResponse(200, apic_response([])),
    )
    result = await client.query_class("fvBD", {})
    assert result.objects == []
    assert len(client._client.requests) == 2


@pytest.mark.asyncio
async def test_query_class_retries_on_timeout_then_succeeds():
    client = _make_client(
        httpx.TimeoutException("timed out"),
        _MockResponse(200, apic_response([])),
    )
    result = await client.query_class("fvBD", {})
    assert result.objects == []
    assert len(client._client.requests) == 2


@pytest.mark.asyncio
async def test_query_class_400_is_never_retried():
    """A permanent error (400) must not consume the retry budget — proven by
    queuing exactly one response: if the implementation retried, the fake
    transport's queue would be empty on the second GET and raise IndexError."""
    client = _make_client(_MockResponse(400, {}))
    with pytest.raises(ApicRequestError):
        await client.query_class("fvBD", {})
    assert len(client._client.requests) == 1


@pytest.mark.asyncio
async def test_query_class_recovers_after_transient_connection_error_post_reauth():
    """401 -> re-auth -> connection error on the retry GET -> next outer
    attempt succeeds without re-authenticating again."""
    objects = make_imdata_objects(
        "fvBD", [{"dn": "uni/tn-OT/BD-servers", "name": "servers"}]
    )
    client = _make_client(
        _MockResponse(401, {}),
        _MockResponse(200, apic_login_response()),
        httpx.TimeoutException("timeout on retry"),
        _MockResponse(200, apic_response(objects)),
    )
    result = await client.query_class("fvBD", {})
    assert len(result.objects) == 1
    assert len(client._client.requests) == 4


@pytest.mark.asyncio
async def test_query_class_exhausts_retry_budget_after_reauth_connection_errors():
    """401 -> re-auth -> connection error, then two more failed outer
    attempts — exhausts the retry budget and raises ApicConnectionError."""
    client = _make_client(
        _MockResponse(401, {}),
        _MockResponse(200, apic_login_response()),
        httpx.TimeoutException("timeout 1"),
        httpx.TimeoutException("timeout 2"),
        httpx.TimeoutException("timeout 3"),
    )
    with pytest.raises(ApicConnectionError):
        await client.query_class("fvBD", {})
    assert len(client._client.requests) == 5


@pytest.mark.asyncio
async def test_query_class_401_then_transient_status_then_recovers():
    """A transient failure after a successful re-auth still gets its own
    retry, rather than being conflated with the auth path."""
    client = _make_client(
        _MockResponse(401, {}),
        _MockResponse(200, apic_login_response()),
        _MockResponse(500, {}),
        _MockResponse(200, apic_response([])),
    )
    result = await client.query_class("fvBD", {})
    assert result.objects == []
    assert len(client._client.requests) == 4


@pytest.mark.asyncio
async def test_backoff_delay_is_bounded_and_increasing():
    client = ApicClient("10.0.0.1", "admin", "secret", retry_backoff_base=0.2)
    d1 = client._backoff_delay(1)
    d2 = client._backoff_delay(2)
    d3 = client._backoff_delay(3)
    assert d1 == 0.2
    assert d2 == 0.4
    assert d1 < d2 < d3
    assert client._backoff_delay(20) == 2.0  # capped


# ── query_class() — malformed APIC responses ─────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_missing_imdata_key_raises_apic_response_error():
    """APIC returns 200 but response body has no 'imdata' key."""
    client = _make_client(_MockResponse(200, {"totalCount": "0"}))
    with pytest.raises(ApicResponseError) as exc_info:
        await client.query_class("fvBD", {})
    assert "imdata" in str(exc_info.value)


# ── query_class() — non-auth HTTP errors ──────────────────────────────────────


@pytest.mark.asyncio
async def test_query_class_400_with_apic_error_body_raises_apic_request_error():
    """A 400 (e.g. malformed filter_expr) with an APIC error body is wrapped,
    surfacing the APIC-supplied error text rather than a raw httpx error."""
    body = {
        "totalCount": "1",
        "imdata": [
            {
                "error": {
                    "attributes": {
                        "code": "400",
                        "text": "unable to process the query, class not found",
                    }
                }
            }
        ],
    }
    client = _make_client(_MockResponse(400, body))
    with pytest.raises(ApicRequestError) as exc_info:
        await client.query_class("fvBD", {})
    assert exc_info.value.status == 400
    assert "class not found" in exc_info.value.apic_text
    assert "class not found" in str(exc_info.value)
    assert len(client._client.requests) == 1  # 400 is permanent — never retried


@pytest.mark.asyncio
async def test_query_class_400_without_body_raises_apic_request_error():
    """A 400 with no usable APIC error body still raises ApicRequestError,
    just without APIC-supplied detail text."""
    client = _make_client(_MockResponse(400, {}))
    with pytest.raises(ApicRequestError) as exc_info:
        await client.query_class("fvBD", {})
    assert exc_info.value.status == 400
    assert exc_info.value.apic_text == ""
    assert len(client._client.requests) == 1


@pytest.mark.asyncio
async def test_query_class_400_is_catchable_as_apic_error():
    """ApicRequestError is a subclass of the base ApicError taxonomy."""
    client = _make_client(_MockResponse(400, {}))
    with pytest.raises(ApicError):
        await client.query_class("fvBD", {})


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


@pytest.mark.asyncio
async def test_query_class_uses_only_filter_expr_when_no_filters():
    """filter_expr alone (no equality filters) sets query-target-filter directly."""
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {}, filter_expr='wcard(fvBD.dn,"uni/tn-OT")')
    params = client._client.requests[0].get("params", {})
    assert params.get("query-target-filter") == 'wcard(fvBD.dn,"uni/tn-OT")'


@pytest.mark.asyncio
async def test_query_class_sets_order_by_param():
    """order_by kwarg maps to 'order-by' query parameter."""
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("faultInst", {}, order_by="faultInst.severity|desc")
    params = client._client.requests[0].get("params", {})
    assert params.get("order-by") == "faultInst.severity|desc"


@pytest.mark.asyncio
async def test_query_class_sets_rsp_subtree_include_param():
    """rsp_subtree_include kwarg maps to 'rsp-subtree-include' query parameter."""
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {}, rsp_subtree_include="faults,required")
    params = client._client.requests[0].get("params", {})
    assert params.get("rsp-subtree-include") == "faults,required"


@pytest.mark.asyncio
async def test_query_class_sets_time_range_param():
    """time_range kwarg maps to 'time-range' query parameter."""
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("faultRecord", {}, time_range="24h")
    params = client._client.requests[0].get("params", {})
    assert params.get("time-range") == "24h"


@pytest.mark.asyncio
async def test_query_class_sets_page_param():
    """page kwarg maps to 'page' query parameter as a string."""
    client = _make_client(_MockResponse(200, apic_response([])))
    await client.query_class("fvBD", {}, page=2)
    params = client._client.requests[0].get("params", {})
    assert params.get("page") == "2"


# ── query_class() — fetch_all pagination ──────────────────────────────────────


def _bd_page(names: list[str], total_count: int) -> _MockResponse:
    """Build one fvBD page response carrying `total_count` as totalCount."""
    objects = make_imdata_objects(
        "fvBD", [{"dn": f"uni/tn-OT/BD-{n}", "name": n} for n in names]
    )
    return _MockResponse(200, {"totalCount": str(total_count), "imdata": objects})


@pytest.mark.asyncio
async def test_query_class_fetch_all_walks_pages_and_concatenates():
    """Three queued pages of page-size 3, the last one short (1 object) —
    fetch_all stops there instead of requesting a fourth page."""
    client = _make_client(
        _bd_page(["a", "b", "c"], 7),
        _bd_page(["d", "e", "f"], 7),
        _bd_page(["g"], 7),
    )
    result = await client.query_class("fvBD", {}, limit=3, fetch_all=True)
    assert {o["name"] for o in result.objects} == {"a", "b", "c", "d", "e", "f", "g"}
    assert len(result.objects) == 7
    assert result.total_available == 7
    assert result.complete is True
    assert len(client._client.requests) == 3


@pytest.mark.asyncio
async def test_query_class_fetch_all_sets_page_and_page_size_per_request():
    """Each page request in a fetch_all loop carries the same page-size
    (=limit) and an incrementing 0-based page number."""
    client = _make_client(
        _bd_page(["a", "b"], 5),
        _bd_page(["c", "d"], 5),
        _bd_page(["e"], 5),
    )
    await client.query_class("fvBD", {}, limit=2, fetch_all=True)
    params_per_page = [r.get("params", {}) for r in client._client.requests]
    assert [p.get("page") for p in params_per_page] == ["0", "1", "2"]
    assert all(p.get("page-size") == "2" for p in params_per_page)


@pytest.mark.asyncio
async def test_query_class_fetch_all_single_short_page_no_truncation():
    """A single page shorter than `limit` is the whole matching set — no
    truncation or cap involved."""
    client = _make_client(_bd_page(["only"], 1))
    result = await client.query_class("fvBD", {}, limit=20, fetch_all=True)
    assert len(result.objects) == 1
    assert result.total_available == 1
    assert result.complete is True
    assert len(client._client.requests) == 1


@pytest.mark.asyncio
async def test_query_class_fetch_all_stops_at_max_pages_cap():
    """_MAX_PAGES full pages, none short — the loop stops at the page-count
    cap (not the object-count cap, since page_size * _MAX_PAGES stays well
    under _MAX_OBJECTS) and reports complete=False, while total_available
    still carries the true (far larger) totalCount."""
    page_size = 3
    pages = [
        _bd_page([f"p{p}-{i}" for i in range(page_size)], 999999)
        for p in range(_MAX_PAGES + 2)  # more supply than the cap will consume
    ]
    client = _make_client(*pages)
    result = await client.query_class("fvBD", {}, limit=page_size, fetch_all=True)
    assert len(result.objects) == _MAX_PAGES * page_size
    assert result.total_available == 999999
    assert result.complete is False
    assert len(client._client.requests) == _MAX_PAGES


@pytest.mark.asyncio
async def test_query_class_fetch_all_stops_at_max_objects_cap():
    """Two full pages of _MAX_OBJECTS/2 objects each exactly reach the
    object-count cap in far fewer than _MAX_PAGES requests — proving the
    object cap (not just the page cap) independently stops the loop."""
    page_size = _MAX_OBJECTS // 2
    client = _make_client(
        _bd_page([f"p0-{i}" for i in range(page_size)], 999999),
        _bd_page([f"p1-{i}" for i in range(page_size)], 999999),
        _bd_page([f"p2-{i}" for i in range(page_size)], 999999),  # unused — proves early stop
    )
    result = await client.query_class("fvBD", {}, limit=page_size, fetch_all=True)
    assert len(result.objects) == _MAX_OBJECTS
    assert result.total_available == 999999
    assert result.complete is False
    assert len(client._client.requests) == 2


@pytest.mark.asyncio
async def test_query_class_timeout_after_reauth_recovers_on_next_attempt():
    """A timeout on the retry GET (right after a successful re-auth) is now
    caught by the outer retry budget instead of failing immediately — the
    next outer attempt succeeds without re-authenticating again."""
    objects = make_imdata_objects(
        "fvBD", [{"dn": "uni/tn-OT/BD-servers", "name": "servers"}]
    )
    client = _make_client(
        _MockResponse(401, apic_response([])),        # first GET → 401
        _MockResponse(200, apic_login_response()),    # POST (re-auth) → OK
        httpx.TimeoutException("timeout on retry"),   # retry GET → timeout
        _MockResponse(200, apic_response(objects)),   # next outer attempt → success
    )
    result = await client.query_class("fvBD", {})
    assert len(result.objects) == 1
    assert len(client._client.requests) == 4


@pytest.mark.asyncio
async def test_query_class_connect_error_after_reauth_exhausts_retry_budget():
    """A ConnectError on the retry GET after re-auth, persisting across the
    full retry budget, raises ApicConnectionError."""
    client = _make_client(
        _MockResponse(401, apic_response([])),
        _MockResponse(200, apic_login_response()),
        httpx.ConnectError("connection refused on retry"),
        httpx.ConnectError("connection refused 2"),
        httpx.ConnectError("connection refused 3"),
    )
    with pytest.raises(ApicConnectionError):
        await client.query_class("fvBD", {})
    assert len(client._client.requests) == 5


@pytest.mark.asyncio
async def test_query_class_invalid_json_raises_apic_response_error():
    """A non-JSON response body raises ApicResponseError."""

    class _BadJsonResponse:
        status_code = 200
        is_success = True

        def json(self):
            raise ValueError("not valid JSON")

        def raise_for_status(self):
            pass

    client = _make_client(_BadJsonResponse())
    with pytest.raises(ApicResponseError, match="not valid JSON"):
        await client.query_class("fvBD", {})


@pytest.mark.asyncio
async def test_close_releases_http_client():
    """close() delegates to the underlying httpx client aclose()."""
    client = _make_client(_MockResponse(200, apic_response([])))
    closed: list[bool] = []
    original_aclose = client._client.aclose

    async def _track_close():
        closed.append(True)
        await original_aclose()

    client._client.aclose = _track_close
    await client.close()
    assert closed == [True]


# ── get_by_dn() / count_class() — _request_json() error handling ────────────
#
# get_by_dn() and count_class() share _request_json() rather than
# query_class()'s code path, so its error handling (401/403 re-auth,
# non-auth HTTP errors, malformed JSON) is exercised independently here.


@pytest.mark.asyncio
async def test_get_by_dn_found_returns_attributes():
    objects = make_imdata_objects("fvBD", [{"name": "servers", "dn": "uni/tn-OT/BD-servers"}])
    client = _make_client(_MockResponse(200, apic_response(objects)))
    obj = await client.get_by_dn("uni/tn-OT/BD-servers")
    assert obj["_class"] == "fvBD"
    assert obj["name"] == "servers"


@pytest.mark.asyncio
async def test_get_by_dn_missing_returns_none():
    """A real 'not found' is HTTP 200 with an empty imdata list — never a
    404 — so it must not be retried or raised, just returned as None."""
    client = _make_client(_MockResponse(200, apic_response([])))
    assert await client.get_by_dn("uni/tn-OT/BD-doesNotExist") is None
    assert len(client._client.requests) == 1


@pytest.mark.asyncio
async def test_get_by_dn_retries_on_404_then_succeeds():
    objects = make_imdata_objects("fvBD", [{"name": "servers", "dn": "uni/tn-OT/BD-servers"}])
    client = _make_client(
        _MockResponse(404, {}),
        _MockResponse(200, apic_response(objects)),
    )
    obj = await client.get_by_dn("uni/tn-OT/BD-servers")
    assert obj["name"] == "servers"
    assert len(client._client.requests) == 2


@pytest.mark.asyncio
async def test_get_by_dn_retries_exhausted_raises_apic_request_error():
    client = _make_client(*[_MockResponse(404, {})] * 3)
    with pytest.raises(ApicRequestError) as exc_info:
        await client.get_by_dn("uni/tn-OT/BD-servers")
    assert exc_info.value.status == 404
    assert len(client._client.requests) == 3


@pytest.mark.asyncio
async def test_get_by_dn_400_raises_apic_request_error():
    body = {
        "imdata": [
            {"error": {"attributes": {"code": "400", "text": "malformed DN"}}}
        ]
    }
    client = _make_client(_MockResponse(400, body))
    with pytest.raises(ApicRequestError) as exc_info:
        await client.get_by_dn("not-a-real-dn")
    assert exc_info.value.status == 400
    assert "malformed DN" in exc_info.value.apic_text
    assert len(client._client.requests) == 1


@pytest.mark.asyncio
async def test_get_by_dn_re_authenticates_on_401():
    objects = make_imdata_objects("fvBD", [{"name": "servers", "dn": "uni/tn-OT/BD-servers"}])
    client = _make_client(
        _MockResponse(401, {}),
        _MockResponse(200, apic_login_response()),
        _MockResponse(200, apic_response(objects)),
    )
    obj = await client.get_by_dn("uni/tn-OT/BD-servers")
    assert obj["name"] == "servers"


@pytest.mark.asyncio
async def test_count_class_reads_total_count():
    objects = make_imdata_objects("fvBD", [{"dn": "uni/tn-OT/BD-servers"}])
    body = {"imdata": objects, "totalCount": "42"}
    client = _make_client(_MockResponse(200, body))
    assert await client.count_class("fvBD", {}) == 42


@pytest.mark.asyncio
async def test_count_class_asks_for_a_one_object_page_not_the_count_subtree():
    """Regression guard for the count idiom itself.

    count_class() must request a 1-object page and read `totalCount`. It must
    NOT send `rsp-subtree-include=count`: that mechanism's `moCount` tally was
    measured wrong on APIC 6.0(9c), reporting 0 for scoped counts against real
    subtrees holding up to 192 objects — which reads as a legitimate answer
    ("none in this tenant") rather than as a failure. See the count_class()
    docstring for the full measurements.
    """
    objects = make_imdata_objects("fvBD", [{"dn": "uni/tn-OT/BD-servers"}])
    body = {"imdata": objects, "totalCount": "192"}
    client = _make_client(_MockResponse(200, body))

    assert await client.count_class("fvBD", {}, scope_dn="uni/tn-OT") == 192

    params = client._client.requests[0]["params"]
    assert params["page-size"] == "1"
    assert "rsp-subtree-include" not in params


@pytest.mark.asyncio
async def test_count_class_prefers_total_count_over_a_mo_count_body():
    """`totalCount` wins even if a `moCount` object is present in the body.

    The two disagreed by nearly 2x on a live fabric (moCount 203 vs. the real
    403 fvBD); totalCount was the exact one. This pins which field is
    authoritative, so restoring the old parser can never pass silently.
    """
    body = {
        "imdata": [{"moCount": {"attributes": {"count": "203"}}}],
        "totalCount": "403",
    }
    client = _make_client(_MockResponse(200, body))
    assert await client.count_class("fvBD", {}) == 403


@pytest.mark.asyncio
async def test_count_class_500_raises_apic_request_error():
    client = _make_client(*[_MockResponse(500, {})] * 3)
    with pytest.raises(ApicRequestError) as exc_info:
        await client.count_class("fvBD", {})
    assert exc_info.value.status == 500
    assert len(client._client.requests) == 3


@pytest.mark.asyncio
async def test_count_class_retries_on_500_then_succeeds():
    objects = make_imdata_objects("fvBD", [{"dn": "uni/tn-OT/BD-servers"}])
    body = {"imdata": objects, "totalCount": "7"}
    client = _make_client(_MockResponse(500, {}), _MockResponse(200, body))
    assert await client.count_class("fvBD", {}) == 7
    assert len(client._client.requests) == 2
