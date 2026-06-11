"""
registry/descriptions.py

Load and search the class-descriptions index built from APIC jsonmeta schemas.

The index maps every known ACI class name to a human-readable label and a
one-sentence description extracted from the schema's `label` and `comment`
fields.  It is loaded once at server startup and kept in the lifespan context.
"""

import json
from pathlib import Path


def load_descriptions(path: Path) -> dict[str, dict[str, str]]:
    """Load class-descriptions.json into memory.

    Args:
        path: Absolute path to the class-descriptions.json file.

    Returns:
        Dict mapping ACI class name → {"label": str, "comment": str}.
        Either key may be absent when the source schema had no value.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def search(
    keyword: str,
    descriptions: dict[str, dict[str, str]],
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search class descriptions by keyword with relevance ranking.

    Performs a case-insensitive substring match against three fields:
      - class name  (weight 3 — exact vocabulary match)
      - label       (weight 2 — human name)
      - comment     (weight 1 — description)

    Results are sorted by descending score; ties preserve insertion order.

    Args:
        keyword:      Case-insensitive search term.
        descriptions: In-memory descriptions dict from load_descriptions().
        limit:        Maximum number of results to return.

    Returns:
        List of dicts, each containing:
          class_name — ACI class name (e.g. "fvBD")
          label      — short human-readable label (may be empty)
          comment    — one-sentence description (may be empty)
    """
    kw = keyword.lower()
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
        if score > 0:
            results.append((score, {"class_name": cls, "label": label, "comment": comment}))

    results.sort(key=lambda x: x[0], reverse=True)
    return [item for _, item in results[:limit]]
