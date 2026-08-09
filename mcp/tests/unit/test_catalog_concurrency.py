# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Concurrency tests for registry.catalog.

What these protect
------------------
`_connect()` returns ONE `sqlite3.Connection`, cached for the process, opened
with `check_same_thread=False`. That single connection is deliberate — a second
one loads a second copy of the string pools (26,654 labels + 25,411 comments)
into memory.

SQLite serialises its own internals, but `sqlite3.Connection` keeps a
per-connection prepared-statement cache that does not. Sharing the connection
across threads without a lock produced, measured on this catalogue:

    16 threads · 9,600 calls · 192 exceptions (2.0 %) · 333 (3.5 %) silently wrong

"Silently wrong" is the part that matters: a schema whose content differs from
the single-threaded reference **with no exception raised**. The caller receives
another class's schema and has no way to know. Everything else in this codebase
is built to stop an agent answering confidently about a fabric it misread; this
would have done exactly that, one `asyncio.to_thread` away.

Nothing in `src/` spawns threads today. These tests exist so that when something
does — a tool moving a catalogue read off the event loop, or a multi-worker
deployment — it is not a silent corruption.
"""

import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from niwashi_mcp.registry import catalog

pytestmark = pytest.mark.catalog

# Spread across packages and shapes, so no single storage path dominates and a
# cross-talk between two reads has dissimilar payloads to be caught on.
_CLASSES = [
    "fvBD", "fvAEPg", "fvTenant", "fvSubnet", "fvCtx", "fvRsCtx",
    "l3extOut", "vzBrCP", "vzEntry", "fabricNode", "l1PhysIf", "polUni",
]


def _reference() -> dict[str, dict]:
    """Single-threaded truth, taken before any thread is started."""
    return {c: catalog.load_schema(c, include_property_details=True) for c in _CLASSES}


class TestCatalogUnderThreads:
    def test_concurrent_load_schema_returns_the_single_threaded_result(self):
        """No exception, and no answer that belongs to another class.

        The assertion is content equality against the reference, not merely
        "did not raise" — the measured failure was 3.5 % of reads returning a
        different class's schema *without* raising. A test that only caught
        exceptions would have passed while the corruption happened.
        """
        expected = _reference()
        order = _CLASSES * 25  # 300 reads per worker's share

        def read(name: str) -> tuple[str, dict]:
            return name, catalog.load_schema(name, include_property_details=True)

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(read, order))

        assert len(results) == len(order)
        wrong = [n for n, got in results if got != expected[n]]
        assert not wrong, (
            f"{len(wrong)} of {len(results)} concurrent reads returned a schema that "
            f"differs from the single-threaded reference: {sorted(set(wrong))}"
        )

    def test_concurrent_mixed_reads_stay_consistent(self):
        """The other public readers share the same connection and the same risk."""
        expected_exists = {c: catalog.class_exists(c) for c in _CLASSES}
        expected_version = catalog.apic_version()

        def probe(name: str) -> tuple[str, bool, str]:
            return name, catalog.class_exists(name), catalog.apic_version()

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(probe, _CLASSES * 25))

        for name, exists, version in results:
            assert exists == expected_exists[name]
            assert version == expected_version

    def test_the_index_rebuild_is_safe_to_race_with_reads(self):
        """`descriptions_index()` sweeps all 15,452 rows; startup does it once,
        but nothing stops a second caller reading while it runs."""
        expected = _reference()

        def build(_: int) -> int:
            return len(catalog.descriptions_index())

        def read(_: int) -> bool:
            return all(
                catalog.load_schema(c, include_property_details=True) == expected[c]
                for c in _CLASSES
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            sizes = list(pool.map(build, range(2)))
            reads = list(pool.map(read, range(24)))

        assert set(sizes) == {15239}
        assert all(reads)


def test_no_statement_bypasses_the_lock():
    """Structural: every read must go through `_query`.

    The lock is only as good as the discipline around it, and
    `_connect().execute(...)` is the natural thing to write. This fails the
    moment one is reintroduced, which a timing test might not.
    """
    tree = ast.parse(Path(catalog.__file__).read_text())

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "execute"
                and node.name != "_query"
            ):
                offenders.append(f"{node.name}() line {call.lineno}")

    assert not offenders, (
        f"{len(offenders)} statement(s) execute on the shared connection outside "
        f"_query(), and therefore outside the lock: {offenders}"
    )
