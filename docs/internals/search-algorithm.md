# Search Algorithm — search_classes

This document describes the problem of searching ACI classes, the two v1
improvement axes, the v2 rewrite that replaced them, and the measured results.
It is the reference for any future evolution of `registry/descriptions.py`.

**2.0 changed the source of the index, not the algorithm.** Every weight, prior,
tie-break rule and curated table below is exactly what shipped in 1.2.2; the
index they run over is now rebuilt from niwaki's catalogue at startup instead of
read from a JSON file. `mcp/tests/unit/test_search_source.py` asserts the two
sources produce the *same top-5 for every golden query* — equality, not a floor —
which is why the numbers in this document did not move across the migration.

---

## 1. Context: the problem of searching an ACI corpus

### The corpus

The Cisco ACI object model that ships in the catalogue holds **15 452 classes**
(APIC 6.0(9c)). Each one is a manageable object — a policy, a relation, a
network configuration, a monitoring object, an internal artifact. The vast
majority are invisible to a network operator: only a few hundred correspond to
directly configurable objects.

`search_classes` does not search all of them. It searches the **15 239** classes
that have something searchable in them:

| | Count |
|---|---|
| Classes in the catalogue (queryable via `query` / `count`) | 15 452 |
| Indexed, therefore findable by `search_classes` | 15 239 |
| … carrying at least one textual field | 15 152 |
| … with a label | 13 681 |
| … with a comment | 12 129 |
| … with usable property labels | 12 856 |
| … reachable *only* through property labels | 549 |
| … flagged `isConfigurable` | 3 010 |
| … flagged `isAbstract` | 1 954 |
| Relation (`Rs`/`Rt`) classes in the index | 3 065 |
| Stats/telemetry classes (time-bucket suffix) in the index | 4 769 |

The 213-class gap between 15 452 and 15 239 is not a defect: those classes have
no label, no comment and no usable property label, so there is nothing to index.
They stay fully queryable — class validation reads the catalogue's `mo` table,
not this index. See [registry.md](registry.md#descriptions_index) for how the
index is built and filtered.

87 of the 15 239 entries carry only structural flags and no text at all. They can
never be returned, because structural priors are applied *after* a positive text
score — never as a score of their own.

### The index

`catalog.descriptions_index()` builds it once, in the server lifespan. Each entry
is sparse — absent fields simply do not appear:

```json
{
  "fvBD": {
    "label": "Bridge Domain",
    "comment": "A bridge domain is a unique layer 2 forwarding domain that contains one or more subnets. Each bridge domain must be linked to a context.",
    "prop_labels": [
      "Optimize Wan Bandwidth between sites", "ARP Flooding",
      "Clear Endpoints", "EP Move Detection Mode", "Ip Learning",
      "MAC Address", "MTU Size", "Unicast Routing",
      "Unknown Mac Unicast Action", "Virtual MAC Address"
    ],
    "isConfigurable": true
  }
}
```

(`fvBD` carries 23 property labels; the list above is abridged.)

The build costs ~430 ms once at startup. `search()` then tokenises that dict once
and caches the tokenised form by object identity, so a query pays neither cost.

### What the LLM agent asks

| Query type | Example | What makes it difficult |
|---|---|---|
| Approximate class name | `"fvbd"`, `"vzbrcp"` | No capitalisation, no separators |
| Exact or close label | `"bridge domain"`, `"tenant"` | 30 classes share the exact label `"Bridge Domain"`; 15 share `"VRF"` |
| Functional concept | `"ARP flooding"`, `"dead interval"` | Absent from the class label and comment |
| Pure synonym | `"gateway"`, `"security policy"` | No textual anchor in the APIC |

An LLM trained on ACI documentation often handles the first two intuitively. It
is with functional queries and synonyms that text search reaches its limits.

---

## 2. The naive approach (v1 baseline)

### How it works

`search()` performed a **linear scan** over every indexed class, scoring by
additive substring containment:

```text
score = 0
if keyword ∈ class_name  (case-insensitive)  → score += 3
if keyword ∈ label        (case-insensitive)  → score += 2
if keyword ∈ comment      (case-insensitive)  → score += 1
```

Classes with `score > 0` were sorted by descending score. On ties, the insertion
order of the source index was preserved.

### The weights

The 3/2/1 weights reflect decreasing confidence in each field:

- The **class name** is the exact technical identifier: if the keyword appears
  there, the match is near-certain.
- The **label** is the official human name given by Cisco: high semantic value.
- The **comment** is a few-sentence description: far more ambiguous, since many
  classes mention "tenant", "VRF" or "bridge domain" in passing.

### Baseline measurement

Recorded in the reference table of `mcp/tests/eval_search.py`, measured on a
39-query golden set against the APIC 6.0(9c) corpus:

| Metric | Score |
|---|---|
| Recall@1 | 15.4 % |
| Recall@5 | 35.9 % |
| MRR | 0.229 |
| Average query time | 3.2 ms |

### Failure analysis

The right class was usually *present* in the results — it was ranked badly. Two
structural causes, both still verifiable in the current corpus.

#### Shared labels

Cisco assigns the same human label to a primary class and to its policies,
relations and variants. Thirty classes carry the exact label `"Bridge Domain"`.
Under the v1 formula the query `"bridge domain"` scores several of them
identically:

| Class | Name | Label | Comment | v1 score |
|---|---|---|---|---|
| `fvBD` | — | `"Bridge Domain"` +2 | contains the phrase +1 | **3** |
| `fvABDPol` | — | `"Bridge Domain"` +2 | contains the phrase +1 | **3** |
| `fvSvcBD` | — | `"Bridge Domain"` +2 | contains the phrase +1 | **3** |
| `l2BD` | — | `"Bridge Domain"` +2 | — | 2 |
| `fvBDDef` | — | `"Bridge Domain"` +2 | — | 2 |

Three-way tie at the top, resolved by the arbitrary insertion order of the index.
`fvBD` could land at rank 1 or rank 3 for reasons having nothing to do with
relevance.

#### The Rs/Rt class problem

ACI relation classes follow a strict naming convention:

- `fvRsCtx`: **R**elation **s**ource — from `fvBD` to `fvCtx`
- `l3extRtVrfValidationPol`: **R**elation **t**arget — a back-reference

They systematically inherit the **label of the class they point at**, and their
camelCase name usually *contains* the target concept as well:

```text
fvRsCtx                 → label "Private Network"   (fvCtx's other name)
l3extRtVrfValidationPol → label "VRF"               (fvCtx's label)
plannerRsBdVrf          → label "VRF"               (fvCtx's label)
```

Result for the query `"VRF"` under the v1 formula:

| Class | Name | Label | Comment | v1 score |
|---|---|---|---|---|
| `l3extRtVrfValidationPol` | contains "vrf" +3 | `"VRF"` +2 | contains "vrf" +1 | **6** |
| `plannerRsBdVrf` | contains "vrf" +3 | `"VRF"` +2 | contains "vrf" +1 | **6** |
| `fvCtx` | `"fvctx"` — no match | `"VRF"` +2 | no match | **2** |

`fvCtx`, the actual VRF class, scores a third of what relation classes that
merely point at it score, because they carry the concept in their name and it
does not.

---

## 3. Axis 1 — the Rs/Rt penalty

### The diagnosis

Rs and Rt classes are **internal artifacts** of the object model. They do not
represent objects an operator creates, modifies or queries directly — they encode
relations between primary objects. An agent calling `query()` targets the primary
class (`fvBD`, `fvCtx`, `vzBrCP`…) and navigates relations afterwards.

The problem is structural, not statistical: relation classes *should not* head a
result list. That is a rule of the object model, not a scoring accident. They are
not rare either — 3 065 of the 15 239 indexed classes match the pattern.

### The detection pattern

A relation class carries `Rs` or `Rt` immediately after its package prefix:

```text
fvRsCtx                 → prefix "fv"    + Rs + "Ctx"
l3extRtVrfValidationPol → prefix "l3ext" + Rt + "VrfValidationPol"
infraRsVpcBndlGrp       → prefix "infra" + Rs + "VpcBndlGrp"
```

```python
_RS_RT_RE = re.compile(r"^[a-z][a-z0-9]*(?:Rs|Rt)[A-Z]")
```

- `^[a-z]` — class names always start lowercase (ACI convention)
- `[a-z0-9]*` — the prefix may contain digits (`l3`, `pol2`, `iso8583`)
- `(?:Rs|Rt)` — the relation marker, always capitalised
- `[A-Z]` — immediately followed by an uppercase letter, the start of the target
  name

### The penalty

v1 subtracted **3 points** after the text score:

```python
if score > 0:
    if _RS_RT_RE.match(cls):
        score -= 3
    if score > 0:
        results.append(...)
```

Applying it *after* the text score does two things: it preserves relative order
among relation classes themselves, and it drops any class whose score falls to
zero out of the results entirely — an irrelevant result has no value at position
10 either.

For the `"VRF"` example above, the two relation classes fall from 6 to 3. `fvCtx`
is still at 2, so the penalty narrowed a four-point gap to one point without
closing it. Closing it needed a signal that is not textual at all — see
[section 6](#6-v2--tokenization-structural-priors-and-a-curated-jargon-table).

### Recorded gains

| Metric | Baseline | + Axis 1 |
|---|---|---|
| Recall@1 | 15.4 % | **28.2 %** |
| Recall@5 | 35.9 % | **41.0 %** |
| MRR | 0.229 | **0.338** |
| Average time | 3.2 ms | 3.2 ms |

**What axis 1 did not solve.** The shared-label problem between primary classes:
`fvBD`, `fvABDPol` and `fvSvcBD` all label `"Bridge Domain"`, none of them a
relation class. Functional queries and synonyms remained at zero.

---

## 4. Axis 2 — property labels

### The diagnosis

Functional search — `"ARP flooding"`, `"dead interval"`, `"link aggregation"`,
`"data plane learning"` — failed completely, because those terms appear **neither
in the label nor in the comment** of the class that owns the behaviour.

The information does exist. Every property of every class carries its own human
label, written by Cisco. `fvBD`'s `arpFlood` property is labelled `"ARP
Flooding"`; `ospfIfPol`'s `deadIntvl` is labelled `"Dead Interval"`. These are the
official terms for what an object can *do* — exactly what a functional query is
asking about — and they were simply absent from the search index.

### The two-component solution

#### Component A — carry property labels into the index

Each index entry gained an optional `prop_labels` list: the human labels of the
class's properties, minus everything that carries no search signal. Four filters
do that work (hidden properties, eight generic cross-class labels such as
`"Name"` and `"Description"`, labels of three characters or fewer, and labels
that merely restate the property name); they are documented in
[registry.md](registry.md#descriptions_index).

```json
{
  "ospfIfPol": {
    "label": "OSPF Interface Policy",
    "comment": "The OSPF interface-level policy information.",
    "prop_labels": [
      "Interface Controls", "Dead Interval", "Hello Interval",
      "Network Type", "Prefix Suppression", "Retransmit Interval",
      "Transmit Delay"
    ],
    "isConfigurable": true
  }
}
```

*Abridged: `ospfIfPol` carries eight property labels.*

**Index impact, measured on the current corpus:** 12 856 of the 15 239 indexed
classes have at least one usable property label, and **549 classes are in the
index for that reason alone** — they have neither a label nor a comment.

#### Component B — consult property labels as a fallback

The v1 change to `search()` was deliberately minimal. The property-label scan ran
only for a class that had scored nothing on the three standard fields:

```python
if score == 0:
    for pl in meta.get("prop_labels", ()):
        if kw in pl.lower():
            score = 1
            break   # one match is enough — no accumulation
```

Three design decisions, all of which survive in spirit into v2:

1. **Fallback only.** A class already matching on its name or label does not also
   collect property points, which would favour classes with many properties.
2. **Fixed +1, no accumulation.** The `break` matters: `fvBD` has 23 property
   labels and would otherwise dominate a more targeted class.
3. **Weight +1, level with the comment.** A property label is contextual, not a
   definition.

### Recorded gains

| Metric | After axis 1 | + Axis 2 |
|---|---|---|
| Recall@1 | 28.2 % | **30.8 %** |
| Recall@5 | 41.0 % | **53.8 %** |
| MRR | 0.338 | **0.400** |
| Average time | 3.2 ms | 11.4 ms |

Per tier, after both axes: tier 1 R@1 35 % / R@5 55 %; tier 2 R@1 80 % / R@5
100 %; tier 3 R@1 9 % / R@5 45 %; tier 4 zero on both.

### Interpreting the numbers

Recall@5 jumped while Recall@1 barely moved, because every class found through a
property label received score 1 — below any class matching on its own name,
label or comment. The expected class typically landed at rank 2–5, beaten by
classes whose text mentioned the words incidentally. Ties among property-label
hits were then resolved by insertion order.

The latency cost came from the same place: for a query that matches nothing on
the three main fields, the `for pl in prop_labels` loop ran for essentially every
class in the index. v2 removes that inner loop entirely (see
[section 6](#performance-keeping-tokenized-matching-inside-the-latency-budget)).

---

## 5. Summary of the three v1 states

| Strategy | R@1 | R@5 | MRR | Avg ms | Indexed classes |
|---|---|---|---|---|---|
| Baseline — naive substring | 15.4 % | 35.9 % | 0.229 | 3.2 | 14 603 |
| + Axis 1 — Rs/Rt penalty | 28.2 % | 41.0 % | 0.338 | 3.2 | 14 603 |
| + Axis 2 — property labels | 30.8 % | 53.8 % | 0.400 | 11.4 | 15 152 |

*Metrics recorded on a 39-query golden set, APIC 6.0(9c) — the reference table in
`mcp/tests/eval_search.py`. The two index sizes are verifiable today: 14 603
classes carry a label or a comment, and 15 152 carry at least one textual field
once property labels are included. Neither is the size of the corpus, which is
15 452, nor of the index, which is 15 239 — the remaining 87 entries hold
structural flags and no text.*

Everything above this line is v1, kept as a record. Its own diagnosis of what
remained unresolved is what motivated the v2 rewrite:

- **The shared-label problem.** Thirty classes are labelled `"Bridge Domain"`.
  The Rs/Rt penalty separates the 25 relation classes among them, but `fvBD`,
  `fvABDPol`, `fvSvcBD`, `l2BD` and `fvBDDef` are left tied on text alone.
- **Pure synonyms.** `"gateway"` → `fvSubnet`, `"security policy"` → `vzBrCP`:
  no textual anchor anywhere in the corpus.
- **The property-label fallback cost.** A linear scan over every class's labels,
  repeated across the whole index on every miss.

---

## 6. v2 — tokenization, structural priors, and a curated jargon table

### Why v1's substring approach hit a ceiling

v1 scored whole-string containment (`keyword in class_name`, `keyword in label`,
`keyword in comment`). Two problems followed from that choice alone, independent
of any weighting:

1. **Multi-word queries can never match a camelCase name.** `"fabric node"` is not
   a substring of `"fabricNode"` — no amount of weight tuning fixes that.
2. **A shared-label tie is unresolvable by text.** When several classes carry the
   identical label and comparable comments, any purely textual scheme scores them
   identically — and something has to break the tie.

v2 replaces substring matching with **tokenized matching**, and adds
**structural priors** that use what a class *is* — configurable, abstract,
telemetry, relation plumbing — rather than only what it is called.

### Tokenization

`_tokenize()` splits camelCase / PascalCase / ACRONYM identifiers into lowercase
tokens with one regex, applied identically to class names, labels, comments,
property labels *and* the query:

```text
"fvBD"                    → ["fv", "bd"]
"l3extRtVrfValidationPol" → ["l3ext", "rt", "vrf", "validation", "pol"]
"IPv6Multicast"           → ["i", "pv6", "multicast"]
"fabricNode"              → ["fabric", "node"]
"fabric node"             → ["fabric", "node"]
```

```python
_TOKEN_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z0-9]+|[A-Z]+")
```

It splits at a lowercase→uppercase transition or an acronym-run boundary and
nowhere else — `"l3ext"` has neither internally, so it stays one token rather
than becoming `"l3"` + `"ext"`. Because both sides of every comparison go through
the same function, `"fabric node"` and `"fabricNode"` produce the identical token
set: problem 1 is gone by construction, not by a special case.

### Scoring, ranked by confidence

| Signal | Weight | Rationale |
|---|---|---|
| Exact match against the label, or against a curated jargon phrase | +20 / +18 | If the user's exact words *are* the official name, that is the answer |
| Exact match against the class name (query squashed, separators stripped) | +25 | The `"fvbd"` → `fvBD` case — the strongest possible signal |
| Query phrase found inside the label or jargon phrase | +6 | e.g. `"routing instance"` inside the jargon `"vrf context routing instance"` |
| Token coverage of the label / class name (squared) | up to +8 / +5 | Rewards a query naming *most or all* of a concept over one sharing an incidental word; squaring makes partial coverage fall off fast |
| Query phrase found inside the joined property-label haystack | +6 | The functional case: `"ARP flooding"` inside `fvBD`'s own property label |
| Query phrase found inside the comment | +2 | Same idea, one tier weaker — the comment is prose, not curated terminology |
| Token coverage of property labels (squared), when no phrase hit | up to +2 | Fallback when the phrase is not a literal substring |
| Token coverage of the comment (squared), when no phrase hit | up to +1 | Weakest signal — the least targeted field |
| Curated synonym hit | up to +3 × coverage | A nudge, not an override; cannot beat a genuine exact match on its own |

### Structural priors

Applied **after** the text score, and only when it is already positive:

| Prior | Adjustment | Source |
|---|---|---|
| `isConfigurable` | +6 | `mo.is_configurable`, carried into the index — 3 010 classes |
| `isAbstract` | −6 | `mo.is_abstract` — 1 954 classes |
| Stats/telemetry suffix (`5min`, `15min`, `1h`, `1d`, `1w`, `1mo`, `1qtr`, `1year`) | −10 | Regex on the class name — 4 769 classes that structurally cannot be a config target |
| Rs/Rt relation class | −8 | The v1 detection regex, now a fixed −8 rather than −3 — 3 065 classes |

**Why this solves what v1 could not.** The `"VRF"` case separates on an axis that
has nothing to do with text: `fvCtx` is configurable (+6) while
`l3extRtVrfValidationPol` is not and is an Rt class (−8). The query now returns
`fvCtx` first, ahead of the relation classes that used to outrank it. The same
mechanism thins the `"Bridge Domain"` family: of the 30 classes carrying that
exact label, 25 are relation classes and only 7 are configurable — so the priors
alone spread a block that was textually indistinguishable across a 14-point
range.

### Tie-breaking

Equal scores are broken, in order: fewer class-name tokens → shorter class name →
alphabetical. This is a deterministic replacement for v1's "insertion order
decides": a concise canonical name (`fvBD`, two tokens) reliably beats a more
specific variant that happens to score identically (`fvABDPol`, three tokens),
and identical input always produces identical output regardless of dict iteration
order.

### A small, honestly-scoped jargon and synonym table

Two curated dicts in `registry/descriptions.py` close gaps that no text-matching
machinery can close, because the terms appear nowhere in the schema:

- **`_JARGON`** (29 entries, `class_name → canonical phrase`) — for classes whose
  real APIC label does not say what an operator would ask for. `bgpPeerP`'s label
  is `"Peer Connectivity Profile"`, which contains neither "BGP" nor "policy", so
  `_JARGON["bgpPeerP"] = "bgp peer policy"` gives it a chance against classes that
  do contain those words literally.
- **`_SYNONYMS`** (11 entries, `informal token → target tokens`) — single-word
  mappings such as `"vpc"`/`"pc"` → `"bndl"`, `"gateway"` → `"subnet"`,
  `"outside"` → `"out"`/`"ext"`, contributing a bounded boost: enough to break a
  tie, never enough to override an exact match.

Both are deliberately small. The module docstring is explicit that entries exist
to close *specific, observed* gaps from the golden set and that every addition
should be justified by a measured delta rather than intuition.

### Performance: keeping tokenized matching inside the latency budget

Tokenizing and scoring every field of ~15 k classes on every call is inherently
more expensive than a raw substring scan. Two design choices keep it inside the
budget:

1. **Build once, cache by object identity.** `_get_index()` tokenises every class
   exactly once and caches the result keyed on the *identity* of the
   `descriptions` dict. In production that dict is built once in the lifespan and
   reused for the process lifetime, so the build is paid once. Comparing with
   `is` against a held reference — rather than caching under `id()` — avoids the
   classic hazard of a garbage-collected object's id being reused by an unrelated
   one, while a genuinely different dict (a fresh one built by a test) still
   correctly misses and rebuilds.
2. **One substring check instead of one per property label.** Each class's
   property labels are pre-lowercased and joined into a single haystack string at
   index-build time, separated by `\x1f` — a control character chosen so a query
   phrase can never match by spanning the boundary between two unrelated labels.
   The per-call cost becomes one `in` test instead of a loop over every label,
   which is what v1's fallback did on every miss.

Measured on an Apple-silicon workstation over the catalogue-rebuilt index:
~430 ms to build it once, ~14 ms per search at `limit=5`, 19.9 ms average across
the 74 golden queries at `limit=10`.

### Measured results

| Metric | v1 (39 queries) | v2 (39 queries) | v2 (74 queries — current) |
|---|---|---|---|
| Recall@1 | 30.8 % | 69.2 % | **78.4 %** |
| Recall@5 | 53.8 % | 89.7 % | **94.6 %** |
| MRR | 0.400 | 0.793 | **0.846** |

*The two 39-query columns are the values recorded in `registry/descriptions.py`'s
module docstring at the time of the rewrite; the 74-query column is measured on
the current corpus and asserted as an equality in CI.*

Per tier on the current 74-query set:

| Tier | n | R@1 | R@5 |
|---|---|---|---|
| 1 — direct label or name | 44 | 90.9 % | 100 % |
| 2 — camelCase name | 10 | 100 % | 100 % |
| 3 — functional property | 15 | 53.3 % | 80.0 % |
| 4 — pure synonym | 5 | 0 % | 80.0 % |

**On the MRR figure.** 0.846 is the value computed over the top **5** results —
the cut-off `mcp/tests/baseline/` and `mcp/tests/unit/test_search_source.py` use,
and the one asserted as an exact equality (0.8461711711711711). Running
`tests/eval_search.py` with its default ten-result window reports **0.854** for
the same run: a class found at rank 6–10 contributes there and not in the
top-5 computation. Both are correct for their own definition; quote the cut-off
alongside the number.

The golden set grew from 39 to 74 queries *alongside* the v2 rewrite. The added
queries were chosen for breadth — more classes, more phrasing styles across all
four tiers — before the numbers above were measured, not curated afterwards.
Recall@1 and Recall@5 both improved on the larger set relative to the original
39, which is the signal you want if a change generalises rather than overfits.

### What the ranking looks like now

Current top-5 for one query per tier, over the catalogue-rebuilt index:

```text
"bridge domain"  → fvBD, fvSvcBD, fvABDPol, l2BD, fvBDDef
"fvbd"           → fvBD, fvAToBD
"ARP flooding"   → fvBD, arpIf, arpInst, arpStAdjEp, arpIfPol
"gateway"        → gleanGateway, fvSubnet, dhcpRelayGwExtIp, dhcpGwDef, dhcpRelayGw
```

The first three land the expected class at rank 1 — including the functional
query, which reaches `fvBD` only through its `arpFlood` property label. The
fourth is the tier-4 limit described below.

---

## 7. Remaining limitations and future directions

### What v2 resolved

- **The shared-label tie** — solved by structural priors, not more text matching.
- **Multi-word queries against camelCase names** — solved by tokenizing both
  sides identically.
- **Non-deterministic tie ordering** — solved by an explicit tie-break rule.

### What remains unresolved

#### Pure synonyms without a curated entry

Tier 4 Recall@1 is still 0 %. The curated tables lift Recall@5 to 80 %, so the
target class does usually appear in the visible result list, but rank-1
placement for a pure synonym competing against a class with a genuine textual
match is a structural limit of curated-table plus text scoring. `"gateway"`
returns `gleanGateway` first — a class whose literal label *is* `"Gateway"` —
with `fvSubnet` second. The curated boost is deliberately capped so it cannot
override an exact match, which is the right general-purpose default even where it
loses this specific case. Closing the gap fully would need a semantic embedding
model or a much larger, actively maintained synonym dictionary: a real
cost/benefit tradeoff, not a quick fix.

#### The curated tables need upkeep

`_JARGON` and `_SYNONYMS` are hand-maintained and will drift as new failures
appear in production use. The module docstring's rule — justify every entry with
a measured delta, never pad speculatively — is the only guardrail against them
becoming an unmaintainable pile of special cases.

### Guide for future evolutions

Any scoring change must be:

1. **Measured on the golden set.** From `mcp/`:

   ```bash
   uv run python tests/eval_search.py --verbose
   ```

   The `--verbose` flag lists every miss and near-miss with the top-3 it returned
   instead.

2. **Gated in CI.** Three layers, deliberately different in strictness:

   | Gate | Asserts |
   |---|---|
   | `tests/unit/test_search_source.py` | **Equality** — R@1 = 0.783784, R@5 = 0.945946, MRR = 0.846171 over the catalogue index, plus a 3 s ceiling on the index build and the "built once" invariant |
   | `tests/eval/test_search_quality.py` | **Floors** — R@1 ≥ 60 %, R@5 ≥ 85 %, golden set ≥ 50 queries |
   | `tests/perf/test_search_perf.py` | **Budgets** — a single search < 200 ms and 100 consecutive searches < 2 s over a synthetic 15 k-entry index |

   A deliberate change to the scorer will break the equality assertions first;
   that is the point. Update the recorded constants in the same commit as the
   change, and say why.

3. **Documented** in the table in `registry/descriptions.py`'s module docstring
   and in this file.

4. **Validated on edge cases**: the Rs/Rt and stats-suffix penalties, priors on
   classes that carry neither flag, and curated entries that measurably help
   rather than plausibly should.

The golden set covers 74 queries across 4 tiers over 15 239 indexed classes.
Growing it — especially tier 3 and 4 cases discovered from real agent usage
rather than invented in the abstract — remains the highest-leverage next step
before introducing more aggressive heuristics.

One caveat on the second gate: it still loads the JSON index file that 2.0
removed, so it skips on a clean checkout. The equality gate and the perf budgets
run unconditionally — the first over the catalogue index, the second over a
synthetic one — which is why the exact recorded values live in
`tests/unit/test_search_source.py` rather than in the floors.
