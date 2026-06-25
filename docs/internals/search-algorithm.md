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

## 5. Summary of the three algorithm states

| Strategy | R@1 | R@5 | MRR | Avg ms | Indexed classes |
|---|---|---|---|---|---|
| Baseline — naive substring | 15.4% | 35.9% | 0.229 | 3.2 | 14 603 |
| + Axis 1 — Rs/Rt penalty | 28.2% | 41.0% | 0.338 | 3.2 | 14 603 |
| + Axis 2 — prop_labels | **30.8%** | **53.8%** | **0.400** | 11.4 | **15 152** |

*Evaluated on 39 queries — golden set `mcp/tests/fixtures/search_golden.json`, APIC mo-apic-v6.0_9c.*

---

## 6. Remaining limitations and future directions

### What remains unresolved

#### The shared-label problem (partial tier 1)

Cisco assigns the same label to dozens of related classes. `fvBD`, `fvABDPol`, `fvSvcBD` all have label = `"Bridge Domain"`. An Rs/Rt penalty cannot resolve this case — these classes are not relations. A secondary sort by class name length (canonical classes are systematically shorter: `fvBD` = 4 chars vs `fvABDPol` = 8 chars) would solve some cases but has not yet been validated on the full corpus.

#### Pure synonyms (tier 4 = 0%)

`"gateway"` → `fvSubnet`: no property of `fvSubnet` is called "gateway". The APIC label for the IP property is `"Subnet"`. `"security policy"` → `vzBrCP`: the contract properties speak of "QoS Class", "Scope", "DSCP" — not "security". These associations have no textual anchor in the APIC corpus.

Resolving tier 4 would require either:

- A semantic embedding model (vector similarity between "gateway" and "first-hop IP address of a subnet")
- A manually maintained ACI synonym dictionary
- A fine-tuned model on Cisco ACI documentation

#### The prop_labels fallback cost at scale

+8 ms per query is acceptable with 15k classes. If the corpus grows significantly (new APIC versions with more classes), the linear scan will become a bottleneck. A pre-computed inverted index (`{prop_label_word: [class_name, ...]}`) built at server load time would reduce complexity from O(n × k) to O(1) on prop_labels.

### Guide for future evolutions

Any scoring modification must be:

1. **Tested on the golden set**: `python mcp/tests/eval_search.py -v` from the repo root
2. **Documented in the table** in `mcp/tests/eval_search.py` (file header)
3. **Validated** on edge-case behavior: Rs/Rt that survive when their score exceeds 3, prop_labels that do not accumulate, empty edge cases

The golden set covers 4 tiers but remains a sample of 39 queries over 15 152 classes. An improvement that gains +5% on the golden set may have regressions not visible in the sample. Growing the golden set to 100+ queries before introducing more aggressive heuristics (secondary sort, length-based weighting) is recommended.
