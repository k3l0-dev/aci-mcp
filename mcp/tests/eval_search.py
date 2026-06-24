# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
tests/eval_search.py

Offline evaluation script for search_classes search quality.

Measures Recall@1, Recall@5, MRR and per-tier breakdown against the 39-query
golden set in tests/fixtures/search_golden.json (4 tiers of increasing
difficulty: direct label, camelCase tokenization, prop_labels, synonyms).

Run from mcp/:
    python tests/eval_search.py
    python tests/eval_search.py --limit 5     # restrict result window
    python tests/eval_search.py --verbose     # show misses and near-misses

Reference results — APIC mo-apic-v6.0_9c, 15 152 classes
──────────────────────────────────────────────────────────
Strategy                        R@1     R@5     MRR    Avg ms
──────────────────────────────  ──────  ──────  ─────  ──────
Baseline (naive substring)      15.4%   35.9%   0.229   3.2
+ Rs/Rt penalty       (axe 1)   28.2%   41.0%   0.338   3.2
+ prop_labels search  (axe 2)   30.8%   53.8%   0.400  11.4

Tier breakdown after axe 1 + axe 2:
  Tier 1 — direct label/name   R@1=35%   R@5= 55%
  Tier 2 — camelCase lookup    R@1=80%   R@5=100%
  Tier 3 — prop_labels         R@1= 9%   R@5= 45%
  Tier 4 — synonyms            R@1= 0%   R@5=  0%   (requires semantic search)

Add a row to the table above whenever a scoring change is shipped.
"""

import argparse
import json
import sys
import time
from pathlib import Path

# Make the mcp/ package importable without installation.
sys.path.insert(0, str(Path(__file__).parent.parent))

from registry.descriptions import load_descriptions, search


DESCRIPTIONS_FILE = Path(__file__).parent.parent.parent / "data" / "class-descriptions.json"
GOLDEN_FILE = Path(__file__).parent / "fixtures" / "search_golden.json"
RESULT_WINDOW = 10


def _rank(results: list[dict], expected: str) -> int | None:
    """Return 1-based rank of expected class in results, or None if absent."""
    for i, r in enumerate(results):
        if r["class_name"] == expected:
            return i + 1
    return None


def evaluate(descriptions: dict, queries: list[dict], limit: int, verbose: bool) -> dict:
    """Run all golden queries and compute metrics."""
    by_tier: dict[int, list] = {}
    ranks: list[int | None] = []
    latencies: list[float] = []

    for entry in queries:
        q, expected, tier = entry["query"], entry["expected"], entry["tier"]

        t0 = time.perf_counter()
        results = search(q, descriptions, limit)
        latencies.append((time.perf_counter() - t0) * 1000)

        rank = _rank(results, expected)
        ranks.append(rank)
        by_tier.setdefault(tier, []).append(rank)

        if verbose and (rank is None or rank > 1):
            top3 = [r["class_name"] for r in results[:3]]
            status = f"rank {rank}" if rank else "MISS"
            print(f"  [{status}] '{q}' → expected {expected}, got {top3}")

    def recall_at(k: int, rs: list) -> float:
        hits = sum(1 for r in rs if r is not None and r <= k)
        return hits / len(rs) if rs else 0.0

    def mrr(rs: list) -> float:
        total = sum(1 / r for r in rs if r is not None)
        return total / len(rs) if rs else 0.0

    return {
        "n": len(ranks),
        "recall_at_1": recall_at(1, ranks),
        "recall_at_5": recall_at(5, ranks),
        "mrr": mrr(ranks),
        "avg_ms": sum(latencies) / len(latencies),
        "by_tier": {
            t: {
                "n": len(rs),
                "recall_at_1": recall_at(1, rs),
                "recall_at_5": recall_at(5, rs),
            }
            for t, rs in sorted(by_tier.items())
        },
    }


def _fmt(m: dict) -> str:
    lines = [
        f"  Queries      : {m['n']}",
        f"  Recall@1     : {m['recall_at_1']:.1%}",
        f"  Recall@5     : {m['recall_at_5']:.1%}",
        f"  MRR          : {m['mrr']:.3f}",
        f"  Avg query    : {m['avg_ms']:.1f} ms",
        "",
        "  Per tier:",
    ]
    for tier, tm in m["by_tier"].items():
        lines.append(
            f"    Tier {tier} (n={tm['n']:2d}): "
            f"Recall@1={tm['recall_at_1']:.1%}  Recall@5={tm['recall_at_5']:.1%}"
        )
    return "\n".join(lines)


def main() -> None:
    """Entry point: load descriptions and golden set, run evaluation, print report."""
    parser = argparse.ArgumentParser(description="Evaluate search_classes quality.")
    parser.add_argument("--limit", type=int, default=RESULT_WINDOW, help="Result window size")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print misses and near-misses")
    args = parser.parse_args()

    descriptions = load_descriptions(DESCRIPTIONS_FILE)
    golden = json.loads(GOLDEN_FILE.read_text())
    queries = golden["queries"]

    print(f"\nSearch quality evaluation  (limit={args.limit}, n={len(queries)} queries)")
    print("─" * 55)

    if args.verbose:
        print("\nMisses and near-misses:")

    metrics = evaluate(descriptions, queries, args.limit, args.verbose)

    if args.verbose:
        print()

    print(_fmt(metrics))
    print()


if __name__ == "__main__":
    main()
