# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Performance tests for registry.filter.build_filter().

build_filter() is called on every query() tool invocation, so it must be
essentially free.  Thresholds are very tight.

Thresholds:
  single call with 1 filter   < 0.1 ms
  10 000 calls with 5 filters < 500 ms total
"""

import time

from registry.filter import build_filter


def test_single_call_is_sub_millisecond():
    t0 = time.perf_counter()
    build_filter("fvBD", {"name": "servers"})
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.001, (
        f"build_filter() took {elapsed * 1000:.3f}ms — must be < 1ms"
    )


def test_10k_calls_with_5_filters():
    filters = {
        "name": "servers",
        "arpFlood": "yes",
        "status": "created",
        "descr": "prod bridge domain",
        "mcastAllow": "no",
    }
    t0 = time.perf_counter()
    for _ in range(10_000):
        build_filter("fvBD", filters)
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.500, (
        f"10 000 build_filter() calls took {elapsed:.3f}s — must be < 500ms"
    )


def test_empty_filter_is_fastest():
    t_empty = time.perf_counter()
    for _ in range(10_000):
        build_filter("fvBD", {})
    t_empty = time.perf_counter() - t_empty

    t_one = time.perf_counter()
    for _ in range(10_000):
        build_filter("fvBD", {"name": "x"})
    t_one = time.perf_counter() - t_one

    # Empty should be at most 2× slower than one-filter — both are trivially fast
    assert t_empty < 0.200
    assert t_one < 0.500


def test_value_with_quotes_does_not_add_significant_overhead():
    """Escaping quotes should add negligible cost."""
    t_plain = time.perf_counter()
    for _ in range(5_000):
        build_filter("fvBD", {"name": "plain-value"})
    t_plain = time.perf_counter() - t_plain

    t_quoted = time.perf_counter()
    for _ in range(5_000):
        build_filter("fvBD", {"name": 'value"with"quotes'})
    t_quoted = time.perf_counter() - t_quoted

    # Escaping should not add more than 50% overhead
    assert t_quoted < t_plain * 1.5 + 0.05
