"""
Integration tests for the three MCP tools: search_classes, get_schema, query.

Uses a StubBackend with sample_imdata to avoid any live APIC connection.
The FastMCP Context is replaced with a simple stub so the tool functions
can be called directly without spinning up an HTTP server.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from registry.descriptions import load_descriptions

# ── Stub backend ──────────────────────────────────────────────────────────────


class StubBackend:
    """Minimal in-memory backend for tool tests."""

    def __init__(self, imdata: list[dict]):
        self._data = imdata

    async def query_class(
        self,
        class_name: str,
        filters: dict[str, str],
        scope_dn: str,
        limit: int,
        order_by: str,
        include_children: list[str] | None = None,
        filter_expr: str | None = None,
        rsp_subtree_include: str | None = None,
        time_range: str | None = None,
        page: int | None = None,
    ) -> list[dict[str, Any]]:
        results = []
        for item in self._data:
            obj = item.get(class_name)
            if obj is None:
                continue
            attrs = dict(obj.get("attributes", {}))
            attrs["_class"] = class_name
            results.append(attrs)

        if scope_dn:
            results = [
                o for o in results
                if o.get("dn") == scope_dn or o.get("dn", "").startswith(scope_dn + "/")
            ]

        for attr, val in filters.items():
            results = [o for o in results if o.get(attr) == val]

        if order_by:
            parts = order_by.split("|")
            attr_key = parts[0].split(".")[-1]
            reverse = len(parts) > 1 and parts[1].lower() == "desc"
            results.sort(key=lambda o: o.get(attr_key, ""), reverse=reverse)

        return results[:limit]

    async def close(self) -> None:
        pass


# ── Context stub ──────────────────────────────────────────────────────────────


def _make_ctx(lifespan_ctx: dict):
    """Minimal stand-in for FastMCP Context."""
    ctx = SimpleNamespace()
    ctx.lifespan_context = lifespan_ctx
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    return ctx


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def schemas_dir():
    return Path(__file__).parent.parent.parent / "data" / "schemas"


@pytest.fixture
def descriptions_file():
    return Path(__file__).parent.parent.parent / "data" / "class-descriptions.json"


@pytest.fixture
def tool_ctx(sample_imdata, schemas_dir, descriptions_file):
    """Lifespan context injected into tool calls."""
    if descriptions_file.exists():
        descriptions = load_descriptions(descriptions_file)
    else:
        descriptions = {
            "fvBD": {"label": "Bridge Domain", "comment": "A bridge domain."},
            "fvTenant": {"label": "Tenant", "comment": "A tenant."},
        }
    return _make_ctx(
        {
            "descriptions": descriptions,
            "backend": StubBackend(sample_imdata),
            "schemas_dir": schemas_dir,
        }
    )


# ── search_classes ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_classes_returns_results(tool_ctx):
    from main import search_classes

    results = await search_classes("bridge", tool_ctx)
    assert isinstance(results, list)
    assert len(results) > 0


@pytest.mark.asyncio
async def test_search_classes_result_fields(tool_ctx):
    from main import search_classes

    results = await search_classes("tenant", tool_ctx)
    for r in results:
        assert "class_name" in r
        assert "label" in r
        assert "comment" in r


@pytest.mark.asyncio
async def test_search_classes_limit_respected(tool_ctx):
    from main import search_classes

    results = await search_classes("a", tool_ctx, limit=3)
    assert len(results) <= 3


@pytest.mark.asyncio
async def test_search_classes_no_match_returns_empty(tool_ctx):
    from main import search_classes

    results = await search_classes("zzz_nonexistent_xyz_abc", tool_ctx)
    assert results == []


# ── get_schema ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(
    not Path(__file__).parent.parent.parent.joinpath("data", "schemas").exists(),
    reason="schemas/ collection not available",
)
async def test_get_schema_known_class(tool_ctx):
    from main import get_schema

    schema = await get_schema("fvBD", tool_ctx)
    assert schema != {}
    assert "identifiedBy" in schema


@pytest.mark.asyncio
async def test_get_schema_unknown_class_returns_empty(tool_ctx):
    from main import get_schema

    schema = await get_schema("nonExistentClassXYZ", tool_ctx)
    assert schema == {}


# ── query ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_query_returns_list(tool_ctx):
    from main import query

    results = await query("fvTenant", tool_ctx)
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_query_class_key_present(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx)
    assert all("_class" in r for r in results)
    assert all(r["_class"] == "fvBD" for r in results)


@pytest.mark.asyncio
async def test_query_with_filters(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, filters={"name": "servers"})
    assert len(results) == 1
    assert results[0]["name"] == "servers"


@pytest.mark.asyncio
async def test_query_with_scope_dn(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, scope_dn="uni/tn-OT")
    assert len(results) == 2
    assert all(r["dn"].startswith("uni/tn-OT/") for r in results)


@pytest.mark.asyncio
async def test_query_limit_capped_at_200(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=500)
    assert len(results) <= 200


@pytest.mark.asyncio
async def test_query_limit_applied(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, limit=1)
    assert len(results) == 1


@pytest.mark.asyncio
async def test_query_unknown_class_returns_empty(tool_ctx):
    from main import query

    results = await query("fabricNode", tool_ctx)
    assert results == []


@pytest.mark.asyncio
async def test_query_order_by_asc(tool_ctx):
    from main import query

    results = await query("fvBD", tool_ctx, order_by="fvBD.name|asc")
    names = [r["name"] for r in results]
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_query_none_filters_treated_as_empty(tool_ctx):
    from main import query

    results_none = await query("fvTenant", tool_ctx, filters=None)
    results_empty = await query("fvTenant", tool_ctx, filters={})
    assert len(results_none) == len(results_empty)
