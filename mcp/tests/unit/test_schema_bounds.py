# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""`get_schema` must not be able to blow an agent's context window.

`dnFormats` and `containedBy` are unbounded in the ACI object model: a class
that can hang off almost any MO enumerates one entry per possible parent. Seven
classes exceed 1 MB of JSON and `faultDelegate` reaches 7.8 MB across 64,313
dnFormats — roughly 2M tokens for a single tool call.

They are not obscure classes. `search_classes("fault")` returns `faultCounts`
at rank 1 and `search_classes("fault instance")` returns `faultInst` at rank 1,
so the two-call workflow the tool documentation prescribes walks straight into
it.

These tests pin the bound, the marker that discloses it, the ceiling on the
opt-in, and — just as important — that the 15,445 classes below the limit come
back untouched.
"""

from __future__ import annotations

import json

import pytest

from niwashi_mcp.main import (
    _SCHEMA_LIST_MAX,
    _SCHEMA_LIST_SAMPLE,
    _bound_schema_lists,
    get_schema,
)
from niwashi_mcp.registry import catalog

# The classes whose raw projection exceeds 1 MB, with their true dnFormats
# counts as measured against the shipped catalogue. Hard-coded on purpose: if a
# catalogue update changes them the test should be looked at, not silently pass.
OVERSIZED = {
    "faultDelegate": 64_313,
    "tagAnnotation": 42_098,
    "tagTag": 42_070,
    "aaaRbacAnnotation": 41_926,
    "healthInst": 31_279,
    "faultCounts": 31_271,
    "faultInst": 24_151,
}

# Classes comfortably under the sample size, which must be unaffected.
NORMAL = ("fvBD", "fvTenant", "fvAEPg", "fvSubnet", "l3extOut")


class TestTheHelperInIsolation:
    """`_bound_schema_lists` — pure, no catalogue, no async."""

    def test_truncates_and_records_the_total(self):
        schema = {"dnFormats": [f"dn-{i}" for i in range(100)], "containedBy": []}
        out = _bound_schema_lists(schema, 10)
        assert out["dnFormats"] == [f"dn-{i}" for i in range(10)]
        assert out["dnFormatsTruncated"]["returned"] == 10
        assert out["dnFormatsTruncated"]["total"] == 100

    def test_keeps_the_prefix_not_an_arbitrary_slice(self):
        """The sample must be the head of the list, so it is reproducible."""
        schema = {"dnFormats": ["a", "b", "c", "d"]}
        assert _bound_schema_lists(schema, 2)["dnFormats"] == ["a", "b"]

    def test_a_list_at_the_limit_is_left_alone(self):
        """Boundary: exactly `limit` entries is not truncation."""
        schema = {"dnFormats": ["a", "b", "c"]}
        out = _bound_schema_lists(schema, 3)
        assert out["dnFormats"] == ["a", "b", "c"]
        assert "dnFormatsTruncated" not in out

    def test_a_short_list_gains_no_marker(self):
        """No marker means the common class is byte-identical to before."""
        out = _bound_schema_lists({"dnFormats": ["only"], "containedBy": ["fv:Tenant"]}, 25)
        assert out == {"dnFormats": ["only"], "containedBy": ["fv:Tenant"]}

    def test_both_lists_are_bounded_independently(self):
        schema = {"dnFormats": ["d"] * 50, "containedBy": ["c"] * 3}
        out = _bound_schema_lists(schema, 10)
        assert out["dnFormatsTruncated"]["total"] == 50
        assert "containedByTruncated" not in out
        assert out["containedBy"] == ["c"] * 3

    def test_the_note_counts_the_right_thing_for_each_key(self):
        """A class with 24,151 dnFormats has 1,895 parents.

        Wording the dnFormats note as "parents" would state a number the agent
        cannot check and that is simply false.
        """
        out = _bound_schema_lists({"dnFormats": ["d"] * 50, "containedBy": ["c"] * 50}, 5)
        assert "DN patterns" in out["dnFormatsTruncated"]["note"]
        assert "parent classes" in out["containedByTruncated"]["note"]
        assert "parent classes" not in out["dnFormatsTruncated"]["note"]

    def test_the_note_points_at_the_way_out(self):
        """An agent that needs more must be able to learn how from the payload."""
        note = _bound_schema_lists({"dnFormats": ["d"] * 50}, 5)["dnFormatsTruncated"]["note"]
        assert "list_limit" in note
        assert str(_SCHEMA_LIST_MAX) in note

    def test_the_dn_note_redirects_to_rnformat(self):
        """The sample loses nothing actionable — say where the signal actually is."""
        note = _bound_schema_lists({"dnFormats": ["d"] * 50}, 5)["dnFormatsTruncated"]["note"]
        assert "rnFormat" in note

    @pytest.mark.parametrize("value", [None, "not-a-list", 42, {"a": 1}])
    def test_non_list_values_are_stepped_over(self, value):
        """Defensive: a projection change must not turn into a TypeError."""
        assert _bound_schema_lists({"dnFormats": value}, 5)["dnFormats"] == value

    def test_a_missing_key_is_not_invented(self):
        assert _bound_schema_lists({}, 5) == {}


@pytest.mark.asyncio
class TestThroughTheTool:
    """What an agent actually receives."""

    @pytest.mark.parametrize("class_name,total", OVERSIZED.items())
    async def test_oversized_classes_are_bounded(self, tool_ctx, class_name, total):
        schema = await get_schema(class_name, tool_ctx)
        assert len(schema["dnFormats"]) == _SCHEMA_LIST_SAMPLE
        assert schema["dnFormatsTruncated"]["total"] == total

    @pytest.mark.parametrize("class_name", OVERSIZED)
    async def test_no_oversized_class_exceeds_a_sane_payload(self, tool_ctx, class_name):
        """The bound in the terms that matter: bytes on the wire.

        10 KB is ~2,500 tokens — an ordinary tool result. Before the bound these
        same classes ranged from 2.6 MB to 7.8 MB.
        """
        size = len(json.dumps(await get_schema(class_name, tool_ctx)))
        assert size < 10_000, f"{class_name} still returns {size:,} bytes"

    @pytest.mark.parametrize("class_name", NORMAL)
    async def test_normal_classes_are_untouched(self, tool_ctx, class_name):
        """The bound must be invisible to the 15,445 classes that never hit it."""
        schema = await get_schema(class_name, tool_ctx)
        raw = catalog.load_schema(class_name)
        assert schema["dnFormats"] == raw["dnFormats"]
        assert schema["containedBy"] == raw["containedBy"]
        assert "dnFormatsTruncated" not in schema
        assert "containedByTruncated" not in schema

    async def test_the_documented_agent_workflow_stays_affordable(self, tool_ctx):
        """The exact path that made this a blocker.

        `search_classes("fault")` ranks `faultCounts` first, and the tool
        documentation tells the agent to call `get_schema` on it next. That
        second call used to return 3.17 MB.
        """
        from niwashi_mcp.main import search_classes

        hits = await search_classes("fault", tool_ctx, limit=3)
        assert hits[0]["class_name"] == "faultCounts", "fixture drifted; pick another keyword"
        total = 0
        for hit in hits:
            total += len(json.dumps(await get_schema(hit["class_name"], tool_ctx)))
        assert total < 20_000, f"search+schema round trip costs {total:,} bytes"

    async def test_the_optin_returns_more(self, tool_ctx):
        schema = await get_schema("faultDelegate", tool_ctx, list_limit=200)
        assert len(schema["dnFormats"]) == 200
        assert schema["dnFormatsTruncated"]["returned"] == 200

    async def test_the_optin_is_capped(self, tool_ctx):
        """The escape hatch must not restore the failure mode it exists beside."""
        schema = await get_schema("faultDelegate", tool_ctx, list_limit=10_000_000)
        assert len(schema["dnFormats"]) == _SCHEMA_LIST_MAX
        assert len(json.dumps(schema)) < 150_000

    @pytest.mark.parametrize("limit", [0, -1, -10_000])
    async def test_a_nonsense_limit_floors_at_one(self, tool_ctx, limit):
        """Never an empty list: an agent given [] would conclude "no DN pattern"."""
        schema = await get_schema("faultDelegate", tool_ctx, list_limit=limit)
        assert len(schema["dnFormats"]) == 1

    async def test_truncation_does_not_disturb_the_other_fields(self, tool_ctx):
        """Only the two bounded keys and their markers may differ from the raw
        projection — the bound is a presentation concern, not a data change."""
        bounded = await get_schema("faultInst", tool_ctx)
        raw = catalog.load_schema("faultInst")
        changed = {k for k in raw if bounded.get(k) != raw[k]}
        assert changed == {"dnFormats", "containedBy"}
        assert set(bounded) - set(raw) == {"dnFormatsTruncated", "containedByTruncated"}

    async def test_an_unknown_class_still_returns_empty(self, tool_ctx):
        """The bound must not turn a miss into a dict carrying markers."""
        assert await get_schema("fvNotARealClass", tool_ctx) == {}


@pytest.mark.asyncio
async def test_no_class_in_the_catalogue_exceeds_the_bound(tool_ctx):
    """The invariant, asserted over all 15,452 classes rather than a sample.

    A per-class assertion is what catches the next `faultDelegate` — a catalogue
    update introducing a new universal class would otherwise ship unnoticed.
    """
    oversized = []
    for (class_name,) in catalog._connect().execute("SELECT class_name FROM mo"):
        schema = await get_schema(class_name, tool_ctx)
        if len(schema.get("dnFormats") or []) > _SCHEMA_LIST_SAMPLE:
            oversized.append(class_name)
        if len(schema.get("containedBy") or []) > _SCHEMA_LIST_SAMPLE:
            oversized.append(class_name)
    assert not oversized, f"{len(oversized)} classes escaped the bound: {oversized[:10]}"
