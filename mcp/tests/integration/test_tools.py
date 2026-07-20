# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Integration tests for the three MCP tools: search_classes, get_schema, query.

Uses StubBackend and MINIMAL_DESCRIPTIONS from conftest so tests always run
without a live APIC or the full data/ schema collection.
"""

import json
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


# ── search_classes — limit boundary values (0, -1, 1, cap, cap+1) ────────────


@pytest.mark.asyncio
async def test_search_classes_limit_zero_clamped_to_one(tool_ctx):
    """A limit of 0 is clamped to 1, not passed through to a [:0] slice."""
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=0)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_classes_limit_negative_clamped_to_one(tool_ctx):
    """A negative limit is clamped to 1 rather than mis-slicing results."""
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=-1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_classes_limit_one_returns_exactly_one(tool_ctx):
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_search_classes_limit_at_cap_50_respected(tool_ctx):
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=50)
    assert len(results) <= 50


@pytest.mark.asyncio
async def test_search_classes_limit_cap_plus_one_still_capped_at_50(tool_ctx):
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=51)
    assert len(results) <= 50


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


# ── query — limit boundary values (0, -1, 1, cap, cap+1) ─────────────────────


@pytest.mark.asyncio
async def test_query_limit_zero_clamped_to_one(tool_ctx):
    """A limit of 0 is clamped to 1, not forwarded to APIC as page-size=0."""
    from main import query

    results = await query("fvBD", tool_ctx, limit=0)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_query_limit_negative_clamped_to_one(tool_ctx):
    """A negative limit is clamped to 1, not forwarded to APIC as page-size=-1."""
    from main import query

    results = await query("fvBD", tool_ctx, limit=-1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_query_limit_negative_reaches_backend_as_clamped_value(
    sample_imdata, schemas_dir
):
    """The clamped value — not the raw negative input — is what the backend
    actually receives, so a negative limit never reaches the real APIC."""
    from main import query

    ctx = _stub_ctx(sample_imdata, schemas_dir)
    await query("fvBD", ctx, limit=-5)
    backend = ctx.lifespan_context["backend"]
    assert backend.calls[-1]["limit"] == 1


@pytest.mark.asyncio
async def test_query_limit_one_returns_exactly_one(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_query_limit_at_cap_200_respected(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=200)
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_query_limit_cap_plus_one_still_capped_at_200(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=201)
    assert len(results) <= 200


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


# ── query — registry/schema asymmetry fallback ────────────────────────────────
#
# The schemas/ collection and class-descriptions.json are built by separate
# schema-collector passes and can drift apart (~300 classes in production
# have a jsonmeta schema but no descriptions entry). A class absent from
# `descriptions` should only be rejected as UnknownClassError when it *also*
# has no resolvable schema file — otherwise a legitimate, queryable class
# would be needlessly blocked.

_EXTRA_ONLY_SCHEMA = {
    "aaaExtraOnlyClass": {
        "identifiedBy": ["name"],
        "rnFormat": "extra-{name}",
        "label": "Extra Only Class",
        "isAbstract": False,
        "isConfigurable": True,
        "className": "ExtraOnlyClass",
        "classPkg": "aaa",
    }
}


@pytest.mark.asyncio
async def test_query_allows_class_absent_from_descriptions_but_with_schema(
    sample_imdata, tmp_path
):
    """A class with a schema file but no class-descriptions entry is allowed
    through instead of raising UnknownClassError."""
    from main import query

    (tmp_path / "aaaExtraOnlyClass.json").write_text(
        json.dumps(_EXTRA_ONLY_SCHEMA), encoding="utf-8"
    )
    ctx = _stub_ctx(sample_imdata, tmp_path, descriptions=dict(MINIMAL_DESCRIPTIONS))

    results = await query("aaaExtraOnlyClass", ctx)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_allows_class_with_schema_logs_warning_not_error(
    sample_imdata, tmp_path
):
    """The schema-fallback path logs a warning (for observability) rather
    than silently allowing the query with no trace."""
    from main import query

    (tmp_path / "aaaExtraOnlyClass.json").write_text(
        json.dumps(_EXTRA_ONLY_SCHEMA), encoding="utf-8"
    )
    ctx = _stub_ctx(sample_imdata, tmp_path, descriptions=dict(MINIMAL_DESCRIPTIONS))

    await query("aaaExtraOnlyClass", ctx)
    ctx.warning.assert_called_once()
    assert "schema file resolved" in ctx.warning.call_args[0][0]


@pytest.mark.asyncio
async def test_query_rejects_class_with_neither_description_nor_schema(
    sample_imdata, tmp_path
):
    """A class with no descriptions entry AND no resolvable schema file is
    still rejected — the fallback only rescues genuinely known classes."""
    from main import query

    # tmp_path is empty — no schema file for any class.
    ctx = _stub_ctx(sample_imdata, tmp_path, descriptions=dict(MINIMAL_DESCRIPTIONS))

    with pytest.raises(UnknownClassError):
        await query("totallyMadeUpClassNotAnywhere", ctx)


# ── query — config_only (Task 5) ──────────────────────────────────────────────

# imdata carrying an operational attribute (modTs) the stub strips under
# config_only, mirroring the APIC rsp-prop-include=config-only behaviour.
_CONFIG_IMDATA = [
    {
        "fvBD": {
            "attributes": {
                "name": "servers",
                "dn": "uni/tn-OT/BD-servers",
                "arpFlood": "no",
                "modTs": "2026-07-20T10:00:00.000+00:00",
                "lcOwn": "local",
            }
        }
    },
]


@pytest.mark.asyncio
async def test_query_config_only_passed_through(sample_imdata, schemas_dir):
    from main import query

    ctx = _stub_ctx(sample_imdata, schemas_dir)
    await query("fvBD", ctx, config_only=True)
    call = ctx.lifespan_context["backend"].calls[-1]
    assert call["config_only"] is True


@pytest.mark.asyncio
async def test_query_config_only_strips_operational_attrs(schemas_dir):
    from main import query

    ctx = _stub_ctx(_CONFIG_IMDATA, schemas_dir)
    results = await query("fvBD", ctx, config_only=True)
    assert results[0]["name"] == "servers"
    assert "modTs" not in results[0]
    assert "lcOwn" not in results[0]


@pytest.mark.asyncio
async def test_query_without_config_only_keeps_all_attrs(schemas_dir):
    from main import query

    ctx = _stub_ctx(_CONFIG_IMDATA, schemas_dir)
    results = await query("fvBD", ctx)
    assert "modTs" in results[0]


# ── get_by_dn (Task 3) ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_dn_found_returns_object(tool_ctx):
    from main import get_by_dn

    obj = await get_by_dn("uni/tn-OT/BD-servers", tool_ctx)
    assert obj["_class"] == "fvBD"
    assert obj["name"] == "servers"
    assert obj["dn"] == "uni/tn-OT/BD-servers"


@pytest.mark.asyncio
async def test_get_by_dn_not_found_returns_structured_error(tool_ctx):
    from main import get_by_dn

    result = await get_by_dn("uni/tn-OT/BD-doesNotExist", tool_ctx)
    assert result["found"] is False
    assert result["dn"] == "uni/tn-OT/BD-doesNotExist"
    assert "No object exists" in result["message"]


@pytest.mark.asyncio
async def test_get_by_dn_not_found_logs_warning(tool_ctx):
    from main import get_by_dn

    await get_by_dn("uni/tn-OT/BD-doesNotExist", tool_ctx)
    tool_ctx.warning.assert_called_once()


@pytest.mark.asyncio
async def test_get_by_dn_config_only_strips_operational_attrs(schemas_dir):
    from main import get_by_dn

    ctx = _stub_ctx(_CONFIG_IMDATA, schemas_dir)
    obj = await get_by_dn("uni/tn-OT/BD-servers", ctx, config_only=True)
    assert obj["name"] == "servers"
    assert "modTs" not in obj
    call = ctx.lifespan_context["backend"].calls[-1]
    assert call["config_only"] is True


@pytest.mark.asyncio
async def test_get_by_dn_include_children_embeds_children(tool_ctx):
    from main import get_by_dn

    # fvBD "mgmt" in sample_imdata carries a fvSubnet child
    obj = await get_by_dn("uni/tn-OT/BD-mgmt", tool_ctx, include_children=["fvSubnet"])
    assert obj["_class"] == "fvBD"
    assert "_children" in obj
    assert obj["_children"][0]["_class"] == "fvSubnet"


# ── count (Task 4) ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_plain_returns_total(tool_ctx):
    from main import count

    result = await count("fvBD", tool_ctx)
    # sample_imdata carries three fvBD objects
    assert result["class_name"] == "fvBD"
    assert result["count"] == 3
    assert result["scope_dn"] is None
    assert result["filters"] == {}


@pytest.mark.asyncio
async def test_count_filtered(tool_ctx):
    from main import count

    # two of the three BDs have arpFlood=no (servers, mgmt)
    result = await count("fvBD", tool_ctx, filters={"arpFlood": "no"})
    assert result["count"] == 2
    assert result["filters"] == {"arpFlood": "no"}


@pytest.mark.asyncio
async def test_count_scoped(tool_ctx):
    from main import count

    result = await count("fvBD", tool_ctx, scope_dn="uni/tn-OT")
    assert result["count"] == 3
    assert result["scope_dn"] == "uni/tn-OT"


@pytest.mark.asyncio
async def test_count_unknown_class_raises_unknown_class_error(tool_ctx):
    from main import count

    with pytest.raises(UnknownClassError) as exc_info:
        await count("xyzTotallyFakeClass99", tool_ctx)
    assert exc_info.value.class_name == "xyzTotallyFakeClass99"


@pytest.mark.asyncio
async def test_count_unknown_class_logs_warning(tool_ctx):
    from main import count

    with pytest.raises(UnknownClassError):
        await count("xyzFakeClass", tool_ctx)
    tool_ctx.warning.assert_called_once()


@pytest.mark.asyncio
async def test_count_allows_class_absent_from_descriptions_but_with_schema(
    sample_imdata, tmp_path
):
    """count() must agree with query() on the registry/schema fallback — a
    class with a schema file but no class-descriptions entry is allowed
    through instead of raising UnknownClassError."""
    from main import count

    (tmp_path / "aaaExtraOnlyClass.json").write_text(
        json.dumps(_EXTRA_ONLY_SCHEMA), encoding="utf-8"
    )
    ctx = _stub_ctx(sample_imdata, tmp_path, descriptions=dict(MINIMAL_DESCRIPTIONS))

    result = await count("aaaExtraOnlyClass", ctx)
    assert result["class_name"] == "aaaExtraOnlyClass"


