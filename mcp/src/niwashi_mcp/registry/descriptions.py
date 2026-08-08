# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
registry/descriptions.py

Load and search the class-descriptions index built from APIC jsonmeta schemas.

The index maps every known ACI class name to a human-readable label, a
one-sentence description, and (for most classes) a list of property labels
and two structural flags (isConfigurable, isAbstract), all extracted from the
schema's own fields. It is loaded once at server startup and kept in the
lifespan context.

Search strategy history
------------------------
v1: weighted substring match over three fields (class name x3, label x2,
comment x1), with a -3 penalty for ACI relation classes (Rs/Rt naming
pattern, e.g. fvRsCtx), plus a prop_labels fallback (+1, no accumulation)
for classes that score 0 on the three main fields — the final v1 state
this module was rewritten from, and the baseline for the numbers below
(see docs/internals/search-algorithm.md for the full axis-by-axis history,
including the intermediate Rs/Rt-only state before the prop_labels axis
was added).

v2 (this version): tokenized, camelCase-aware matching replaces raw
substring search, so a multi-word query like "fabric node" can match a
class name it could never touch as a single unbroken substring. Scoring is
reworked around what a query phrase actually means:

  - An exact match against the class's human label (e.g. "VRF" == fvCtx's
    label "VRF") or against the class name itself dominates everything else.
  - Token coverage against the label and class name rewards queries that
    name most or all of a concept ("bridge domain" fully covering fvBD's
    label) over one that only shares an incidental word.
  - A phrase match inside a property label (e.g. "ARP flooding" inside
    fvBD's "ARP Flooding" property) or a property-token coverage score lets
    a query about a class's *behaviour* land on the right class even when
    that behaviour isn't in the class's own label or comment.
  - Structural priors reflect what APIC objects structurally are, not what
    they're called: `isConfigurable` classes get a bonus (a user asking
    about a concept almost always wants the object they can create/edit,
    not a same-named stats/telemetry class), `isAbstract` classes get a
    penalty (they can never be the answer to "which class do I query"),
    and the existing Rs/Rt penalty is kept for relation-plumbing classes.
  - Ties are broken by preferring fewer class-name tokens then a shorter
    class name then alphabetical order — a concise, canonical class name
    over a more specific variant that happens to score identically.
  - A small curated table of ACI jargon/aliases (`_JARGON`, `_SYNONYMS`)
    covers the handful of terms operators use that appear nowhere in the
    schema text itself (e.g. "gateway" for a subnet, "VRF" as a query when
    the label match alone isn't enough to disambiguate).

Because scoring now needs each class's text pre-tokenized rather than
scanned fresh per call, a build-once index is cached across calls (see
`_get_index`) keyed by the identity of the `descriptions` dict passed in —
that dict is loaded once at server startup and reused for the process
lifetime, so the cache is built exactly once in production. `search()`'s
public signature is unchanged.

Measured on the golden set (data/schemas mo-apic-v6.0_9c; see
tests/eval_search.py and tests/fixtures/search_golden.json):

  Metric        v1, 39 queries   v2, 39 queries   v2, 74 queries (grown set)
  ──────────    ──────────────   ──────────────   ──────────────────────────
  Recall@1           30.8%            69.2%                 78.4%
  Recall@5           53.8%            89.7%                 94.6%
  MRR                0.400            0.793                 0.854

The golden set grew from 39 to 74 queries alongside the v2 rewrite, adding
breadth (more classes, more phrasing styles) rather than cases picked to
flatter the new algorithm — Recall@1/5 both *improved* on the larger set
relative to the original 39, which is the result you'd want to see if the
gains generalize rather than overfit.

`tests/eval/test_search_quality.py` runs this evaluation as a pytest test
with a floor on Recall@1 — a search-quality regression fails CI, not just an
offline report. Run `python tests/eval_search.py --verbose` for the current
per-query breakdown, including misses and near-misses.
"""

import json
import re
from pathlib import Path

from niwashi_mcp.exceptions import DescriptionsLoadError

# ── Tokenization ──────────────────────────────────────────────────────────────

# Splits a camelCase / PascalCase / ACRONYM-mixed identifier into lowercase
# tokens: "fvBD" -> ["fv", "bd"], "l3extRtVrfValidationPol" ->
# ["l3ext", "rt", "vrf", "validation", "pol"], "IPv6Multicast" ->
# ["i", "pv6", "multicast"] (acronym runs are split before a trailing
# Titlecase word). Also used on natural-language queries and labels/comments,
# where it degrades gracefully to simple whitespace/punctuation splitting.
_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")

# Separator used to join a class's property labels into a single haystack for
# the phrase-match check (see _build_entry). Chosen to be vanishingly
# unlikely to appear inside real property-label text, so a query can never
# falsely "match" by spanning the boundary between two unrelated labels.
_PROP_SEP = "\x1f"


def _tokenize(text: str) -> list[str]:
    """Split an identifier or natural-language string into lowercase tokens."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# ── Structural signals ─────────────────────────────────────────────────────────

# ACI relation classes (Rs = resolution source, Rt = relation target) are
# internal plumbing objects — they are structurally never the primary target
# of a user query. Pattern: package prefix followed by Rs/Rt at a camelCase
# boundary, e.g. fvRsCtx, l3extRtVrfValidationPol.
_RS_RT_RE = re.compile(r"^[a-z][a-z0-9]*(?:Rs|Rt)[A-Z]")

# Telemetry/stats classes carry a time-bucket suffix and structurally can
# never be what a "bridge domain" / "VRF" / etc. query is asking for — they
# exist purely to hold aggregated counters for another class.
_STATS_SUFFIX_RE = re.compile(r"(?:5min|15min|1h|1d|1w|1mo|1qtr|1year)$")

# ── Curated ACI jargon and synonyms ────────────────────────────────────────────
#
# A small, honestly-scoped table for the handful of terms operators use that
# a class's own label/comment/property text does not surface on its own.
# Not exhaustive by design — it exists to close specific, observed gaps
# (see tests/fixtures/search_golden.json tiers 3-4), not to reimplement a
# thesaurus. Extend it when a real query is found to need it; resist the
# temptation to pad it speculatively.
#
# _JARGON maps a class name to the plain-English phrase a user would say for
# it, when that phrase differs meaningfully from the class's own label (e.g.
# bgpPeerP's actual APIC label is "Peer Connectivity Profile" — a user asking
# for "BGP peer policy" would not find "BGP" in that label at all).
_JARGON: dict[str, str] = {
    "fvCtx": "vrf context routing instance",
    "fvBD": "bridge domain",
    "fvAEPg": "epg application endpoint group",
    "fvTenant": "tenant",
    "vzBrCP": "contract security policy",
    "vzFilter": "filter",
    "vzSubj": "contract subject",
    "l3extOut": "l3out layer 3 outside",
    "l3extLNodeP": "logical node profile",
    "l3extLIfP": "logical interface profile",
    "l2extOut": "l2out layer 2 outside",
    "bgpPeerP": "bgp peer policy",
    "ospfIfPol": "ospf interface policy",
    "eigrpIfPol": "eigrp interface policy",
    "infraAccBndlGrp": "port channel vpc interface policy group",
    "infraAccPortGrp": "access port policy group",
    "infraAttEntityP": "aaep attachable entity profile",
    "physDomP": "physical domain",
    "vmmDomP": "vmm domain",
    "l3extDomP": "l3 domain",
    "l2extDomP": "l2 domain",
    "fvSubnet": "subnet gateway",
    "fvCEp": "client endpoint",
    "fvStCEp": "static endpoint",
    "fabricNode": "fabric node switch",
    "fabricPath": "fabric path",
    "fvRsCtx": "bridge domain to vrf relation",
    "qosDppPol": "qos data plane policing policy",
    "fvEpRetPol": "endpoint retention policy",
}

# _SYNONYMS maps a single informal query token to the tokens that actually
# appear in the target class's name or label, contributing a modest boost
# proportional to how much of the query it covers — a soft nudge, not an
# override, so it cannot beat a genuine exact match.
_SYNONYMS: dict[str, frozenset[str]] = {
    "vrf": frozenset({"ctx", "context"}),
    "context": frozenset({"ctx", "vrf"}),
    "instance": frozenset({"ctx", "inst"}),
    "routing": frozenset({"ctx", "vrf", "rtctrl"}),
    "epg": frozenset({"aepg"}),
    "outside": frozenset({"out", "ext"}),
    "endpoint": frozenset({"ep", "cep"}),
    "vpc": frozenset({"bndl"}),
    "pc": frozenset({"bndl"}),
    "gateway": frozenset({"subnet"}),
    "security": frozenset({"contract", "brcp"}),
}


def _build_entry(cls: str, meta: dict[str, str]) -> dict:
    """Precompute the tokenized/lowercased fields used by score() for one class.

    Isolated so `_get_index()` can build all 15k+ entries once and cache the
    result — see the module docstring for why this matters for latency.
    """
    label = meta.get("label", "")
    comment = meta.get("comment", "")
    prop_labels = meta.get("prop_labels") or []
    jargon = _JARGON.get(cls, "")

    return {
        "cls": cls,
        "cls_lc": cls.lower(),
        "label": label,
        "comment": comment,
        "label_lc": label.lower(),
        "comment_lc": comment.lower(),
        "jargon_lc": jargon,
        "name_toks": frozenset(_tokenize(cls)),
        "label_toks": frozenset(_tokenize(label)) | frozenset(_tokenize(jargon)),
        "comment_toks": frozenset(_tokenize(comment)),
        "prop_haystack": _PROP_SEP.join(p.lower() for p in prop_labels),
        "prop_toks": frozenset(t for p in prop_labels for t in _tokenize(p)),
        "configurable": bool(meta.get("isConfigurable")),
        "abstract": bool(meta.get("isAbstract")),
        "is_rs_rt": bool(_RS_RT_RE.match(cls)),
        "is_stats": bool(_STATS_SUFFIX_RE.search(cls)),
        "n_name_toks": len(_tokenize(cls)),
        "name_len": len(cls),
    }


# Single-slot cache: (source dict identity, built index). `descriptions` is
# loaded once at server startup and reused for the process lifetime, so in
# production this builds the ~15k-entry index exactly once. Comparing by
# object identity (not a dict keyed by id()) avoids the classic id-reuse
# hazard of caching against a garbage-collected object's id — we hold a real
# reference to the source dict here, so a distinct dict (e.g. a fresh copy
# built by a test) always correctly misses the cache and rebuilds.
_index_cache: tuple[dict, list[dict]] | None = None


def _get_index(descriptions: dict[str, dict[str, str]]) -> list[dict]:
    """Return the cached tokenized index for `descriptions`, building it if needed."""
    global _index_cache
    if _index_cache is not None and _index_cache[0] is descriptions:
        return _index_cache[1]

    index = [_build_entry(cls, meta) for cls, meta in descriptions.items()]
    _index_cache = (descriptions, index)
    return index


def load_descriptions(path: Path) -> dict[str, dict[str, str]]:
    """Load class-descriptions.json into memory.

    Args:
        path: Absolute path to the class-descriptions.json file.

    Returns:
        Dict mapping ACI class name → {"label": str, "comment": str,
        "prop_labels": list[str], "isConfigurable": bool, "isAbstract": bool}.
        All keys but the class name may be absent when the source schema had
        no value (or the flag was False — both fields are omitted rather than
        written as `false`, to keep the file compact).

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


def _score(
    q_lc: str, squash: str, qset: frozenset[str], n: int, e: dict
) -> float:
    """Score one class entry against a tokenized query.

    See the module docstring for the rationale behind each term. Returns 0
    for a class that should not appear in results at all (no signal found).
    """
    s = 0.0

    if q_lc and q_lc == e["label_lc"]:
        s += 20.0
    if q_lc and q_lc == e["jargon_lc"]:
        s += 18.0
    if squash and squash == e["cls_lc"]:
        s += 25.0
    if q_lc and (q_lc in e["label_lc"] or (e["jargon_lc"] and q_lc in e["jargon_lc"])):
        s += 6.0

    if qset:
        cov_label = len(qset & e["label_toks"]) / n
        cov_name = len(qset & e["name_toks"]) / n
        s += 8.0 * cov_label * cov_label + 5.0 * cov_name * cov_name

        if q_lc and q_lc in e["prop_haystack"]:
            s += 6.0
        else:
            cov_prop = len(qset & e["prop_toks"]) / n
            s += 2.0 * cov_prop * cov_prop

        if q_lc and q_lc in e["comment_lc"]:
            s += 2.0
        else:
            cov_comment = len(qset & e["comment_toks"]) / n
            s += 1.0 * cov_comment * cov_comment

        alias_hits = sum(
            1
            for t in qset
            if _SYNONYMS.get(t) and _SYNONYMS[t] & (e["name_toks"] | e["label_toks"])
        )
        if alias_hits:
            s += 3.0 * alias_hits / n

    if s <= 0:
        return 0.0

    # Structural priors — reflect what the object structurally is, not what
    # it happens to be called.
    if e["configurable"]:
        s += 6.0
    if e["abstract"]:
        s -= 6.0
    if e["is_stats"]:
        s -= 10.0
    if e["is_rs_rt"]:
        s -= 8.0

    return s


def search(
    keyword: str,
    descriptions: dict[str, dict[str, str]],
    limit: int = 10,
) -> list[dict[str, str]]:
    """Search class descriptions by keyword with relevance ranking.

    Tokenizes the query (camelCase-aware, so "fabric node" and "fabricNode"
    are treated the same way) and scores every class on: exact label/jargon
    match, exact class-name match, phrase and token-coverage matches against
    the label, class name, property labels, and comment, plus a small curated
    synonym table. Structural priors then adjust the raw text score:
    `isConfigurable` classes are boosted (a query almost always wants the
    object you can actually configure), `isAbstract`, Rs/Rt relation, and
    stats/telemetry classes are penalized (see module docstring for the full
    rationale). Classes with a final score at or below zero are excluded.

    Results are sorted by descending score; ties are broken by fewer
    class-name tokens, then a shorter class name, then alphabetically — all
    deterministic, so identical input always produces identical output.

    Args:
        keyword:      Case-insensitive search term (plain English or partial class name).
        descriptions: In-memory descriptions dict from load_descriptions().
        limit:        Maximum number of results to return. Values below 1
                      (zero or negative) are treated as 1 rather than passed
                      through to the final `results[:limit]` slice, where a
                      negative value would otherwise silently drop entries
                      from the end instead of the intended "return fewer
                      results" behaviour.

    Returns:
        List of dicts, each containing:
          class_name — ACI class name (e.g. "fvBD")
          label      — short human-readable label (may be empty)
          comment    — one-sentence description (may be empty)
    """
    kw = keyword.lower().strip()
    if not kw:
        return []
    limit = max(1, limit)

    squash = re.sub(r"[\s\-_]", "", kw)
    qset = frozenset(_tokenize(keyword))
    n = len(qset) or 1

    index = _get_index(descriptions)
    scored: list[tuple[float, int, int, str]] = []
    for e in index:
        s = _score(kw, squash, qset, n, e)
        if s > 0:
            scored.append((s, e["n_name_toks"], e["name_len"], e["cls"]))

    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3]))

    by_cls = descriptions
    return [
        {
            "class_name": cls,
            "label": by_cls[cls].get("label", ""),
            "comment": by_cls[cls].get("comment", ""),
        }
        for _, _, _, cls in scored[:limit]
    ]
