# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Capture the behavioural baseline of the current implementation.

Why this exists
---------------
The 2.0 migration replaces the data layer (raw jsonmeta files) with niwaki's
SQLite catalogue. The tools' signatures do not change, so a regression would be
*silent*: ``get_schema()`` could start returning a subtly different shape, the
search index could drift, and every existing test would still pass. The floors
in ``tests/eval/test_search_quality.py`` are deliberately loose (60 % / 85 %)
and would not catch a drop from the actual 78.4 % to, say, 62 %.

This module records what the implementation *actually does today*, so that
``test_baseline.py`` can assert the new data layer reproduces it exactly.

What it records
---------------
1. ``schemas``  — full ``load_schema()`` output for a stratified sample of
   classes, including ``property_details``. This is the parity oracle: it is
   the only artefact that would catch a changed field shape.
2. ``search``   — Recall@1, Recall@5, MRR overall and per tier on the 74-query
   golden set, plus the exact top-5 for every query. The per-query ranking is
   what pins agent-visible behaviour; the aggregate alone can stay flat while
   individual answers move.
3. ``index``    — size and a content digest of the descriptions index, so a
   rebuilt index can be proven byte-equivalent.
4. ``perf``     — wall-clock timings for the hot paths, recorded as *observed*
   values. They are context for humans, not assertions: see the note below.
5. ``env``      — what produced the numbers, so a mismatch can be explained
   rather than guessed at.

On timings
----------
Timings are recorded but **not asserted** by ``test_baseline.py``. A CI runner
is not a workstation and wall-clock comparisons across machines produce false
failures, which is worse than no signal. The existing ``tests/perf/`` budgets
remain the enforcement mechanism; these numbers exist so a human can see an
order-of-magnitude regression at a glance.

Usage
-----
    python -m tests.baseline.capture            # writes baseline.json
    python -m tests.baseline.capture --check    # compare, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_MCP_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_MCP_ROOT))

# ruff: noqa: E402 — the sys.path insert above must run before these imports,
# because this module is also executed directly (`python -m tests.baseline.capture`)
# from a tree that is not yet an installed package.
from niwashi_mcp.registry import catalog
from niwashi_mcp.registry.descriptions import search as desc_search

BASELINE_PATH = _HERE / "baseline.json"
_REPO_ROOT = _MCP_ROOT.parent
_SCHEMAS_DIR = _REPO_ROOT / "data" / "schemas"
_DESCRIPTIONS = _REPO_ROOT / "data" / "class-descriptions.json"
_GOLDEN = _MCP_ROOT / "tests" / "fixtures" / "search_golden.json"

# A stratified sample, not a random one. Each class is here because it exercises
# a distinct shape that the catalogue swap could plausibly break.
SAMPLE_CLASSES: list[str] = [
    # Bread and butter — the classes an agent touches on almost every task.
    "fvTenant", "fvBD", "fvAEPg", "fvCtx", "fvSubnet", "fvAp",
    # Relations: Rs (outgoing) and Rt (incoming), the colon-notation carriers.
    "fvRsCtx", "fvRsBd", "fvRsPathAtt", "fvRtBd", "fvRsProv", "fvRsCons",
    # Contracts and filters — nested containment.
    "vzBrCP", "vzFilter", "vzEntry", "vzSubj",
    # L3Out family — the deepest containment chains in the model.
    "l3extOut", "l3extLNodeP", "l3extRsNodeL3OutAtt", "l3extInstP",
    # Physical / fabric — DN-prefix containment rather than containedBy.
    "fabricNode", "l1PhysIf", "ethpmPhysIf", "pcAggrIf", "vpcIf", "fabricLink",
    # Faults and health — huge dnFormats, the non-truncation canaries.
    "faultInst", "faultDelegate", "faultRecord", "healthInst",
    # Abstract classes — isAbstract must survive.
    "fvATg", "l3extLIfP",
    # Stats — isStat, a category with its own rules.
    "eqptIngrTotal5min",
    # Enum-heavy — property_details.options is where the defaultValue marker leaks.
    "fvRsPathAtt", "mgmtRsOoBStNode",
    # Registers carrying mo:* types — the 275 options that 2.0 drops.
    "actionAeSubj",
    # Tagging / annotation — attachable everywhere, big containedBy.
    "tagTag", "tagAnnotation",
    # Deliberately absent, to pin the empty-dict contract.
    "fvNotARealClass",
]


def _digest(obj: Any) -> str:
    """Stable digest of a JSON-serialisable object."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


# Above this JSON size, the verbatim schema is not stored — only its digest and
# a trimmed excerpt. `faultDelegate` alone carries 64,313 dnFormats; storing the
# sample verbatim produces a 26 MB file, which is not a reviewable artefact.
# Drift detection is unaffected: the digest is always computed on the *full*
# schema, so any change anywhere still fails the comparison.
_INLINE_LIMIT_BYTES = 20_000


def _trim(schema: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return a storable view of a schema, and whether it had to be trimmed."""
    raw = json.dumps(schema, ensure_ascii=False)
    if len(raw) <= _INLINE_LIMIT_BYTES:
        return schema, False
    trimmed = dict(schema)
    for key in ("dnFormats", "containedBy", "contains"):
        val = schema.get(key)
        if isinstance(val, list) and len(val) > 3:
            trimmed[key] = [*val[:3], f"… +{len(val) - 3} more (elided, see digest)"]
    return trimmed, True


def capture_schemas() -> dict[str, Any]:
    """get_schema() output for the sample, with and without property details.

    Reads through whatever data layer the server currently uses — since
    iteration 4 that is the catalogue. The recorded baseline.json was captured
    from the jsonmeta reader, so this comparison is the migration's proof:
    "the new implementation reproduces what the old one did".

    The digest is the oracle; the stored payload is for human review.
    """
    out: dict[str, Any] = {}
    for cls in dict.fromkeys(SAMPLE_CLASSES):  # dedupe, keep order
        plain = catalog.load_schema(cls)
        detailed = catalog.load_schema(cls, include_property_details=True)
        stored, was_trimmed = _trim(plain)
        out[cls] = {
            "exists": catalog.class_exists(cls),
            "schema_digest": _digest(plain),  # always on the FULL schema
            "detailed_digest": _digest(detailed),
            "property_count": len(plain.get("properties", [])),
            "dn_format_count": len(plain.get("dnFormats", [])),
            "contained_by_count": len(plain.get("containedBy", [])),
            "contains_count": len(plain.get("contains", [])),
            "relation_to_count": len(plain.get("relationTo", {})),
            "relation_from_count": len(plain.get("relationFrom", {})),
            "keys": sorted(plain),
            "trimmed": was_trimmed,
            "schema": stored,
        }
    return out


def capture_search(descriptions: dict) -> dict[str, Any]:
    """Aggregate metrics *and* the exact ranking of every golden query."""
    golden = json.loads(_GOLDEN.read_text())["queries"]
    per_query: dict[str, Any] = {}
    ranks: list[int | None] = []
    by_tier: dict[str, list[int | None]] = {}

    for item in golden:
        q, expected, tier = item["query"], item["expected"], str(item.get("tier", "?"))
        hits = desc_search(q, descriptions, limit=5)
        names = [h["class_name"] if isinstance(h, dict) else h for h in hits]
        rank = names.index(expected) + 1 if expected in names else None
        ranks.append(rank)
        by_tier.setdefault(tier, []).append(rank)
        per_query[q] = {"expected": expected, "tier": tier, "top5": names, "rank": rank}

    def _r_at(k: int, rs: list[int | None]) -> float:
        return round(sum(1 for r in rs if r is not None and r <= k) / len(rs), 6) if rs else 0.0

    def _mrr(rs: list[int | None]) -> float:
        return round(sum(1 / r for r in rs if r is not None) / len(rs), 6) if rs else 0.0

    return {
        "n": len(ranks),
        "recall_at_1": _r_at(1, ranks),
        "recall_at_5": _r_at(5, ranks),
        "mrr": _mrr(ranks),
        "by_tier": {
            t: {"n": len(rs), "recall_at_1": _r_at(1, rs), "recall_at_5": _r_at(5, rs)}
            for t, rs in sorted(by_tier.items())
        },
        "per_query": per_query,
        "per_query_digest": _digest(per_query),
    }


def capture_index(descriptions: dict) -> dict[str, Any]:
    """Pin the descriptions index so a rebuilt one can be proven equivalent."""
    return {
        "class_count": len(descriptions),
        "digest": _digest(descriptions),
        "fields_seen": sorted({k for v in descriptions.values() for k in v}),
        "sample": {c: descriptions[c] for c in sorted(descriptions)[:3]},
    }


def capture_perf(descriptions: dict) -> dict[str, Any]:
    """Observed timings. Context for humans — not asserted (see module docstring)."""

    def _time(fn, n: int) -> float:
        fn()  # warm
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        return round((time.perf_counter() - t0) / n * 1000, 4)

    return {
        "unit": "ms_per_call",
        "load_schema": _time(lambda: catalog.load_schema("fvBD"), 200),
        "load_schema_detailed": _time(
            lambda: catalog.load_schema("fvBD", include_property_details=True), 200
        ),
        "search": _time(lambda: desc_search("bridge domain", descriptions, limit=5), 20),
        "index_build_cold": None,  # filled by the caller, measured once
    }


def capture() -> dict[str, Any]:
    t0 = time.perf_counter()
    descriptions = catalog.descriptions_index()
    load_ms = round((time.perf_counter() - t0) * 1000, 2)

    perf = capture_perf(descriptions)
    perf["descriptions_load_cold"] = load_ms

    return {
        "_meta": {
            "purpose": "Behavioural baseline of the pre-2.0 (jsonmeta) data layer.",
            "captured_from": "niwaki catalogue (was: data/schemas + class-descriptions.json)",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "note": "Timings are recorded, not asserted. See capture.py docstring.",
        },
        "index": capture_index(descriptions),
        "schemas": capture_schemas(),
        "search": capture_search(descriptions),
        "perf": perf,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="compare only, write nothing")
    args = ap.parse_args()

    current = capture()

    if args.check:
        if not BASELINE_PATH.exists():
            print("no baseline recorded yet", file=sys.stderr)
            return 2
        recorded = json.loads(BASELINE_PATH.read_text())
        drift = [
            k
            for k in ("index", "schemas", "search")
            if _digest(recorded[k]) != _digest(current[k])
        ]
        if drift:
            print(f"DRIFT in: {', '.join(drift)}", file=sys.stderr)
            return 1
        print("no drift")
        return 0

    BASELINE_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    s = current["search"]
    print(f"wrote {BASELINE_PATH.relative_to(_MCP_ROOT)}")
    print(f"  classes indexed : {current['index']['class_count']:,}")
    print(f"  schemas sampled : {len(current['schemas'])}")
    print(f"  R@1 {s['recall_at_1']:.1%}  R@5 {s['recall_at_5']:.1%}  MRR {s['mrr']:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
