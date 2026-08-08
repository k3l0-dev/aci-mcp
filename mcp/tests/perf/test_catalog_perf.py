# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Performance tests for registry.catalog — the 2.0 data layer.

Why this file exists
--------------------
Until 2.0 the schema path was `registry/schema.py`, and `tests/perf/` measured
it. The 2.0 migration deleted that reader and replaced it with SQLite, which
changed the cost profile completely — and nothing replaced the measurement. The
orphaned fixtures survived for a while and were removed; the gap they left is
what this file closes.

What these actually protect
---------------------------
Three decisions in `registry/catalog.py` are load-bearing for latency and
memory, and all three are one deleted decorator away from silently reverting:

1. `_connect()` is `lru_cache(maxsize=1)`. Its docstring says a second
   connection "would load a second copy of the string pools (26,654 labels +
   25,411 comments) into memory". Losing the cache also means opening the
   database on every single call.
2. `_pool()` and `_pool_blob()` are cached. Without them every label, comment
   and blob costs a round trip, and `descriptions_index()` — which runs once at
   startup over 15,452 classes — becomes the startup.
3. `property_details` is opt-in. `get_schema`'s docstring tells an agent to
   prefer `properties_filter` "to protect the token budget"; the cost side of
   that argument has never been measured.

How they are asserted
---------------------
Structurally where possible, by ratio where not. A wall-clock constant
calibrated on a laptop is a test that passes on a laptop — the search perf
suite already learned that the hard way (see test_search_perf.py). The one
absolute ceiling here is a generous sanity bound, not a target.
"""

import time

import pytest

from niwashi_mcp.registry import catalog

pytestmark = pytest.mark.catalog


# A spread across packages and shapes: a small leaf, a heavily-propertied
# class, a relation, an abstract class, and the root. Chosen so no single
# storage path dominates the measurement.
_SAMPLE = [
    "fvBD",
    "fvAEPg",
    "fvTenant",
    "fvSubnet",
    "fvCtx",
    "fvRsCtx",
    "l3extOut",
    "vzBrCP",
    "vzEntry",
    "fabricNode",
    "l1PhysIf",
    "polUni",
]


def _warm() -> None:
    """Open the connection and populate the pools, so later calls measure reads."""
    catalog.load_schema("fvBD")


class TestCatalogPerf:
    def test_the_connection_is_opened_once_however_many_calls(self):
        """Structural, so no hardware can mask it.

        `lru_cache(maxsize=1)` on `_connect` is the only thing keeping one
        SQLite handle — and therefore one copy of the string pools — for the
        life of the process. Remove it and this jumps to one miss per call.
        """
        _warm()
        before = catalog._connect.cache_info().misses
        for name in _SAMPLE:
            catalog.load_schema(name)
        after = catalog._connect.cache_info()

        assert after.misses == before, (
            f"_connect() missed its cache {after.misses - before} more times across "
            f"{len(_SAMPLE)} schema loads — every miss opens a second SQLite "
            f"connection and a second copy of the string pools"
        )
        assert after.currsize == 1

    def test_the_pools_are_cached_across_classes(self):
        """Pooled labels and comments are shared; reading them twice must not cost twice.

        Expressed as a hit *ratio* rather than a count, because the number of
        lookups depends on how many properties the sampled classes carry, which
        is a property of the corpus and not of this code.
        """
        _warm()
        for name in _SAMPLE:
            catalog.load_schema(name, include_property_details=True)
        info = catalog._pool.cache_info()

        total = info.hits + info.misses
        assert total > 0, "no pool lookups happened — the sample is not exercising them"
        assert info.hits / total > 0.5, (
            f"pool cache hit ratio {info.hits / total:.1%} — pooled strings are being "
            f"re-read from SQLite instead of reused"
        )

    def test_property_details_is_the_expensive_path(self):
        """The measurement behind an opt-in.

        `get_schema` defaults `include_property_details` to False and the
        docstring justifies it on token budget alone. It is also the slower
        path — projecting constraints for every property decodes an enum blob
        and joins a comment per property. A ratio, so it holds anywhere.
        """
        _warm()
        # fvAEPg carries enough properties for the difference to be structural
        # rather than noise.
        for _ in range(3):
            catalog.load_schema("fvAEPg")
            catalog.load_schema("fvAEPg", include_property_details=True)

        t0 = time.perf_counter()
        for _ in range(20):
            catalog.load_schema("fvAEPg")
        plain = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(20):
            catalog.load_schema("fvAEPg", include_property_details=True)
        detailed = time.perf_counter() - t0

        assert detailed > plain, (
            "property_details is not measurably more expensive than the plain "
            "schema — either the projection stopped happening, or the plain path "
            "started doing it anyway"
        )

    def test_a_filtered_projection_costs_less_than_the_full_one(self):
        """`properties_filter` is the token-efficient path; it must also be the cheap one.

        This is what makes the advice in `get_schema`'s docstring true rather
        than merely well-intentioned.
        """
        _warm()
        for _ in range(3):
            catalog.load_schema("fvAEPg", include_property_details=True)
            catalog.load_schema("fvAEPg", properties_filter=["name", "descr"])

        t0 = time.perf_counter()
        for _ in range(20):
            catalog.load_schema("fvAEPg", include_property_details=True)
        full = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(20):
            catalog.load_schema("fvAEPg", properties_filter=["name", "descr"])
        filtered = time.perf_counter() - t0

        assert filtered < full, (
            "asking for two properties costs as much as asking for all of them — "
            "properties_filter is projecting more than it was asked for"
        )

    def test_a_schema_load_is_an_indexed_lookup_not_a_scan(self):
        """A generous ceiling, as a sanity bound rather than a target.

        `mo.class_name` is looked up directly. If that ever degraded to a table
        scan over 15,452 rows, the median would move by orders of magnitude —
        which is the only thing this bound is sized to catch. Measured warm:
        well under 1 ms locally.
        """
        _warm()
        timings = []
        for name in _SAMPLE:
            t0 = time.perf_counter()
            catalog.load_schema(name)
            timings.append(time.perf_counter() - t0)

        timings.sort()
        median = timings[len(timings) // 2]
        assert median < 0.050, (
            f"median schema load {median * 1000:.1f} ms — sized to catch a scan, "
            f"not to police milliseconds"
        )

    def test_an_unknown_class_is_rejected_without_touching_the_pools(self):
        """The miss path is the cheap one, and stays cheap.

        `load_schema` returns `{}` for an unknown class before any pool lookup
        or blob decode. An agent that guesses class names hits this constantly.
        """
        _warm()
        before = catalog._pool.cache_info()
        for i in range(50):
            assert catalog.load_schema(f"zzzNoSuchClass{i}") == {}
        after = catalog._pool.cache_info()

        assert after.misses == before.misses and after.hits == before.hits, (
            "a failed class lookup performed a pool read — the miss path is doing "
            "work before it establishes the class exists"
        )
