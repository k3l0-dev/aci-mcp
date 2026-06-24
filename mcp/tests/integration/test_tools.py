# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Integration tests for the three MCP tools: search_classes, get_schema, query.

Uses StubBackend and MINIMAL_DESCRIPTIONS from conftest so tests always run
without a live APIC or the full data/ schema collection.
"""

from pathlib import Path

import pytest
from exceptions import UnknownClassError
from tests.conftest import MINIMAL_DESCRIPTIONS, StubBackend, make_ctx


# ── Tool context helpers ──────────────────────────────────────────────────────


def _stub_ctx(sample_imdata, schemas_dir, descriptions=None):
    """Build a tool context with optional custom descriptions."""
    desc = descriptions if descriptions is not None else dict(MINIMAL_DESCRIPTIONS)
    return make_ctx(
        {
            "descriptions": desc,
            "backend": StubBackend(sample_imdata),
            "schemas_dir": schemas_dir,
        }
    )


# ── search_classes ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_classes_returns_results(tool_ctx):
    from main import search_classes

    results = await search_classes("bridge", tool_ctx)
    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_search_classes_result_shape(tool_ctx):
    from main import search_classes

    results = await search_classes("tenant", tool_ctx)
    for r in results:
        assert "class_name" in r
        assert "label" in r
        assert "comment" in r


@pytest.mark.asyncio
async def test_search_classes_limit_capped_at_50(tool_ctx):
    from main import search_classes

    # Requesting 999 — must be capped at 50
    results = await search_classes("a", tool_ctx, limit=999)
    assert len(results) <= 50


@pytest.mark.asyncio
async def test_search_classes_limit_respected(tool_ctx):
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=2)
    assert len(results) <= 2


@pytest.mark.asyncio
async def test_search_classes_no_match_returns_empty(tool_ctx):
    from main import search_classes

    results = await search_classes("zzz_nonexistent_xyz_abc", tool_ctx)
    assert results == []


@pytest.mark.asyncio
async def test_search_classes_logs_result_count(tool_ctx):
    from main import search_classes

    await search_classes("bridge", tool_ctx)
    tool_ctx.info.assert_called_once()
    call_args = tool_ctx.info.call_args[0][0]
    assert "search_classes" in call_args


# ── get_schema ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_schema_unknown_class_returns_empty(tool_ctx):
    from main import get_schema

    schema = await get_schema("nonExistentClassXYZ", tool_ctx)
    assert schema == {}


@pytest.mark.asyncio
async def test_get_schema_unknown_class_logs_warning(tool_ctx):
    from main import get_schema

    await get_schema("nonExistentClassXYZ", tool_ctx)
    tool_ctx.warning.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not Path(__file__).parent.parent.parent.parent.joinpath("data", "schemas").exists(),
    reason="schemas/ collection not available",
)
async def test_get_schema_known_class_returns_required_fields(tool_ctx):
    from main import get_schema

    schema = await get_schema("fvBD", tool_ctx)
    assert schema != {}
    for field in ("identifiedBy", "rnFormat", "containedBy"):
        assert field in schema, f"Missing field: {field}"


# ── query ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_returns_list(tool_ctx):
    from main import query

    results = await query("fvTenant", tool_ctx)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_result_has_class_key(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx)
    assert all("_class" in r for r in results)
    assert all(r["_class"] == "fvBD" for r in results)


@pytest.mark.asyncio
async def test_query_with_equality_filter(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, filters={"name": "servers"})
    assert len(results) == 1
    assert results[0]["name"] == "servers"


@pytest.mark.asyncio
async def test_query_with_scope_dn_restricts_results(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, scope_dn="uni/tn-OT")
    assert len(results) >= 2
    assert all(r["dn"].startswith("uni/tn-OT/") for r in results)


@pytest.mark.asyncio
async def test_query_limit_capped_at_200(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=9999)
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_query_limit_applied(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_query_order_by_asc(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, order_by="fvBD.name|asc")
    names = [r["name"] for r in results]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_query_order_by_desc(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, order_by="fvBD.name|desc")
    names = [r["name"] for r in results]
    assert names == sorted(names, reverse=True)


@pytest.mark.asyncio
async def test_query_none_filters_equivalent_to_empty(tool_ctx):
    from main import query

    results_none = await query("fvTenant", tool_ctx, filters=None)
    results_empty = await query("fvTenant", tool_ctx, filters={})
    assert len(results_none) == len(results_empty)


@pytest.mark.asyncio
async def test_query_include_children_populates_children_key(tool_ctx):
    from main import query

    # fvBD "mgmt" in sample_imdata has a fvSubnet child
    results = await query("fvBD", tool_ctx, include_children=["fvSubnet"])
    mgmt = next((r for r in results if r["name"] == "mgmt"), None)
    assert mgmt is not None
    assert "_children" in mgmt
    assert mgmt["_children"][0]["_class"] == "fvSubnet"


# ── query — unknown class (UnknownClassError) ─────────────────────────────────


@pytest.mark.asyncio
async def test_query_unknown_class_raises_unknown_class_error(tool_ctx):
    from main import query

    with pytest.raises(UnknownClassError) as exc_info:
        await query("xyzTotallyFakeClass99", tool_ctx)
    assert exc_info.value.class_name == "xyzTotallyFakeClass99"


@pytest.mark.asyncio
async def test_query_unknown_class_error_includes_suggestions(
    sample_imdata, schemas_dir
):
    from main import query

    # Use a registry that contains "fvBD" so "fvBd" (typo) gets a suggestion
    ctx = _stub_ctx(sample_imdata, schemas_dir, descriptions=dict(MINIMAL_DESCRIPTIONS))
    with pytest.raises(UnknownClassError) as exc_info:
        await query("fvBd", ctx)  # lowercase 'd' — typo
    # Should suggest fvBD
    assert "fvBD" in exc_info.value.suggestions or "fvBD" in str(exc_info.value)


@pytest.mark.asyncio
async def test_query_unknown_class_logs_warning(tool_ctx):
    from main import query

    with pytest.raises(UnknownClassError):
        await query("xyzFakeClass", tool_ctx)
    tool_ctx.warning.assert_called_once()


@pytest.mark.asyncio
async def test_query_unknown_class_error_carries_registry_size(tool_ctx):
    from main import query

    with pytest.raises(UnknownClassError) as exc_info:
        await query("xyzFakeClass", tool_ctx)
    assert exc_info.value.registry_size == len(
        tool_ctx.lifespan_context["descriptions"]
    )


