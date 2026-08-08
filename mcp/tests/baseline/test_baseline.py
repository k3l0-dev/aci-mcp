# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Assert the implementation still behaves as recorded in ``baseline.json``.

This is the regression net for the 2.0 data-layer migration. The five tools keep
their signatures, so a defect in the swap would be *silent*: a changed field
shape, a drifted search ranking, a truncated list. Nothing else in the suite
would notice.

Each test here fails loudly on one specific kind of drift, and the failure
message says which class or which query moved — not just that a hash differs.

Relationship to the rest of the suite
-------------------------------------
- ``tests/eval/test_search_quality.py`` keeps *floors* (60 % / 85 %). It answers
  "is search still acceptable?".
- This module asserts *equality* against the recorded run. It answers "did
  anything move at all?". During the migration that is the stronger question:
  the index is meant to be reconstructed byte-identically, so any movement is a
  bug, not a trade-off.

Regenerating
------------
``python -m tests.baseline.capture`` rewrites the reference. Do that **only**
with a deliberate, reviewed reason, and say so in the commit message — silently
re-recording the baseline defeats its entire purpose.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.baseline.capture import (
    BASELINE_PATH,
    capture_index,
    capture_schemas,
    capture_search,
)

pytestmark = pytest.mark.baseline


@pytest.fixture(scope="module")
def recorded() -> dict:
    if not BASELINE_PATH.exists():
        pytest.skip(f"no baseline recorded at {BASELINE_PATH}")
    return json.loads(BASELINE_PATH.read_text())


@pytest.fixture(scope="module")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def descriptions(repo_root: Path) -> dict:
    from registry.descriptions import load_descriptions

    path = repo_root / "data" / "class-descriptions.json"
    if not path.exists():
        pytest.skip("class-descriptions.json not present (CI without the data bundle)")
    return load_descriptions(path)


@pytest.fixture(scope="module")
def schemas_dir(repo_root: Path) -> Path:
    from registry.schema import resolve_schemas_dir

    resolved = resolve_schemas_dir(repo_root / "data" / "schemas")
    if not resolved.is_dir() or not any(resolved.iterdir()):
        pytest.skip("data/schemas is empty (CI without the schema bundle)")
    return resolved


# --------------------------------------------------------------- index


def test_index_class_count_unchanged(recorded, descriptions):
    """The number of indexed classes is a contract: it bounds what search can find."""
    assert len(descriptions) == recorded["index"]["class_count"], (
        f"index size moved: {len(descriptions):,} now vs "
        f"{recorded['index']['class_count']:,} recorded"
    )


def test_index_content_identical(recorded, descriptions):
    """Byte-level equality of the whole index.

    The 2.0 migration rebuilds this index from niwaki's catalogue and claims the
    result is byte-identical. This is the test that makes the claim falsifiable.
    """
    current = capture_index(descriptions)
    if current["digest"] == recorded["index"]["digest"]:
        return

    # Digest mismatch is useless on its own — say *what* moved.
    rec_fields = set(recorded["index"]["fields_seen"])
    cur_fields = set(current["fields_seen"])
    detail = []
    if rec_fields != cur_fields:
        detail.append(f"fields changed: -{rec_fields - cur_fields} +{cur_fields - rec_fields}")
    pytest.fail("descriptions index drifted. " + ("; ".join(detail) or "content differs"))


# --------------------------------------------------------------- schemas


def test_schema_output_identical(recorded, schemas_dir):
    """``get_schema()`` returns exactly what it returned before, class by class.

    This is the parity oracle for the data-layer swap. It covers the plain call
    and the ``include_property_details=True`` call, on a stratified sample:
    relations, abstract classes, stats classes, enum-heavy classes, the
    huge-``dnFormats`` monsters, and one class that must stay absent.
    """
    current = capture_schemas(schemas_dir)
    moved: list[str] = []

    for cls, ref in recorded["schemas"].items():
        assert cls in current, f"{cls} vanished from the sample"
        got = current[cls]
        if got["schema_digest"] != ref["schema_digest"]:
            moved.append(
                f"{cls}: schema changed "
                f"(props {ref['property_count']}→{got['property_count']}, "
                f"dnFormats {ref['dn_format_count']}→{got['dn_format_count']}, "
                f"keys -{sorted(set(ref['keys']) - set(got['keys']))} "
                f"+{sorted(set(got['keys']) - set(ref['keys']))})"
            )
        elif got["detailed_digest"] != ref["detailed_digest"]:
            moved.append(f"{cls}: property_details changed (plain schema is unchanged)")

    assert not moved, "get_schema() drifted:\n  " + "\n  ".join(moved)


def test_absent_class_still_returns_empty_dict(recorded, schemas_dir):
    """A class that does not exist returns ``{}`` and ``class_exists`` says False.

    Pinned separately because the 2.0 migration changes *where* absence is
    determined (file lookup → SQL lookup), and an exception thrown instead of an
    empty dict would be a contract break an agent cannot recover from.
    """
    from registry.schema import class_exists, load_schema

    absent = [c for c, r in recorded["schemas"].items() if not r["exists"]]
    assert absent, "baseline has no absent-class case — the contract is unpinned"
    for cls in absent:
        assert load_schema(cls, schemas_dir) == {}, f"{cls} should return an empty dict"
        assert class_exists(cls, schemas_dir) is False, f"class_exists({cls}) should be False"


def test_dn_formats_not_truncated(recorded, schemas_dir):
    """The high-cardinality classes keep every template.

    ``faultDelegate`` carries 64,313 DN templates and ``faultInst`` 24,151. A
    storage or serialisation change that silently caps a list would be invisible
    in aggregate but would make the anti-hallucination anchor in SKILL.md lie.
    """
    from registry.schema import load_schema

    checked = 0
    for cls, ref in recorded["schemas"].items():
        if ref["dn_format_count"] < 1000:
            continue
        got = len(load_schema(cls, schemas_dir).get("dnFormats", []))
        assert got == ref["dn_format_count"], (
            f"{cls}: dnFormats went from {ref['dn_format_count']:,} to {got:,}"
        )
        checked += 1
    assert checked >= 2, "no high-cardinality class in the sample — canary is missing"


# --------------------------------------------------------------- search


def test_search_aggregate_metrics_unchanged(recorded, descriptions):
    """Recall and MRR are *equal* to the recorded run, not merely above a floor.

    The loose floors in tests/eval/ would let 78.4 % rot to 61 % unnoticed.
    """
    current = capture_search(descriptions)
    ref = recorded["search"]
    for metric in ("recall_at_1", "recall_at_5", "mrr"):
        assert current[metric] == pytest.approx(ref[metric], abs=1e-6), (
            f"{metric} moved: {current[metric]:.4f} now vs {ref[metric]:.4f} recorded "
            f"(n={current['n']})"
        )


def test_search_per_query_ranking_unchanged(recorded, descriptions):
    """Every golden query returns the same top-5, in the same order.

    Aggregates can stay flat while individual answers swap — and it is the
    individual answer an agent acts on. This catches what the aggregate hides.
    """
    current = capture_search(descriptions)
    moved: list[str] = []

    for q, ref in recorded["search"]["per_query"].items():
        got = current["per_query"].get(q)
        if got is None:
            moved.append(f"'{q}': query disappeared from the golden set")
        elif got["top5"] != ref["top5"]:
            moved.append(
                f"'{q}' (expects {ref['expected']}): {ref['top5']} → {got['top5']}"
            )

    assert not moved, f"{len(moved)} query ranking(s) drifted:\n  " + "\n  ".join(moved[:10])


def test_search_per_tier_unchanged(recorded, descriptions):
    """Per-tier recall is pinned, so a gain in one tier cannot mask a loss in another."""
    current = capture_search(descriptions)
    for tier, ref in recorded["search"]["by_tier"].items():
        got = current["by_tier"].get(tier)
        assert got is not None, f"tier {tier} disappeared"
        assert got["recall_at_1"] == pytest.approx(ref["recall_at_1"], abs=1e-6), (
            f"tier {tier} R@1 moved: {got['recall_at_1']:.1%} vs {ref['recall_at_1']:.1%}"
        )
