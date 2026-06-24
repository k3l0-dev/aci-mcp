# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Performance tests for registry.descriptions.search().

Validates that keyword search over a 15 k-class registry stays within
acceptable latency bounds for single and repeated calls.

Thresholds (on a modern laptop, no parallelism):
  single search over 15k classes  < 200 ms
  100 consecutive searches         < 2 s total
"""

import time

from registry.descriptions import search


class TestSearchPerf:
    def test_single_search_15k_classes(self, large_descriptions):
        t0 = time.perf_counter()
        results = search("fabric", large_descriptions, limit=10)
        elapsed = time.perf_counter() - t0

        assert len(results) > 0, "Expected at least one match for 'fabric'"
        assert elapsed < 0.200, (
            f"search() over 15k classes took {elapsed:.3f}s — must be < 200ms"
        )

    def test_100_consecutive_searches(self, large_descriptions):
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
        t0 = time.perf_counter()
        for i in range(100):
            search(keywords[i % len(keywords)], large_descriptions, limit=10)
        elapsed = time.perf_counter() - t0

        assert elapsed < 2.0, (
            f"100 searches over 15k classes took {elapsed:.3f}s — must be < 2s"
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

        # Miss should be in the same ballpark — not more than 3× slower
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
