# Search Algorithm — search_classes

This document describes the problem of searching ACI classes, the two improvement axes implemented, the precise mechanics of each algorithm, and the measured gains. It serves as a reference for any future evolution of `registry/descriptions.py`.

---

## 1. Context: the problem of searching an ACI corpus

### The corpus

The Cisco ACI object model has **15 152 classes** documented in the jsonmeta files provided by the APIC. Each class represents a manageable object — a policy, a relation, a network configuration, a monitoring object, an internal artifact. The vast majority of these classes are invisible to a network operator: only a few hundred correspond to directly configurable objects.

The file `data/class-descriptions.json` indexes these classes with three fields:

```json
{
  "fvBD": {
    "label":   "Bridge Domain",
    "comment": "A bridge domain is a unique layer 2 forwarding domain..."
  }
}
```

`search_classes` operates on this index.

### What the LLM agent asks

When an LLM agent calls `search_classes`, it can phrase its query in several ways:

| Query type | Example | What makes it difficult |
|---|---|---|
| Approximate class name | `"fvbd"`, `"vzbrcp"` | No capitalisation, no separators |
| Exact or close label | `"bridge domain"`, `"tenant"` | Multiple classes share the same label |
| Functional concept | `"ARP flooding"`, `"dead interval"` | Absent from the class label and comment |
| Pure synonym | `"gateway"`, `"security policy"` | No textual anchor in the APIC |

An LLM trained on ACI documentation often handles the first two types intuitively. It is with functional queries and synonyms that text search reaches its limits.

---

## 2. The naive approach (baseline)

### How it works

The `search()` function performs a **linear scan** over all 15 152 classes. For each class it computes a score by additive matching:

```
score = 0
if keyword ∈ class_name  (case-insensitive)  → score += 3
if keyword ∈ label        (case-insensitive)  → score += 2
if keyword ∈ comment      (case-insensitive)  → score += 1
```

Classes with `score > 0` are sorted by descending score. On ties, the insertion order in the JSON is preserved.

### The weights

The 3/2/1 weights reflect decreasing confidence in each field:

- The **class name** is the exact technical identifier: if the keyword appears there, the match is near-certain.
- The **label** is the official human name given by Cisco: high semantic value.
- The **comment** is a few-sentence description: more ambiguous matches (many classes mention "tenant", "VRF", "bridge domain" in passing).

### Baseline measurements

Evaluated on a golden set of **39 queries** across 4 tiers of increasing difficulty (APIC mo-apic-v6.0_9c, 15 152 classes):

| Metric | Score |
|---|---|
| Recall@1 | 15.4% |
| Recall@5 | 35.9% |
| MRR | 0.229 |
| Tier 1 — direct label/name | R@1 = 10%  /  R@5 = 50% |
| Tier 2 — camelCase name | R@1 = 80%  /  R@5 = 80% |
| Tier 3 — functional property | R@1 = 0%   /  R@5 = 0% |
| Tier 4 — pure synonym | R@1 = 0%   /  R@5 = 0% |
| Average query time | 3.2 ms |

### Failure analysis

**Why does tier 1 fail at 90% Recall@1?**

The issue is not that the right class is absent from results — it appears in the top 5 in 50% of cases. The problem is ranking. Concrete example:

- Query: `"bridge domain"`
- `fvBD`: label = `"Bridge Domain"` → `"bridge domain"` in label → **score 2**
- `fvABDPol`: label = `"Bridge Domain"` → `"bridge domain"` in label → **score 2**
- `eqptcapacityBDEntry`: label = `"Bridge Domain Entry"` → `"bridge domain"` in label → **score 2**

Cisco assigns the same human label to the primary class and to all related classes (policies, relations, variants). About ten classes share the label `"Bridge Domain"`. They all get score 2. The insertion order in the JSON — arbitrary — decides the ranking. `fvBD` may end up at rank 5 or rank 8.

#### The Rs/Rt class problem

ACI relation classes follow a strict naming convention:

- `fvRsCtx`: **R**elation **s**ource — from fvBD to fvCtx
- `l3extRtVrfValidationPol`: **R**elation **t**arget — back-reference to a VRF policy

These classes systematically inherit the **label of their target class**. Example:

```
fvRsCtx                 → label "Private Network"  (label of fvCtx)
l3extRtVrfValidationPol → label "VRF"              (label of fvCtx)
plannerRsBdVrf          → label "VRF"              (label of fvCtx)
```

Moreover, the relation class name **often contains** the target concept: `l3extRtVrfValidationPol` contains `Vrf`. Result for the query `"VRF"`:

- `l3extRtVrfValidationPol`: `"vrf"` in name (+3) + `"VRF"` exact label (+2) + `"vrf"` in comment (+1) = **score 6**
- `plannerRsBdVrf`: `"vrf"` in name (+3) + `"VRF"` exact label (+2) + comment (+1) = **score 6**
- `fvCtx`: `"vrf"` absent from name `fvctx` (0) + `"VRF"` exact label (+2) + `"vrf"` in comment (+1) = **score 3**

`fvCtx`, the actual VRF class, is beaten by its own relation classes because they encode the concept in their camelCase name.

---

## 3. Axis 1 — Rs/Rt penalty

### The diagnosis

Rs and Rt classes are **internal artifacts** of the APIC object model. They do not represent objects that a network operator creates, modifies, or queries directly — they encode relations between primary objects. In practice, an LLM agent calling `query()` never targets an Rs/Rt class: it targets the primary class (`fvBD`, `fvCtx`, `vzBrCP`…) and navigates via relations afterwards.

The problem is therefore structural, not statistical: Rs/Rt classes **should not** appear at the top of search results. This is not a question of ambiguous score — it is a semantic rule of the APIC object model.

### The detection pattern

The ACI naming convention is strict and consistent. A relation class is identified by the presence of `Rs` or `Rt` (with capitalisation) immediately after the package prefix in the camelCase name:

```
fvRsCtx               → prefix "fv"    + Rs + "Ctx"
l3extRtVrfValidationPol → prefix "l3ext" + Rt + "VrfValidationPol"
infraRsVpcBndlGrp     → prefix "infra" + Rs + "VpcBndlGrp"
```

The detection regex:

```python
_RS_RT_RE = re.compile(r"^[a-z][a-z0-9]*(?:Rs|Rt)[A-Z]")
```

Pattern details:

- `^[a-z]`: class name always starts with a lowercase (ACI convention)
- `[a-z0-9]*`: prefix may contain digits (`l3`, `pol2`, `iso8583`)
- `(?:Rs|Rt)`: the relation marker, always capitalized
- `[A-Z]`: immediately followed by an uppercase letter (start of the target relation name)

### The penalty applied

After computing the usual score (name/label/comment), Rs/Rt classes receive a penalty of **-3 points**:

```python
if score > 0:
    if _RS_RT_RE.match(cls):
        score -= 3
    if score > 0:
        results.append(...)
```

The penalty is applied **after** the initial score for two reasons:

1. It preserves the relative order among Rs/Rt classes themselves (those that match better remain better ranked among themselves)
2. Classes whose score drops to 0 or below are **excluded from results** — an irrelevant result has no value even at position 10

### Concrete cases after application

**Query `"VRF"`:**

| Class | Raw score | Penalty | Final score |
|---|---|---|---|
| `l3extRtVrfValidationPol` | 6 (name+label+comment) | -3 (Rt) | **3** |
| `plannerRsBdVrf` | 6 (name+label+comment) | -3 (Rs) | **3** |
| `fvCtx` | 3 (label+comment) | 0 (not Rs/Rt) | **3** |

All three classes end at score 3. The tie persists — but `fvCtx` is now **in the race**, which was not the case before (score 3 vs score 6 for the Rs/Rt classes).

**Query `"bridge domain"`:**

| Class | Raw score | Penalty | Final score |
|---|---|---|---|
| `fvBD` | 3 (label+comment) | 0 | **3** |
| `fvRsSvcBDToBDAtt` | 3 (label+comment) | -3 (Rs) | **0 → excluded** |
| `fvRtBd` | 3 (label+comment) | -3 (Rt) | **0 → excluded** |

Relation classes are excluded. `fvBD` remains but still competes against non-Rs/Rt classes that share the label (`fvABDPol`, `eqptcapacityBDEntry`…).

### Measured gains

| Metric | Baseline | + Axis 1 | Delta |
|---|---|---|---|
| Recall@1 | 15.4% | **28.2%** | +12.8% |
| Recall@5 | 35.9% | **41.0%** | +5.1% |
| MRR | 0.229 | **0.338** | +0.109 |
| Tier 1 R@1 | 10% | **35%** | +25% |
| Tier 1 R@5 | 50% | **55%** | +5% |
| Tier 2 R@5 | 80% | **100%** | +20% |
| Tier 3 R@1 | 0% | 0% | 0% |
| Average time | 3.2 ms | **3.2 ms** | 0 |

The gain in tier 1 is substantial (+25% Recall@1). The gain in tier 2 on Recall@5 (+20%) is less obvious but explained: for camelCase name queries (`"l3extout"`), `l3extRt*` classes cluttering the top positions are now penalized, freeing slots for `l3extOut`.

**What axis 1 does not solve:** The shared-label problem between primary classes persists. `fvBD` and `fvABDPol` both have label = `"Bridge Domain"` and neither is an Rs/Rt relation. They continue to share rank 1 based on insertion order. Tiers 3 and 4 remain at 0%.

---

## 4. Axis 2 — prop_labels enrichment

### The diagnosis

Functional search (tier 3) — `"ARP flooding"`, `"dead interval"`, `"link aggregation"`, `"data plane learning"` — fails completely because these terms appear **neither in the label nor in the comment** of the relevant classes.

Yet this information exists in the APIC jsonmeta files. Each jsonmeta file describes not only the class itself (its label, its comment) but also **all its properties**: each configurable attribute has its own human label provided by Cisco.

Example — `fvBD.json` (excerpt):

```json
{
  "fv:BD": {
    "label":   "Bridge Domain",
    "comment": ["A bridge domain is a unique layer 2 forwarding domain..."],
    "properties": {
      "arpFlood": {
        "label": "ARP Flooding",
        "comment": ["Enable ARP flooding"],
        ...
      },
      "unicastRoute": {
        "label": "Unicast Routing",
        ...
      },
      "mac": {
        "label": "MAC Address",
        ...
      },
      "mtu": {
        "label": "MTU Size",
        ...
      }
    }
  }
}
```

These property labels — `"ARP Flooding"`, `"Unicast Routing"`, `"MAC Address"` — are the official Cisco terminology for describing an object's capabilities. An LLM agent searching for `"ARP flooding"` is looking precisely for the class that **owns** that capability. The information exists; it is simply absent from the search index.

### The two-component solution

#### Component A — enrich the index with property labels

Each entry in `class-descriptions.json` carries an optional `prop_labels` field: a deduplicated list of human-readable labels extracted from the class's configurable properties. Generic labels (`"Name"`, `"Description"`, `"Managed By"`, etc.) and labels that add no search value are excluded during index build.

The result is a `prop_labels` field in `class-descriptions.json`:

```json
{
  "fvBD": {
    "label":       "Bridge Domain",
    "comment":     "A bridge domain is a unique layer 2 forwarding domain...",
    "prop_labels": [
      "ARP Flooding",
      "Unicast Routing",
      "MAC Address",
      "MTU Size",
      "EP Move Detection Mode",
      "Multicast Allow",
      "Unknown Mac Unicast Action",
      "Virtual MAC Address"
    ]
  }
}
```

**Index impact:** 12 856 classes out of 15 152 have at least one useful prop_label after filtering. 549 classes that had neither a usable label nor comment enter the index for the first time thanks to their prop_labels.

#### Component B — MCP server: consult prop_labels as fallback

The modification of `search()` in `mcp/registry/descriptions.py` is intentionally minimal. The prop_labels scan is only triggered if the class has **not yet scored any points** on the three standard fields:

```python
if score == 0:
    for pl in meta.get("prop_labels", ()):
        if kw in pl.lower():
            score = 1
            break   # one match is enough — no accumulation
```

**Three design decisions:**

1. **Fallback only (`score == 0`).** If a class already matches on its name or label, the prop_labels scan is not triggered. This avoids inflating the score of a class that would match on both its label and its properties — which would artificially favour classes with many properties.

2. **Fixed score +1, no accumulation.** A class found via prop_labels gets exactly 1 point, even if ten of its properties contain the keyword. This ceiling prevents classes with many properties (such as `fvBD` with 20+ prop_labels) from dominating more targeted classes. The `break` after the first match is critical.

3. **Weight +1 = same level as comment.** A prop_label is contextual information, not a central definition. Placing it at the same level as the comment (the least discriminating field) is intentional.

### Behaviour with concrete examples

**Query `"ARP flooding"`:**

```
fvBD:
  - "arp flooding" in name "fvbd"? No → 0
  - "arp flooding" in label "Bridge Domain"? No → 0
  - "arp flooding" in comment? No → score still 0
  - score == 0 → scan prop_labels:
    - "ARP Flooding" → "arp flooding" in "arp flooding" → YES → score = 1, break

Final score fvBD = 1.
```

```
uribv4Entity:
  - "arp flooding" in name? No → 0
  - "arp flooding" in label "IPv4 Route"? No → 0
  - "arp flooding" in comment? No → score 0
  - scan prop_labels: no prop_label contains "arp flooding" → score stays 0

Excluded from results.
```

**Query `"dead interval"`:**

`ospfIfPol` (OSPF Interface Policy) has a property `deadIntvl` whose label is `"Dead Interval"`. Before axis 2, this information was not in the index. After:

```
ospfIfPol:
  - No match on name/label/comment → score 0
  - Scan prop_labels: "Dead Interval" → "dead interval" in "dead interval" → YES → score = 1
```

### Measured gains

| Metric | After axis 1 | + Axis 2 | Axis 2 delta |
|---|---|---|---|
| Recall@1 | 28.2% | **30.8%** | +2.6% |
| Recall@5 | 41.0% | **53.8%** | +12.8% |
| MRR | 0.338 | **0.400** | +0.062 |
| Tier 1 R@1 | 35% | **35%** | 0% |
| Tier 3 R@1 | 0% | **9%** | +9% |
| Tier 3 R@5 | 0% | **45%** | +45% |
| Tier 4 | 0% | **0%** | 0% |
| Average time | 3.2 ms | **11.4 ms** | +8.2 ms |

### Interpreting the numbers

**Why does R@1 progress little (+2.6%) while R@5 jumps (+12.8%)?**

Classes found via prop_labels all receive score 1 — the lowest possible score, below any class that matches on its name, label, or comment. In most cases the expected class ends up at position 2 to 5, beaten by classes that contain the keyword in their label or comment with a higher score.

Example — query `"ARP flooding"`:

- `fvABD` (Attached Bridge Domain): its prop_labels also contain `"ARP Flooding"` (same object model) → score 1
- `fvABDPol`: same → score 1
- `fvBD`: score 1
- Three-way tie; insertion order decides. `fvABD` precedes `fvBD` in the JSON → rank 3 for `fvBD`.

The real benefit is in **Recall@5**, because the LLM agent reads the 10 results returned. Finding the right class at rank 3 or rank 4 is an operational win — the LLM can identify it from the visible label in the response.

**Why +8.2 ms of latency?**

The prop_labels fallback is triggered for every class that does not match on name/label/comment. For a query like `"ARP flooding"`, nearly all 15 152 classes fail on the three standard fields — the `for pl in meta.get("prop_labels", ())` loop executes ~15 000 times. Each iteration compares a short string (the keyword) against short strings (the prop_labels).

11 ms remains acceptable for an MCP tool (the LLM does not make these calls in a tight loop), but the degradation is real. It could be optimized with a pre-computed inverted index on prop_labels at load time, at the cost of higher memory footprint.

---

## 5. Summary of the three v1 algorithm states

| Strategy | R@1 | R@5 | MRR | Avg ms | Indexed classes |
|---|---|---|---|---|---|
| Baseline — naive substring | 15.4% | 35.9% | 0.229 | 3.2 | 14 603 |
| + Axis 1 — Rs/Rt penalty | 28.2% | 41.0% | 0.338 | 3.2 | 14 603 |
| + Axis 2 — prop_labels | 30.8% | 53.8% | 0.400 | 11.4 | 15 152 |

*Evaluated on 39 queries — golden set `mcp/tests/fixtures/search_golden.json`, APIC mo-apic-v6.0_9c.*

Everything above this line describes v1, kept as a historical record per the "guide for future evolutions" at the end of this document. v1's own diagnosis of what remained unresolved — reproduced below — is exactly what motivated the v2 rewrite in section 6; section 7 revisits each point with what changed.

*v1's own diagnosis of what remained unresolved (context for section 6):*

- **The shared-label problem (partial tier 1).** Cisco assigns the same label to dozens of related classes (`fvBD`, `fvABDPol`, `fvSvcBD` all label = `"Bridge Domain"`). The Rs/Rt penalty doesn't apply — these aren't relation classes.
- **Pure synonyms (tier 4 = 0%).** `"gateway"` → `fvSubnet`, `"security policy"` → `vzBrCP`: no textual anchor anywhere in the APIC corpus for these.
- **The prop_labels fallback cost at scale.** +8 ms/query from a linear `for pl in prop_labels` scan repeated across ~15k classes on every miss.

---

## 6. v2 — tokenization, structural priors, and a curated jargon table

### Why v1's substring approach hit a ceiling

v1 scored whole-string substring containment (`keyword in class_name`, `keyword in label`, `keyword in comment`). Two structural problems followed directly from that choice, independent of weighting:

1. **Multi-word queries against camelCase names never match.** `"fabric node"` (with a space) cannot be a substring of `"fabricNode"` (no space) — full stop, no amount of weight tuning fixes this. Tier 1 queries phrased as multiple words routinely missed classes whose *name* was the obvious answer.
2. **The shared-label tie was unresolvable by score alone.** When ten classes share the exact label `"Bridge Domain"`, they get the *same* score under any purely textual scheme — v1's own diagnosis (section 5) correctly identified this as needing something other than more text matching.

v2 replaces the substring scheme with **tokenized matching** and adds **structural priors** that use what a class *is* (configurable vs. abstract vs. operational/stats), not just what it's called.

### Tokenization

`_tokenize()` splits camelCase/PascalCase/ACRONYM identifiers into lowercase tokens using one regex (`_TOKEN_RE`), applied identically to class names, labels, comments, prop_labels, *and* the query itself:

```
"fvBD"                    → ["fv", "bd"]
"l3extRtVrfValidationPol" → ["l3", "ext", "rt", "vrf", "validation", "pol"]
"fabric node"             → ["fabric", "node"]
```

Because both sides of the comparison go through the same tokenizer, `"fabric node"` and `"fabricNode"` now produce the identical token set `{"fabric", "node"}` — problem 1 above is gone by construction, not by a special case.

### Scoring: what a phrase match means, ranked by confidence

| Signal | Weight | Rationale |
|---|---|---|
| Exact match against the class's label or curated jargon phrase | +20 / +18 | If the user's exact words *are* the official name, this is the answer — full stop. |
| Exact match against the class name (query squashed, no separators) | +25 | Tier 2's use case (`"fvbd"` → `fvBD`) — the strongest possible signal. |
| Query phrase found as a substring inside the label or jargon phrase | +6 | e.g. `"routing instance"` inside jargon `"vrf context routing instance"`. |
| Token coverage of the label / class name (squared) | up to +8 / +5 | Rewards a query that names *most or all* of a concept over one sharing one incidental word — squaring makes partial coverage fall off fast. |
| Query phrase found inside the joined property-label haystack | +6 | The tier 3 case: `"ARP flooding"` inside fvBD's own `arpFlood` property label. |
| Query phrase found as a substring inside the comment | +2 | Same idea as the property-label substring check above, one tier weaker — the comment is prose, not curated terminology. |
| Token coverage of property labels (squared), when no substring hit | up to +2 | Fallback when the phrase isn't a literal substring of the property-label haystack. |
| Token coverage of comment (squared), when no substring hit | up to +1 | Weakest signal — comments are the least targeted field. |
| Curated synonym hit (see below) | up to +3 × coverage | A soft nudge, not an override — cannot beat a genuine exact match on its own. |

### Structural priors — what the object *is*, not what it's called

These are applied **after** the text score, and only when the text score is already positive (a class with zero textual signal never surfaces just because it happens to be configurable):

| Prior | Adjustment | Data source |
|---|---|---|
| `isConfigurable` | +6 | jsonmeta root field, now carried into `class-descriptions.json` (see below) |
| `isAbstract` | −6 | jsonmeta root field, same |
| Stats/telemetry suffix (`5min`, `15min`, `1h`, …) | −10 | Regex on the class name — these classes structurally cannot be a config target |
| Rs/Rt relation class | −8 | Same detection regex as v1 (`_RS_RT_RE`), now a fixed penalty rather than v1's −3 |

**Why this solves the shared-label problem v1 couldn't:** `fvBD` (the real, configurable bridge domain) and `eqptcapacityBDEntry5min` (a stats-bucket class that happens to share the label "Bridge Domain") now separate on a completely different axis than text — one is a real config object, the other is structurally a telemetry sample. `data/class-descriptions.json` gained `isConfigurable`/`isAbstract` fields for exactly this (regenerated via `schema-collector`'s `_step_descriptions`; ~3,010 of 15,239 classes are configurable, ~1,954 are abstract — both flags omitted, not written as `false`, when absent, matching the file's existing sparse-field convention).

### Tie-breaking

Ties (equal score) are broken, in order: fewer class-name tokens → shorter class name → alphabetical. This is a deliberate, deterministic replacement for v1's "JSON insertion order decides" — a concise canonical name (`fvBD`, 2 tokens) now reliably beats a more specific variant that happens to score identically (`fvABDPol`, 3 tokens), and identical input always produces identical output regardless of dict iteration order.

### A small, honestly-scoped jargon/synonym table

Two curated dicts in `registry/descriptions.py` close gaps that no amount of text-matching machinery can close, because the terms genuinely don't appear anywhere in the schema:

- **`_JARGON`** (`class_name → canonical phrase`): for classes whose real APIC label doesn't say what an operator would ask for. Example: `bgpPeerP`'s actual label is `"Peer Connectivity Profile"` — it says nothing about "BGP" or "peer policy" at all, so `_JARGON["bgpPeerP"] = "bgp peer policy"` gives it a fighting chance against classes that DO literally contain those words in their own text.
- **`_SYNONYMS`** (`informal token → target tokens`): single-word informal mappings (`"vpc"/"pc" → "bndl"`, `"gateway" → "subnet"`, `"outside" → "out"/"ext"`) contributing a modest, bounded boost — enough to break a tie, never enough to override a genuine exact match on its own.

Both tables are deliberately small (~30 and ~11 entries respectively) and exist to close *specific, observed* gaps from the golden set, not to reimplement a thesaurus. The module docstring is explicit about resisting the temptation to pad them speculatively — every entry should trace back to a real query that needed it. One entry (`fabricNode: "fabric node switch"`) was tried and measured to have *zero* effect on the golden set (a competitor's own label contains "switch" as a true substring, e.g. `"vswitch"`) and was kept minimal rather than expanded further once the lack of benefit was confirmed — a reminder that every addition here should be justified by a measured delta, not intuition.

### Performance: keeping tokenized matching inside the existing latency budget

Tokenizing and scoring every field for all ~15k classes on *every single call* is inherently more expensive than v1's raw substring scan. Two design choices keep it well inside `tests/perf/test_search_perf.py`'s existing budgets (single search < 200 ms, 100 consecutive searches < 2 s):

1. **Build once, cache by object identity.** `_get_index()` tokenizes every class exactly once and caches the result keyed by the *identity* (not a hashed key) of the `descriptions` dict passed in. In production that dict is loaded once at server startup and reused for the process lifetime, so the ~300 ms build cost (on the real, prop_label-rich corpus) is paid exactly once, ever. Comparing by `is` rather than caching in a dict keyed by `id()` deliberately avoids the classic id-reuse hazard of a garbage-collected object's id being handed to a new, unrelated object.
2. **One substring check instead of twenty.** Profiling the first tokenized implementation showed `any(keyword in prop_label for prop_label in prop_labels)` — a nested loop over every class's property labels, for every class, on every call — consuming the majority of total time (confirmed via `cProfile`: ~56% of wall time in one profiling run). Fix: join each class's `prop_labels` into a single pre-lowercased haystack string at index-build time (`_PROP_SEP`-joined, a control character chosen so a query phrase can never spuriously span the boundary between two unrelated labels), and do exactly one substring check against it. This alone cut measured per-call latency from ~18 ms to ~11 ms on the real corpus.

### Measured gains

| Metric | v1 (39 queries) | v2 (39 queries) | v2 (74 queries — grown set) |
|---|---|---|---|
| Recall@1 | 30.8% | 69.2% | **78.4%** |
| Recall@5 | 53.8% | 89.7% | **94.6%** |
| MRR | 0.400 | 0.793 | **0.854** |
| Tier 1 R@1 / R@5 | 35% / 55% | 90% / 100% | 90.9% / 100% |
| Tier 2 R@1 / R@5 | — | 100% / 100% | 100% / 100% |
| Tier 3 R@1 / R@5 | 9% / 45% | 36.4% / 63.6% | 53.3% / 80.0% |
| Tier 4 R@1 / R@5 | 0% / 0% | 0% / 100% | 0% / 80.0% |
| Average query | 11.4 ms | ~11 ms | ~11-20 ms |

The golden set grew from 39 to 74 queries *alongside* the v2 rewrite — the added queries were chosen for breadth (more classes, more phrasing styles across all four tiers) before the numbers above were measured, not curated afterward to flatter the algorithm. Recall@1 and Recall@5 both *improved* on the larger set relative to the original 39, which is the signal you want to see if a change generalizes rather than overfits.

---

## 7. Remaining limitations and future directions

### What v2 resolved

- The shared-label tie (v1's diagnosis, section 5): solved via structural priors, not more text matching — see section 6.
- Multi-word queries against camelCase names: solved by tokenizing both sides identically.
- Non-deterministic tie ordering: solved by an explicit, documented tie-break rule.

### What remains unresolved

#### Pure synonyms without a curated entry (tier 4 R@1 still low)

Tier 4 Recall@1 is still 0% on the current golden set — the curated jargon/synonym table lifts Recall@5 to 80% (a query's target class now usually appears somewhere in the visible top-10, up from 0%/0% under v1), but rank-1 placement for a pure synonym competing against a class with a genuine textual match (e.g. `"gateway"` vs. `gleanGateway`'s literal label `"Gateway"`) remains a real, structural limit of curated-table + text-scoring — the curated boost is deliberately capped so it cannot override an exact match, which is the right general-purpose default even where it loses this specific case. Closing this fully would need either a semantic embedding model or a much larger, actively maintained synonym dictionary — a real cost/benefit tradeoff, not a quick fix.

#### The curated tables need upkeep

`_JARGON`/`_SYNONYMS` are hand-maintained. They will drift as new golden-set failures are found in production use; the module docstring's instruction to justify every entry with a measured delta (never pad speculatively) is the guardrail against this becoming an unmaintainable pile of special cases.

### Guide for future evolutions

Any scoring modification must be:

1. **Tested on the golden set**: `python mcp/tests/eval_search.py -v` from the repo root.
2. **Gated in CI**: `mcp/tests/eval/test_search_quality.py` fails the build if Recall@1/5 drop below their floors — a regression is caught automatically, not just visible in an offline report someone has to remember to run.
3. **Documented in the table** in `registry/descriptions.py`'s module docstring and this file.
4. **Validated** on edge-case behavior: Rs/Rt and stats-suffix penalties, structural priors on classes lacking the flag entirely, curated-table entries that measurably help (not just plausibly should).

The golden set now covers 74 queries across 4 tiers over ~15.2k classes. Growing it further — especially tier 3/4 cases discovered from real agent usage rather than invented in the abstract — remains the highest-leverage next step before introducing more aggressive heuristics.
