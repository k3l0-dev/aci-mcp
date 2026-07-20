# Tool: search_classes

Discover ACI class names by keyword. **Always call this first** — never assume a class name.

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
    "comment": "A bridge domain is a unique layer 2 forwarding domain that contains one or more subnets."
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

For the full mechanics, worked examples, and measured gains (current:
Recall@1 78.4% / Recall@5 94.6% on a 74-query golden set — up from a 15.4%
Recall@1 naive baseline), see [internals/search-algorithm.md](../internals/search-algorithm.md).

---

## Examples

```python
# Find the Bridge Domain class
search_classes("bridge domain")
# → [{"class_name": "fvBD", "label": "Bridge Domain", ...}]

# Find all tenant-related classes
search_classes("tenant", limit=20)

# Partial class name (you remember part of it)
search_classes("fvAEP")

# Operational data
search_classes("fault")
search_classes("audit log")

# Network topology
search_classes("node")
search_classes("path endpoint")

# Functional / property-level query — matches via the class's own property labels
search_classes("ARP flooding")   # → fvBD (arpFlood property)
search_classes("dead interval")  # → ospfIfPol (deadIntvl property)
```

---

## Common searches

| You want | Use keyword |
|---|---|
| Bridge domains | `bridge domain` |
| Tenants | `tenant` |
| EPGs | `endpoint group` |
| Contracts | `contract` |
| VRFs | `vrf` or `layer 3` |
| Faults | `fault` |
| Fabric nodes | `node` or `fabric node` |
| Interface policies | `interface policy` |
| Physical ports | `path endpoint` |
| Subnets | `subnet` |

---

## Edge cases

- **Empty or whitespace-only keyword** → returns `[]` immediately (no scan)
- **No match** → returns `[]`
- **`limit <= 0`** → clamped to `1` rather than passed through — a search always returns at least one result if any class matches at all
- **`limit > 50`** → silently capped at 50
- Search is **case-insensitive** — `"BRIDGE"` and `"bridge"` produce the same results
- Results are deterministic — identical input always produces identical output, regardless of registry load order
