# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
registry/descriptions.py

Load and search the class-descriptions index built from APIC jsonmeta schemas.

The index maps every known ACI class name to a human-readable label and a
one-sentence description extracted from the schema's `label` and `comment`
fields.  It is loaded once at server startup and kept in the lifespan context.

Search strategy (v1 — axe 1: Rs/Rt penalty)
--------------------------------------------
The search function uses a weighted substring match over three fields
(class name ×3, label ×2, comment ×1).  The sole deliberate scoring
adjustment is a -3 penalty applied to ACI relation classes (Rs/Rt naming
pattern, e.g. fvRsCtx, l3extRtVrfValidationPol).

Rationale: APIC inherits the label of a target class into every relation
class that points to it.  Without the penalty, "bridge domain" returns
fvABDPol/fvRsSvcBDToBDAtt before fvBD because they all share the same
"Bridge Domain" label and the relation classes additionally contain the
concept name in their camelCase class name, yielding a higher raw score.
Rs/Rt objects are internal plumbing; they are structurally never the
primary target of a user query.

Measured gain on 39-query golden set (data/schemas mo-apic-v6.0_9c):

  Metric        Baseline    +Rs/Rt penalty    Delta
  ──────────    ────────    ──────────────    ─────
  Recall@1        15.4%          28.2%        +12.8%
  Recall@5        35.9%          41.0%         +5.1%
  MRR              0.229          0.338        +0.109
  Tier 1 R@1      10.0%          35.0%        +25.0%
  Tier 1 R@5      50.0%          55.0%         +5.0%
  Tier 2 R@5      80.0%         100.0%        +20.0%
  Avg query        3.2 ms         3.2 ms        0

Tier 3 (prop_labels) and Tier 4 (synonyms) remain at 0% — they require
enriching the class-descriptions index with property labels extracted from
the jsonmeta schemas (planned as axe 2).
"""

import json
import re
from pathlib import Path

from exceptions import DescriptionsLoadError

# ACI relation classes (Rs = resolution source, Rt = relation target) are
# internal plumbing objects — they are structurally never the primary target
# of a user query.  Pattern: package prefix followed by Rs/Rt at a camelCase
# boundary, e.g. fvRsCtx, l3extRtVrfValidationPol.
_RS_RT_RE = re.compile(r"^[a-z][a-z0-9]*(?:Rs|Rt)[A-Z]")


def load_descriptions(path: Path) -> dict[str, dict[str, str]]:
    """Load class-descriptions.json into memory.

    Args:
        path: Absolute path to the class-descriptions.json file.

    Returns:
        Dict mapping ACI class name → {"label": str, "comment": str}.
        Either key may be absent when the source schema had no value.

    Raises:
        DescriptionsLoadError: File not found or contains invalid JSON.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise DescriptionsLoadError(
            f"class-descriptions.json not found at {path}. "
            "Regenerate it with: aci-collect run --from descriptions"
        ) from None
    except OSError as exc:
        raise DescriptionsLoadError(f"Cannot read {path}: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DescriptionsLoadError(
            f"class-descriptions.json at {path} is not valid JSON: {exc}"
        ) from exc


def search(
    keyword: str,
    descriptions: dict[str, dict[str, str]],
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search class descriptions by keyword with relevance ranking.

    Scores each class against the keyword using three fields:
      - class name  weight +3  (direct vocabulary match)
      - label       weight +2  (human-readable name)
      - comment     weight +1  (one-sentence description)

    Rs/Rt relation classes receive a -3 penalty (see module docstring).
    Classes whose adjusted score reaches zero are excluded from results.

    Results are sorted by descending score; ties preserve insertion order.

    Args:
        keyword:      Case-insensitive search term (plain English or partial class name).
        descriptions: In-memory descriptions dict from load_descriptions().
        limit:        Maximum number of results to return.

    Returns:
        List of dicts, each containing:
          class_name — ACI class name (e.g. "fvBD")
          label      — short human-readable label (may be empty)
          comment    — one-sentence description (may be empty)
    """
    kw = keyword.lower()
    if not kw:
        return []
    results: list[tuple[int, dict[str, str]]] = []

    for cls, meta in descriptions.items():
        label = meta.get("label", "")
        comment = meta.get("comment", "")
        score = 0
        if kw in cls.lower():
            score += 3
        if kw in label.lower():
            score += 2
        if kw in comment.lower():
            score += 1
        if score == 0:
            # Only scan prop_labels when the class didn't already match on
            # name/label/comment — avoids inflating scores for well-matched classes
            # and keeps the hot path fast for the majority of queries.
            for pl in meta.get("prop_labels", ()):
                if kw in pl.lower():
                    score = 1
                    break
        if score > 0:
            if _RS_RT_RE.match(cls):
                score -= 3
            if score > 0:
                results.append(
                    (score, {"class_name": cls, "label": label, "comment": comment})
                )

    results.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in results[:limit]]
