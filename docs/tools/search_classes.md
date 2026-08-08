# Tool: search_classes

Discover ACI class names by keyword. **Always call this first** — never assume a class name.

Search runs entirely in memory. The index is built once at server startup from
the catalogue embedded in the `niwaki` package; no APIC round trip and no file
read happen per call.

---

## Signature

```python
search_classes(keyword: str, limit: int = 10) -> list[dict[str, str]]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `keyword` | `str` | — | Plain English term or partial ACI class name |
| `limit` | `int` | `10` | Maximum results to return. **Clamped to [1, 50].** |

---

## Return value

List of dicts, sorted by relevance score (descending):

```json
[
  {
    "class_name": "fvBD",
    "label": "Bridge Domain",
    "comment": "A bridge domain is a unique layer 2 forwarding domain that contains one or more subnets. Each bridge domain must be linked to a context."
  }
]
```

| Field | Description |
|---|---|
| `class_name` | Exact ACI class name — use this in `get_schema()` and `query()` |
| `label` | Short human-readable label from the APIC schema |
| `comment` | One-sentence description from the APIC schema |

An empty list means no class matched the keyword. Refine or broaden the search term.

---

## What the index covers

The index holds **15,239** classes; the catalogue holds **15,452**. The 213
classes that are missing carry no label, no comment, and no discriminating
property label, so there is nothing to index them by.

Those 213 remain fully usable: `get_schema()` describes them and `query()` /
`count()` accept them. Only keyword discovery cannot reach them. If a class name
comes from a `contains` list, a DN, or a design document, pass it straight to
`get_schema()` rather than treating a search miss as proof it does not exist.

---

## Scoring (v2)

The query is tokenized the same way as class names (camelCase-aware), so a
multi-word query like `"fabric node"` matches `fabricNode` even though it's
never a literal substring of it. Each class is scored on several signals,
strongest first:

| Signal | Contribution |
|---|---|
| Query is an exact match for the class's label (or a curated ACI jargon phrase) | Dominant — this almost certainly is the answer |
| Query squashed (no spaces/dashes) exactly equals the class name | Dominant — the `"fvbd"` → `fvBD` case |
| Query phrase found inside the label, jargon phrase, or a property label | Strong |
| Token coverage of the label / class name / property labels / comment | Proportional to how much of the query is covered — the more of the query a field explains, the higher this scores |
| A small curated synonym table (e.g. `"vpc"`↔`bndl`, `"gateway"`↔`subnet`) | Modest boost, never enough to beat a genuine exact match |

After the text score, **structural priors** adjust the ranking based on what
the object actually *is*, not just what it's called:

| Prior | Effect | Why |
|---|---|---|
| `isConfigurable` | Boost | You almost always want the object you can actually create/edit, not a same-labeled stats/telemetry class |
| `isAbstract` | Penalty | Can never be the class you query directly |
| Stats/telemetry suffix (`5min`, `1h`, `1d`, …) | Penalty | Structurally a counter bucket, never the config object |
| Rs/Rt relation class name (`fvRsCtx`, `l3extRtVrfValidationPol`, …) | Penalty | Internal plumbing — never the primary target of a query |

Ties are broken deterministically: fewer class-name tokens, then a shorter
class name, then alphabetical — so `fvBD` reliably beats a more specific
variant like `fvABDPol` when both would otherwise tie.

Measured on the 74-query golden set: **Recall@1 78.4%, Recall@5 94.6%,
MRR 0.846**. The naive substring baseline the algorithm replaced scored
Recall@1 15.4% on the earlier 39-query set. The scorer, its synonym table, and
its structural priors are unchanged in 2.0 — only the source the index is built
from moved, and the metrics are asserted as equalities rather than floors so any
movement reads as a rebuild bug rather than a scoring trade-off.

For the full mechanics, worked examples, and the axis-by-axis history, see
[internals/search-algorithm.md](../internals/search-algorithm.md).

---

## Examples

```python
# Find the Bridge Domain class
search_classes("bridge domain")
# → [{"class_name": "fvBD", "label": "Bridge Domain", ...}]

# Find all tenant-related classes
search_classes("tenant", limit=20)

# The class name itself — spacing, dashes and case are squashed before matching
search_classes("fvbd")            # → fvBD

# Operational data
search_classes("fault instance")  # → faultInst
search_classes("fault record")    # → faultRecord
search_classes("audit log")       # → aaaModLR

# Network topology
search_classes("fabric node")     # → fabricNode

# Functional / property-level query — matches via the class's own property labels
search_classes("ARP flooding")    # → fvBD (arpFlood property)
search_classes("dead interval")   # → ospfIfPol (deadIntvl property)
```

### Name the object, not the family

ACI names thousands of classes around the same few words, so a bare noun tends
to land on the family rather than on the class you want. Adding the second word
of the object's real name is what separates them:

| Too broad | Lands on | Precise | Lands on |
|---|---|---|---|
| `fault` | `faultCounts` and other counters | `fault instance` | `faultInst` |
| `node` | `tracerouteNode`, `dhcpClientNode` | `fabric node` | `fabricNode` |
| `endpoint group` | `igmpsnoopEpgRec` | `application endpoint group` | `fvAEPg` |
| `interface policy` | `bfdRsIfPol` and its siblings | `ospf interface policy` | `ospfIfPol` |
| `layer 3` | `l3extOut` | `vrf` | `fvCtx` |

The broad query is not wrong — the class you want is usually still in the top 10
— but read the returned `label` before committing to a name rather than taking
the first result on faith.

---

## Common searches

Each of these returns the listed class as the **first** result:

| You want | Use keyword | First result |
|---|---|---|
| Bridge domains | `bridge domain` | `fvBD` |
| Tenants | `tenant` | `fvTenant` |
| EPGs | `application endpoint group` | `fvAEPg` |
| Contracts | `contract` | `vzBrCP` |
| Contract subjects | `contract subject` | `vzSubj` |
| VRFs | `vrf` | `fvCtx` |
| Subnets | `subnet` | `fvSubnet` |
| Faults | `fault instance` | `faultInst` |
| Audit records | `audit log` | `aaaModLR` |
| Fabric nodes | `fabric node` | `fabricNode` |
| Physical domains | `physical domain` | `physDomP` |
| Access port policy groups | `access port policy group` | `infraAccPortGrp` |
| Endpoints | `client endpoint` | `fvCEp` |

---

## Edge cases

- **Empty or whitespace-only keyword** → returns `[]` immediately (no scan)
- **No match** → returns `[]`
- **`limit <= 0`** → clamped to `1` rather than passed through — a search always returns at least one result if any class matches at all
- **`limit > 50`** → silently capped at 50
- Search is **case-insensitive** — `"BRIDGE"` and `"bridge"` produce the same results. Note that this applies to *searching* only: `get_schema`, `query`, and `count` match class names exactly, so copy `class_name` from the result rather than retyping it.
- Results are deterministic — identical input always produces identical output, regardless of index build order
