# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/eval/test_search_quality.py

Runs the search_classes quality evaluation (see tests/eval_search.py) as a
pytest test with a floor on Recall@1, so a scoring regression in
registry.descriptions.search() fails CI rather than only showing up in an
offline report someone has to remember to run.

Skips when the full data/class-descriptions.json collection is not present
(e.g. a bare checkout without the data/ directory) — the golden set assumes
the real ~15k-class registry, not a synthetic fixture, since the whole point
is measuring ranking quality against the actual production data.
"""

import sys
from pathlib import Path

import pytest

# tests/eval_search.py is a standalone script (not a package module) living
# one directory up from this test — make its evaluate() importable.
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval_search import GOLDEN_FILE, evaluate

from niwashi_mcp.registry import catalog

# Floor, not a target: the measured result as of the v2 rewrite is
# Recall@1=78.4%/Recall@5=94.6% on the 74-query golden set (see the module
# docstring in registry/descriptions.py). These floors leave headroom for
# the golden set to grow without becoming flaky, while still catching a
# real regression — e.g. a change that silently drops the isConfigurable
# prior or breaks tokenization would fail this test immediately.
MIN_RECALL_AT_1 = 0.60
MIN_RECALL_AT_5 = 0.85


@pytest.fixture(scope="module")
def golden_metrics() -> dict:
    import json

    descriptions = catalog.descriptions_index()
    queries = json.loads(GOLDEN_FILE.read_text())["queries"]
    return evaluate(descriptions, queries, limit=10, verbose=False)


def test_recall_at_1_meets_floor(golden_metrics):
    """search_classes must find the right class as the #1 result at least
    MIN_RECALL_AT_1 of the time on the golden set — this is what "trust the
    first result" (the server's own mandatory-workflow instruction) requires.
    """
    r1 = golden_metrics["recall_at_1"]
    assert r1 >= MIN_RECALL_AT_1, (
        f"Recall@1 dropped to {r1:.1%} (floor: {MIN_RECALL_AT_1:.0%}) — "
        "run `python tests/eval_search.py --verbose` to see which queries regressed."
    )


def test_recall_at_5_meets_floor(golden_metrics):
    """Within the top-5 results, the right class should almost always appear —
    this is the practical floor for an agent that inspects a short result list.
    """
    r5 = golden_metrics["recall_at_5"]
    assert r5 >= MIN_RECALL_AT_5, (
        f"Recall@5 dropped to {r5:.1%} (floor: {MIN_RECALL_AT_5:.0%}) — "
        "run `python tests/eval_search.py --verbose` to see which queries regressed."
    )


def test_golden_set_has_meaningful_size(golden_metrics):
    """Guards against the golden set silently shrinking to a handful of
    queries a change could trivially satisfy without generalizing.
    """
    assert golden_metrics["n"] >= 50
