# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Performance tests for registry.schema.load_schema().

load_schema() is called on every get_schema() tool invocation (lazy loading).
Each file read should be fast since files are small (~20 KB) and modern OSes
cache them in the page cache after the first read.

Thresholds:
  cold load of a single schema   < 5 ms
  200 distinct cold schema loads < 500 ms total
  re-reading the same file 100×  < 100 ms (page cache warm)
"""

import time

from registry.schema import load_schema
from tests.perf.conftest import _make_class_name


def test_single_cold_schema_load(large_schema_dir):
    cls = _make_class_name(0)
    t0 = time.perf_counter()
    schema = load_schema(cls, large_schema_dir)
    elapsed = time.perf_counter() - t0

    assert schema != {}, f"Expected schema for {cls}"
    assert elapsed < 0.005, (
        f"Cold schema load took {elapsed * 1000:.1f}ms — must be < 5ms"
    )


def test_200_distinct_cold_schema_loads(large_schema_dir):
    t0 = time.perf_counter()
    loaded = 0
    for i in range(200):
        cls = _make_class_name(i)
        schema = load_schema(cls, large_schema_dir)
        if schema:
            loaded += 1
    elapsed = time.perf_counter() - t0

    assert loaded == 200, f"Only loaded {loaded}/200 schemas"
    assert elapsed < 0.500, (
        f"200 cold schema loads took {elapsed:.3f}s — must be < 500ms"
    )


def test_repeated_load_of_same_schema_is_fast(large_schema_dir):
    """OS page cache should make repeated reads of the same file very fast."""
    cls = _make_class_name(0)
    load_schema(cls, large_schema_dir)  # warm the cache

    t0 = time.perf_counter()
    for _ in range(100):
        load_schema(cls, large_schema_dir)
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.100, (
        f"100 warm schema reads took {elapsed:.3f}s — must be < 100ms"
    )


def test_missing_schema_lookup_is_fast(large_schema_dir):
    """Unknown class (file not found) should return quickly without scanning."""
    t0 = time.perf_counter()
    for _ in range(1_000):
        result = load_schema("nonExistentClassXYZ999", large_schema_dir)
        assert result == {}
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, (
        f"1000 missing schema lookups took {elapsed:.3f}s — must be < 1s"
    )


def test_schema_parsing_produces_correct_structure(large_schema_dir):
    """Verify that perf-grade synthetic schemas parse into the expected shape."""
    cls = _make_class_name(0)
    schema = load_schema(cls, large_schema_dir)

    assert "identifiedBy" in schema
    assert "containedBy" in schema
    assert isinstance(schema["containedBy"], list)
    assert isinstance(schema["properties"], list)
    assert schema["properties"] == sorted(schema["properties"])
