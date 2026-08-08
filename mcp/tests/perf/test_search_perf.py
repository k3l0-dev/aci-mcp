# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Performance tests for registry.descriptions.search().

What these actually protect
---------------------------
`search()` keeps a single-slot cache of the tokenised index, keyed on the
*identity* of the descriptions dict. `main.py` builds that dict exactly once in
the lifespan, deliberately, because losing the cache would re-tokenise 15 k+
entries on every call and turn a ~15 ms search into a ~400 ms one. That
regression — roughly 25x — is the one worth a test.

Why the wall-clock budgets were wrong
-------------------------------------
Until 2.0 these thresholds were calibrated "on a modern laptop", and the CI
workflow excluded `tests/perf` entirely, so they had never run on CI hardware.
The release pipeline runs the full suite, and a shared 2-core runner is 2–4x
slower: the single-search assertion measured 0.426 s against a 200 ms budget.

Worse, that test measured the *cold* path — the first call, which builds the
index — and called the result "search latency". Production never pays that per
search; it pays it once at startup. So the tests now measure the warm path,
which is what an agent's queries actually cost, and the cache itself is pinned
by a ratio rather than a constant, since a ratio holds on any machine.

Absolute budgets that remain are deliberately generous sanity ceilings, not
performance targets. Measured warm: ~11 ms locally, ~25 ms on a GitHub runner.
"""

import time

from niwashi_mcp.registry.descriptions import search


def _warm(descriptions) -> None:
    """Force the tokenised index to be built, so later calls measure search."""
    search("warmup", descriptions, limit=1)


class TestSearchPerf:
    def test_the_tokenised_index_is_cached_across_calls(self, large_descriptions):
        """The regression that matters, expressed so hardware cannot mask it.

        A fresh dict identity misses the single-slot cache and rebuilds; the
        same dict hits it. If the cache were lost, both would cost the same and
        the ratio would collapse to ~1. Measured ratio is ~27x locally and
        ~17x on CI, so a threshold of 3 has room on both while still failing
        loudly the moment every call starts rebuilding.
        """
        fresh = dict(large_descriptions)  # same content, new identity → miss

        t0 = time.perf_counter()
        search("fabric", fresh, limit=10)
        cold = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(20):
            search("fabric", fresh, limit=10)
        warm = (time.perf_counter() - t0) / 20

        assert cold > warm * 3, (
            f"the tokenised index is being rebuilt per call: first call {cold * 1000:.1f} ms, "
            f"subsequent {warm * 1000:.1f} ms — they should differ by an order of magnitude"
        )

    def test_single_search_15k_classes(self, large_descriptions):
        """Steady-state latency of one search — the warm path production runs."""
        _warm(large_descriptions)

        t0 = time.perf_counter()
        results = search("fabric", large_descriptions, limit=10)
        elapsed = time.perf_counter() - t0

        assert len(results) > 0, "Expected at least one match for 'fabric'"
        assert elapsed < 0.200, (
            f"warm search() over 15k classes took {elapsed:.3f}s — must be < 200ms"
        )

    def test_100_consecutive_searches(self, large_descriptions):
        """Repeated searches must stay linear and keep hitting the cache.

        The 6 s ceiling is a sanity bound: 100 warm searches measure ~1.1 s
        locally and ~2.5 s on a shared runner. It is set to catch a collapse
        (a lost cache costs ~40 s here), not to police a few milliseconds.
        """
        keywords = [
            "fabric",
            "tenant",
            "bd",
            "contract",
            "node",
            "policy",
            "subnet",
            "vrf",
            "epg",
            "fault",
            "path",
            "port",
        ]
        _warm(large_descriptions)

        t0 = time.perf_counter()
        for i in range(100):
            search(keywords[i % len(keywords)], large_descriptions, limit=10)
        elapsed = time.perf_counter() - t0

        assert elapsed < 6.0, (
            f"100 warm searches over 15k classes took {elapsed:.3f}s — must be < 6s"
        )

    def test_no_match_search_is_not_slower(self, large_descriptions):
        """A search that returns nothing should not be slower than one that matches."""
        t_miss = time.perf_counter()
        for _ in range(50):
            search("zzz_nonexistent_term_xyz", large_descriptions, limit=10)
        t_miss = time.perf_counter() - t_miss

        t_hit = time.perf_counter()
        for _ in range(50):
            search("fabric", large_descriptions, limit=10)
        t_hit = time.perf_counter() - t_hit

        # Miss should be in the same ballpark — not more than 3x slower
        assert t_miss < t_hit * 3 + 0.1, (
            f"No-match search ({t_miss:.3f}s) is disproportionately slower than "
            f"matching search ({t_hit:.3f}s)"
        )

    def test_search_result_count_scales_with_keyword_specificity(
        self, large_descriptions
    ):
        broad = search("a", large_descriptions, limit=1000)
        specific = search("fvBD", large_descriptions, limit=1000)
        # "a" appears in almost everything; "fvBD" should match fewer classes
        assert len(broad) >= len(specific)

    def test_limit_prevents_large_result_allocation(self, large_descriptions):
        """Enforcing limit=5 over 15k entries should still be fast."""
        t0 = time.perf_counter()
        results = search("a", large_descriptions, limit=5)
        elapsed = time.perf_counter() - t0

        assert len(results) == 5
        assert elapsed < 0.200
