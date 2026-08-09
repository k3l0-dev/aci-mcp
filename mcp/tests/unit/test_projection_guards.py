# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
The filters and priors that shape what an agent receives — pinned.

Each guard below is correct today and was, until this file, invisible to the
suite: an audit's mutation pass removed each in turn and every one of the 539
tests stayed green. They are not defects. They are decisions with no evidence
attached, which is how a decision becomes an accident during a refactor.

Two are projection filters in `registry.catalog`, deciding what `get_schema`
puts in an agent's context. One is a structural prior in the search scorer,
carrying 31.3 % of the index. All three are cheap to assert and expensive to
lose quietly — losing them produces a plausible answer, not an error.
"""

from __future__ import annotations

import re

import pytest

from niwashi_mcp.registry import catalog
from niwashi_mcp.registry.descriptions import search

pytestmark = pytest.mark.catalog

_STATS_SUFFIX = re.compile(r"(?:5min|15min|1h|1d|1w|1mo|1qtr|1year)$")


class TestProjectionFilters:
    """What `get_schema` refuses to put in front of an agent."""

    def test_an_empty_default_is_omitted_rather_than_shown(self):
        """`"default": ""` is noise that reads as information.

        86 properties across 57 classes store an empty string as their default.
        Emitting the key tells an agent the schema declares a default when it
        declares nothing — and for a value it may then try to filter on. The
        catalogue's filter drops them; nothing asserted it, so removing the
        filter added 86 keys to `property_details` with a green suite.
        """
        stored = catalog._query(
            "SELECT m.class_name, p.wire_name FROM prop p JOIN mo m ON m.id = p.class_id "
            "WHERE p.default_val IN ('\"\"', '[]', '{}')"
        )
        assert stored, "the corpus no longer has empty defaults — re-scope this test"

        for cls, wire in stored:
            details = catalog.load_schema(cls, include_property_details=True)
            detail = details.get("property_details", {}).get(wire)
            if detail is None:
                continue
            assert "default" not in detail, (
                f"{cls}.{wire} exposes an empty default — an agent reads that as "
                f"'the schema declares one' when it declares nothing"
            )

    def test_the_default_value_marker_never_reaches_options(self):
        """Enum blobs carry a `defaultValue` marker entry whose `localName` is
        not a value the APIC accepts. Present on ~90 % of enums, so an agent
        filtering on it gets an empty result and no error."""
        schema = catalog.load_schema("fvBD", include_property_details=True)
        for name, detail in schema["property_details"].items():
            assert "defaultValue" not in detail.get("options", []), (
                f"fvBD.{name} offers 'defaultValue' as a settable value"
            )

    def test_the_null_comment_sentinel_never_reaches_a_comment(self):
        """`comment_pool` stores the literal string "null" for "no comment" on
        4,463 rows. Passed through, an agent reads the word as documentation."""
        for cls in ("fvBD", "fvAEPg", "fvTenant", "l3extOut", "vzBrCP"):
            schema = catalog.load_schema(cls, include_property_details=True)
            for name, detail in schema["property_details"].items():
                assert detail.get("comment") != "null", f"{cls}.{name}"


class TestStructuralPriors:
    """The scoring adjustments that decide what a keyword actually surfaces."""

    def test_the_stats_penalty_keeps_telemetry_classes_off_the_top(self):
        """31.3 % of the index carries a time-bucket suffix.

        4,769 of 15,239 entries are `…5min`, `…1h`, `…1year` telemetry holders.
        They exist to accumulate counters for another class and can never be the
        answer to "which class do I query for X". The -10 prior is the only
        thing keeping them out of the top of a functional query, and the whole
        evaluation stack was blind to it: not one of the 74 golden queries
        surfaces a stats class, so removing the penalty changed no metric.
        """
        index = catalog.descriptions_index()
        stats = [c for c in index if _STATS_SUFFIX.search(c)]
        assert len(stats) > 4_000, f"only {len(stats)} stats classes — re-scope this test"

        # Terms whose plain text matches telemetry classes strongly. Without the
        # penalty these are exactly the queries that return a counter holder.
        for keyword in ("ingress bytes", "egress packets", "health score"):
            top = [r["class_name"] for r in search(keyword, index, limit=5)]
            assert top, f"{keyword!r} returned nothing"
            penalised = [c for c in top if _STATS_SUFFIX.search(c)]
            assert not penalised, (
                f"{keyword!r} surfaced telemetry classes in its top 5: {penalised}. "
                f"They hold counters for another class and cannot be what the "
                f"question is asking for."
            )

    def test_abstract_classes_are_penalised_not_hidden(self):
        """An abstract class can never be instantiated, so it is never the
        answer to "which class do I query" — but it is still a real part of the
        model, so it is pushed down rather than filtered out."""
        index = catalog.descriptions_index()
        abstract = {c for c, m in index.items() if m.get("isAbstract")}
        assert abstract, "no abstract classes in the index — re-scope this test"

        # Penalised, not excluded: the -6 prior demotes, it does not filter, so
        # the assertion is about ORDER rather than membership.
        #
        # The terms below are chosen because the prior is load-bearing for them,
        # which took measuring: for "bridge domain" fvBD leads on an exact jargon
        # match whether the penalty exists or not, so a test built on that query
        # asserts nothing. Removing the penalty flips exactly these two —
        # "interface" poeIf → ipIf and "domain" bgpDom → l2Dom, both abstract.
        for keyword in ("interface", "domain"):
            top = [r["class_name"] for r in search(keyword, index, limit=5)]
            assert top, f"{keyword!r} returned nothing"
            assert top[0] not in abstract, (
                f"{keyword!r} led with the abstract class {top[0]}. An abstract "
                f"class cannot be instantiated, so it is never the answer to "
                f"'which class do I query'."
            )

        # …and still reachable when named directly, which is the other half of
        # "penalised, not hidden".
        name = next(iter(sorted(abstract)))
        assert any(r["class_name"] == name for r in search(name, index, limit=5))

    def test_relation_classes_are_penalised(self):
        """Rs/Rt objects are internal plumbing — structurally never the primary
        target of a user's question, even when their name matches best."""
        index = catalog.descriptions_index()
        top = [r["class_name"] for r in search("bridge domain", index, limit=3)]
        assert not [c for c in top if re.match(r"^[a-z][a-z0-9]*(?:Rs|Rt)[A-Z]", c)], (
            f"a relation class led a functional query: {top}"
        )
