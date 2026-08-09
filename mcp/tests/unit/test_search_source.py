# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""``search_classes`` now runs on the catalogue-rebuilt index.

Iteration 3 swaps the *source* of the search index — from
``data/class-descriptions.json`` to niwaki's catalogue — while leaving the
scorer (``_score``, the synonym table, the structural priors) untouched. The
index was proven byte-identical in iteration 2, so the correct expectation is
not "search is still acceptable" but "search has not moved at all".

Two things are pinned here that nothing else covers:

1. **Equality, not floors.** ``tests/eval/test_search_quality.py`` asserts
   Recall@1 ≥ 60 %, which would let 78.4 % rot to 61 % unnoticed. These tests
   assert the exact recorded values, and the exact per-query ranking — an
   aggregate can stay flat while individual answers permute, and it is the
   individual answer an agent acts on.

2. **The index is built once.** ``descriptions.search()`` caches its tokenised
   index keyed on the *identity* of the descriptions dict. Handing it a freshly
   built dict on every call would silently re-tokenise 15,239 entries per query
   and turn a 15 ms search into seconds — with every correctness test still
   green. That failure mode is invisible to every other test in the suite.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from niwashi_mcp.registry import catalog, descriptions

pytestmark = pytest.mark.catalog

# Recorded on the jsonmeta data layer, 2026-08-08, ranking within the top 5.
# See tests/baseline/README.md for why this differs from the 0.854 MRR quoted
# elsewhere (that figure was computed over a longer result list).
EXPECTED_RECALL_AT_1 = 0.7837837837837838
EXPECTED_RECALL_AT_5 = 0.9459459459459459
EXPECTED_MRR = 0.8461711711711711

# Generous: measured at ~440 ms on a workstation. This is a smoke check against
# an order-of-magnitude regression, not a benchmark — CI hardware varies.
INDEX_BUILD_BUDGET_S = 3.0


@pytest.fixture(scope="module")
def golden() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "search_golden.json"
    return json.loads(path.read_text())["queries"]


@pytest.fixture(scope="module")
def index() -> dict:
    """The catalogue-rebuilt index — the one the server now uses."""
    return catalog.descriptions_index()


def _rank(index: dict, query: str, expected: str) -> int | None:
    hits = descriptions.search(query, index, limit=5)
    names = [h["class_name"] for h in hits]
    return names.index(expected) + 1 if expected in names else None


def _ranks(index: dict, golden: list[dict]) -> list[int | None]:
    return [_rank(index, q["query"], q["expected"]) for q in golden]


@pytest.fixture(scope="module")
def ranks(index, golden) -> list[int | None]:
    """Golden-set ranks, computed once.

    Recomputing per test would be correct but slow: ``search`` caches its
    tokenised index on dict identity, so any interleaving of two indexes
    re-tokenises 15,239 entries every time.
    """
    return _ranks(index, golden)


class TestSearchQualityUnchanged:
    def test_recall_at_1_is_exactly_the_recorded_value(self, ranks):
        """Equality, because the index is meant to be byte-identical.

        Any movement here is a bug in the rebuild, not a scoring trade-off.
        """
        r1 = sum(1 for r in ranks if r is not None and r <= 1) / len(ranks)
        assert r1 == pytest.approx(EXPECTED_RECALL_AT_1, abs=1e-9), (
            f"Recall@1 moved to {r1:.4%} from {EXPECTED_RECALL_AT_1:.4%}"
        )

    def test_recall_at_5_is_exactly_the_recorded_value(self, ranks):
        r5 = sum(1 for r in ranks if r is not None and r <= 5) / len(ranks)
        assert r5 == pytest.approx(EXPECTED_RECALL_AT_5, abs=1e-9)

    def test_mrr_is_exactly_the_recorded_value(self, ranks):
        mrr = sum(1 / r for r in ranks if r is not None) / len(ranks)
        assert mrr == pytest.approx(EXPECTED_MRR, abs=1e-9)

    def test_every_query_returns_the_same_top_5_as_recorded(self, index, golden):
        """Per-query equality against the pre-2.0 recording.

        Aggregates can stay flat while individual answers permute, and it is
        the individual answer an agent acts on. Compared against
        `baseline/baseline.json`, captured from the file-backed index before
        the migration — the file itself is deleted, and pointing at it made
        this test skip silently.
        """
        recorded_path = Path(__file__).resolve().parents[1] / "baseline" / "baseline.json"
        if not recorded_path.exists():
            pytest.skip("baseline.json not recorded")
        recorded = json.loads(recorded_path.read_text())["search"]["per_query"]

        # Grouped per index, never interleaved: `search` caches on dict
        # identity, so alternating between the two sources would re-tokenise
        # 15,239 entries on all 148 calls and make this test take a minute.
        moved = []
        for item in golden:
            q = item["query"]
            if q not in recorded:
                continue
            now = [h["class_name"] for h in descriptions.search(q, index, limit=5)]
            if now != recorded[q]["top5"]:
                moved.append(f"'{q}': {recorded[q]['top5']} → {now}")
        assert not moved, f"{len(moved)} ranking(s) moved:\n  " + "\n  ".join(moved[:10])


class TestIndexIsBuiltOnce:
    """The performance trap that no correctness test would catch."""

    def test_repeated_search_reuses_the_tokenised_index(self, index):
        """Two searches on the same dict must not rebuild the index.

        ``_get_index`` caches on ``is`` identity. Asserted on the cache object
        itself rather than on timing: a wall-clock comparison is flaky on a
        loaded CI runner, and the invariant we care about is "the same tokenised
        list is reused", which is directly observable.
        """
        descriptions._index_cache = None
        descriptions.search("bridge domain", index, limit=5)
        built = descriptions._index_cache[1]

        descriptions.search("subnet", index, limit=5)
        assert descriptions._index_cache[1] is built, "index was rebuilt on the second call"

    def test_a_fresh_dict_correctly_misses_the_cache(self):
        """The identity keying must not produce stale results either.

        A different dict has to rebuild — otherwise a caller that legitimately
        swaps the index would keep searching the old one. Uses a tiny synthetic
        index: identity behaviour does not depend on size, and building the real
        one twice would add a minute to the suite for no extra signal.
        """
        first = {"fvBD": {"label": "Bridge Domain"}}
        second = {"fvBD": {"label": "Bridge Domain"}}  # equal, distinct object

        descriptions._index_cache = None
        descriptions.search("bridge", first, limit=1)
        assert descriptions._index_cache[0] is first

        descriptions.search("bridge", second, limit=1)
        assert descriptions._index_cache[0] is second, "a distinct dict must rebuild"

    def test_index_build_is_not_pathologically_slow(self):
        """Order-of-magnitude smoke check on the startup cost.

        The build runs once, inside a lifespan that already performs an APIC
        authentication round trip, so a few hundred milliseconds is affordable.
        Seconds would not be.
        """
        t0 = time.perf_counter()
        catalog.descriptions_index()
        elapsed = time.perf_counter() - t0
        assert elapsed < INDEX_BUILD_BUDGET_S, f"index build took {elapsed:.2f}s"


class TestLifespanUsesTheCatalogue:
    @staticmethod
    def _main_source() -> str:
        """Source of main.py.

        ``app_lifespan`` is wrapped by FastMCP's ``@lifespan`` decorator and is
        no longer a function object, so ``inspect.getsource`` on it raises. The
        module file is read instead.
        """
        from niwashi_mcp import main

        return Path(main.__file__).read_text()

    def test_main_no_longer_reads_the_descriptions_file(self):
        """The file is gone from the startup path — it is deleted in iteration 5."""
        source = self._main_source()
        assert "catalog.descriptions_index()" in source
        assert "load_descriptions(" not in source

    def test_apic_version_is_logged_at_startup(self):
        """From 2.0 the corpus version is pinned by a dependency, not the operator.

        It has to be visible in the logs, otherwise a silent niwaki upgrade
        changes the object model an agent reasons about with no trace.
        """
        assert "catalog.apic_version()" in self._main_source()


# ── the golden set itself ─────────────────────────────────────────────────────


def test_golden_set_has_meaningful_size():
    """Guards against the golden set silently shrinking.

    Folded in from `tests/eval/test_search_quality.py`, which was deleted. That
    module asserted Recall@1 >= 0.60 and Recall@5 >= 0.85 against an
    implementation delivering 0.784 / 0.946 — floors so far below the real
    figures that its own docstring's claim (that it would catch removing the
    `isConfigurable` prior) was measurably false: removing it drops R@1 to 71.6 %
    and the test still passed. Of twelve scoring mutants it killed two;
    `tests/baseline/` kills all twelve, because it asserts the metrics and the
    exact top-5 of every query as *equalities* rather than floors.

    This check was the one thing in that module the baseline does not restate:
    a golden set trimmed to a handful of queries could be satisfied by a change
    that does not generalise.
    """
    import json
    from pathlib import Path

    golden = Path(__file__).resolve().parents[1] / "fixtures" / "search_golden.json"
    queries = json.loads(golden.read_text())["queries"]
    assert len(queries) >= 50, f"golden set shrank to {len(queries)} queries"
