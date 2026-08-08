# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/integration/test_tool_client_wiring.py

Tool-layer wiring tests — prove that the MCP tools in main.py (query,
get_by_dn, count) forward their parameters all the way into the *real*
ApicClient's request construction (registry.filter.build_filter +
apic.client.ApicClient.query_class/get_by_dn/count_class), not merely into
StubBackend's simplified Python reimplementation of filtering/scoping.

Why a separate file instead of extending test_tools.py: test_tools.py's own
module docstring states it "always run[s] without a live APIC ... [using]
StubBackend" — that is a deliberate, documented scope. StubBackend does not
call registry.filter.build_filter() at all (it filters in-memory with plain
dict equality), so a StubBackend-only suite can never notice a broken or
un-forwarded parameter in the real request-building path, nor a FilterError
that only build_filter() raises. Mixing "goes through the real client" tests
into a file whose docstring says the opposite would be confusing, so this
gap gets its own file, reusing the FakeHTTPClient/_MockResponse pattern
already proven in tests/unit/test_client.py.

Each test wires main's tool functions to a context whose "backend" is a real
ApicClient instance pointed at a FakeHTTPClient recorder (no network I/O),
then asserts on the URL/params that ApicClient actually built — the same
thing the real APIC would receive.
"""

from pathlib import Path

import pytest

from niwashi_mcp.apic.client import ApicClient
from niwashi_mcp.exceptions import FilterError
from tests.conftest import MINIMAL_DESCRIPTIONS, apic_response, make_ctx
from tests.unit.test_client import FakeHTTPClient, _MockResponse

# ── Wiring helpers ────────────────────────────────────────────────────────────


def _real_client(*responses) -> ApicClient:
    """Build a real ApicClient wired to a FakeHTTPClient request recorder.

    Mirrors tests/unit/test_client.py's `_make_client` helper exactly, so the
    request-capture mechanics are identical to the already-trusted
    client-level unit tests — only the caller (a tool function, not the test
    itself) differs.
    """
    client = ApicClient("10.0.0.1", "admin", "secret")
    client._client = FakeHTTPClient(*responses)
    return client


def _tool_ctx(backend: ApicClient, schemas_dir: Path):
    """Build a tool context whose backend is a real ApicClient, not StubBackend."""
    return make_ctx(
        {
            "descriptions": dict(MINIMAL_DESCRIPTIONS),
            "backend": backend,
            "schemas_dir": schemas_dir,
        }
    )


# ── query() → ApicClient.query_class() — page ────────────────────────────────


@pytest.mark.asyncio
async def test_query_page_reaches_real_apic_request(schemas_dir):
    """query()'s `page` argument must reach the APIC request as `page=<n>`,
    not just StubBackend's `calls` list which never round-trips through
    build_filter/query params construction."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query("fvBD", ctx, page=4)

    params = client._client.requests[0]["params"]
    assert params.get("page") == "4"


@pytest.mark.asyncio
async def test_query_page_none_omits_page_param(schemas_dir):
    """When page is not supplied, no `page` param is sent at all — confirms
    the tool's default (None) is forwarded, not e.g. a stray '0'."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query("fvBD", ctx)

    params = client._client.requests[0]["params"]
    assert "page" not in params


# ── query() → ApicClient.query_class() — rsp_subtree_include ─────────────────


@pytest.mark.asyncio
async def test_query_rsp_subtree_include_reaches_real_apic_request(schemas_dir):
    """query()'s `rsp_subtree_include` argument must reach the APIC request
    as `rsp-subtree-include=<value>`."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query("fvBD", ctx, rsp_subtree_include="faults,required")

    params = client._client.requests[0]["params"]
    assert params.get("rsp-subtree-include") == "faults,required"


# ── query() → ApicClient.query_class() — time_range ───────────────────────────


@pytest.mark.asyncio
async def test_query_time_range_reaches_real_apic_request(schemas_dir):
    """query()'s `time_range` argument must reach the APIC request as
    `time-range=<value>`. Uses fvBD (a MINIMAL_DESCRIPTIONS class) purely to
    exercise parameter forwarding — whether time_range is semantically valid
    for a given class is an APIC-side concern covered by tests/live/, not
    something the wiring layer enforces."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query("fvBD", ctx, time_range="24h")

    params = client._client.requests[0]["params"]
    assert params.get("time-range") == "24h"


# ── query() → ApicClient.query_class() — combined params in one call ─────────


@pytest.mark.asyncio
async def test_query_page_rsp_subtree_include_time_range_all_reach_request(
    schemas_dir,
):
    """All three previously StubBackend-only-tested parameters — page,
    rsp_subtree_include, time_range — reach a single real APIC request
    simultaneously, together with an equality filter and scope_dn, proving
    they compose correctly rather than only working in isolation."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query(
        "fvBD",
        ctx,
        filters={"name": "servers"},
        scope_dn="uni/tn-OT",
        page=2,
        rsp_subtree_include="health",
        time_range="1week",
    )

    req = client._client.requests[0]
    assert "/api/mo/uni/tn-OT.json" in req["url"]
    params = req["params"]
    assert params.get("page") == "2"
    assert params.get("rsp-subtree-include") == "health"
    assert params.get("time-range") == "1week"
    assert params.get("query-target") == "subtree"
    assert params.get("target-subtree-class") == "fvBD"
    assert 'eq(fvBD.name,"servers")' in params.get("query-target-filter", "")


# ── query() → limit clamping still lands as the real page-size ───────────────


@pytest.mark.asyncio
async def test_query_clamped_limit_reaches_real_apic_request_as_page_size(
    schemas_dir,
):
    """The tool-layer clamp (query()'s limit is clamped to [1, 200] before
    reaching the backend) must still be what the real APIC request carries
    as page-size — not the raw, unclamped caller value."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query("fvBD", ctx, limit=9999)

    params = client._client.requests[0]["params"]
    assert params.get("page-size") == "200"


# ── query() → FilterError propagation through the tool ───────────────────────


@pytest.mark.asyncio
async def test_query_invalid_filter_attribute_raises_filter_error(schemas_dir):
    """An invalid filter *attribute name* — one that fails
    registry.filter._IDENT_RE (must start with a letter, letters/digits
    only) — must raise FilterError all the way out through the query() tool.

    This is specifically an identifier-syntax check, not an "unknown
    attribute" check: build_filter() has no notion of which attributes
    actually exist on a class (that would silently build a valid-looking but
    APIC-meaningless eq() predicate). "bad-attr" is rejected here only
    because of the hyphen, which is not a valid ACI identifier character —
    StubBackend can never catch this because it never calls build_filter()
    at all; it matches filters via plain dict equality in Python.
    """
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    with pytest.raises(FilterError, match="attribute"):
        await query("fvBD", ctx, filters={"bad-attr": "value"})

    # The request must never have been sent — build_filter() raises before
    # ApicClient.query_class() issues the GET.
    assert client._client.requests == []


@pytest.mark.asyncio
async def test_query_leading_digit_filter_attribute_raises_filter_error(schemas_dir):
    """A second, distinct invalid-identifier shape — a leading digit — also
    raises FilterError, confirming the check is the general identifier regex
    and not a hardcoded blocklist of specific characters."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    with pytest.raises(FilterError):
        await query("fvBD", ctx, filters={"123attr": "value"})


@pytest.mark.asyncio
async def test_count_invalid_filter_attribute_raises_filter_error(schemas_dir):
    """count() shares the same build_filter() call path (via
    ApicClient.count_class) as query() — an invalid filter attribute name
    must propagate as FilterError through count() too, not just query()."""
    from niwashi_mcp.main import count

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    with pytest.raises(FilterError):
        await count("fvBD", ctx, filters={"bad-attr": "value"})


@pytest.mark.asyncio
async def test_query_well_formed_but_nonexistent_attribute_does_not_raise(
    schemas_dir,
):
    """Contrast case: a syntactically well-formed attribute name that simply
    does not exist as a real fvBD property is NOT a FilterError — it builds
    a valid eq() predicate and is sent to the APIC, which would silently
    return no matches. build_filter() validates identifier syntax only, never
    attribute existence; that distinction is exactly what the previous
    FilterError tests must not blur."""
    from niwashi_mcp.main import query

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await query("fvBD", ctx, filters={"totallyNotARealProperty": "value"})

    params = client._client.requests[0]["params"]
    assert 'eq(fvBD.totallyNotARealProperty,"value")' in params["query-target-filter"]


# ── get_by_dn() → ApicClient.get_by_dn() ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_dn_config_only_reaches_real_apic_request(schemas_dir):
    """get_by_dn()'s config_only flag must reach the request as
    rsp-prop-include=config-only against the real /api/mo/{dn}.json URL."""
    from niwashi_mcp.main import get_by_dn

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await get_by_dn("uni/tn-OT/BD-servers", ctx, config_only=True)

    req = client._client.requests[0]
    assert "/api/mo/uni/tn-OT/BD-servers.json" in req["url"]
    assert req["params"].get("rsp-prop-include") == "config-only"


@pytest.mark.asyncio
async def test_get_by_dn_include_children_reaches_real_apic_request(schemas_dir):
    """get_by_dn()'s include_children list must reach the request as
    rsp-subtree=children&rsp-subtree-class=<comma-joined classes>."""
    from niwashi_mcp.main import get_by_dn

    client = _real_client(_MockResponse(200, apic_response([])))
    ctx = _tool_ctx(client, schemas_dir)

    await get_by_dn(
        "uni/tn-OT/BD-servers", ctx, include_children=["fvSubnet", "fvRsCtx"]
    )

    params = client._client.requests[0]["params"]
    assert params.get("rsp-subtree") == "children"
    assert "fvSubnet" in params.get("rsp-subtree-class", "")
    assert "fvRsCtx" in params.get("rsp-subtree-class", "")


# ── count() → ApicClient.count_class() ────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_scope_dn_and_filters_reach_real_apic_request(schemas_dir):
    """count()'s scope_dn and filters must reach the real request as the
    subtree endpoint plus a query-target-filter, exactly like query()."""
    from niwashi_mcp.main import count

    body = {"imdata": [], "totalCount": "2"}
    client = _real_client(_MockResponse(200, body))
    ctx = _tool_ctx(client, schemas_dir)

    result = await count("fvBD", ctx, filters={"arpFlood": "no"}, scope_dn="uni/tn-OT")

    req = client._client.requests[0]
    assert "/api/mo/uni/tn-OT.json" in req["url"]
    params = req["params"]
    assert params.get("query-target") == "subtree"
    assert params.get("page-size") == "1"
    assert "rsp-subtree-include" not in params
    assert 'eq(fvBD.arpFlood,"no")' in params.get("query-target-filter", "")
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_count_filter_expr_combines_with_filters_in_real_request(schemas_dir):
    """count()'s filter_expr and filters combine via and(...) in the real
    request exactly as query()'s do — same ApicClient code path."""
    from niwashi_mcp.main import count

    body = {"imdata": [], "totalCount": "0"}
    client = _real_client(_MockResponse(200, body))
    ctx = _tool_ctx(client, schemas_dir)

    await count(
        "fvBD",
        ctx,
        filters={"name": "servers"},
        filter_expr='wcard(fvBD.dn,"uni/tn-OT")',
    )

    params = client._client.requests[0]["params"]
    filt = params.get("query-target-filter", "")
    assert filt.startswith("and(")
    assert 'wcard(fvBD.dn,"uni/tn-OT")' in filt
    assert 'eq(fvBD.name,"servers")' in filt
