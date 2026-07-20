---
name: aci-mcp-query
description: Query the ACI APIC controller via MCP tools. Use when the user asks about ACI infrastructure — faults, tenants, bridge domains, EPGs, VRFs, contracts, fabric nodes, routing, endpoints, audit logs.
---

You have five MCP tools:

- `search_classes` — find a class name from a keyword
- `get_schema` — inspect a class: identifiers, containment, children, per-property constraints
- `query` — fetch objects of a class (filtered, scoped)
- `get_by_dn` — fetch one object directly by its DN (shortcut, skips discovery)
- `count` — count objects of a class without transferring them

This skill explains the ACI object model, the data structures the tools
return, how to read a schema, and how to navigate the object tree.

**Discovery vs. shortcut.** The `search_classes → get_schema → query` sequence
below is mandatory only when you do *not* already hold a verified class name and
DN. When you already have an exact DN (from a previous result or a design), call
`get_by_dn(dn)` directly — no search/schema detour. See section 5.5.

---

## 1. The ACI Object Model (MO)

Every entity in ACI is a **Managed Object (MO)**: a typed node in a tree.

```
polUni                          ← root
  fvTenant                      ← tenant
    fvCtx                       ← VRF
    fvBD                        ← bridge domain
      fvSubnet                  ← subnet
    fvAp                        ← application profile
      fvAEPg                    ← EPG
        fvRsCons                ← relation: consumes contract  ← Rs object
  fabricTopology
    fabricPod
      fabricNode                ← spine / leaf / controller
  faultSummary
    faultInst                   ← individual fault
```

Every MO has:
- A **class name** (camelCase, package prefix: `fv`, `fabric`, `fault`…)
- A **Distinguished Name (DN)** — the full path from root, unique across the fabric
- A flat bag of **attributes** (strings, even booleans and integers)

---

## 2. Canonical object shape

The `query` tool returns an **envelope**, not a bare list — the objects live
under `results`, alongside the true size of the matching set:

```json
{
  "results": [
    {
      "_class": "<ClassName>",
      "dn": "<full/path/to/object>",
      "name": "<identifier>",
      "<attr>": "<value>",
      ...
    }
  ],
  "returned": 1,
  "total_available": 1,
  "truncated": false,
  "next_page": null,
  "complete": true,
  "note": null
}
```

| Field | Meaning |
|---|---|
| `results` | The objects, same per-item shape as before (`_class`, `dn`, attributes, optional `_children`) |
| `returned` | `len(results)` — how many objects came back in this call |
| `total_available` | The APIC-reported true match count — fabric-wide or subtree-wide, regardless of how many were fetched |
| `truncated` | `total_available > returned` — this call did NOT return everything that matches |
| `next_page` | `page + 1` when `truncated` and `fetch_all` was not used; `null` otherwise |
| `complete` | `false` only when `fetch_all=True` hit the safety cap before exhausting all matches |
| `note` | Guidance string when `truncated` or capped; `null` otherwise |

**`truncated: true` is a hard stop for any max/min/total/all-of conclusion** —
see section 5's Pagination subsection and the FULL-FABRIC AGGREGATION rule in
the server instructions.

All attribute values in `results` items are **strings** (including numbers
and booleans).

When `include_children` is set, each object in `results` also contains
`_children` — a flat list of child attribute dicts, each with their own
`_class` key:

```json
{
  "results": [
    {
      "_class": "fvBD",
      "dn": "uni/tn-OT/BD-servers",
      "unicastRoute": "yes",
      "_children": [
        {"_class": "fvSubnet", "dn": "uni/tn-OT/BD-servers/subnet-[10.0.0.1/24]", "ip": "10.0.0.1/24"},
        {"_class": "fvRsCtx",  "dn": "uni/tn-OT/BD-servers/rsctx", "tnFvCtxName": "ot.main.vrf"}
      ]
    }
  ],
  "returned": 1,
  "total_available": 1,
  "truncated": false,
  "next_page": null,
  "complete": true,
  "note": null
}
```

---

## 3. The Distinguished Name (DN)

The DN encodes the full containment path. Each `/`-separated component is a
**Relative Name (RN)** built from the class's `rnFormat` template:

```
uni / tn-{name} / BD-{name} / subnet-[{ip}]
```

Rules:
- `scope_dn` is always an exact DN prefix — get it from a prior `query` result, never construct it from memory
- Every `dn` in a result is a valid `scope_dn` for child queries
- The parent DN is `dn` up to (not including) the last `/` component

---

## 4. Schema anatomy

`get_schema(class_name)` returns the APIC jsonmeta schema, simplified.
Here is what each field means and how to use it:

### `identifiedBy` — primary key within the parent scope

```json
"identifiedBy": ["name"]
"identifiedBy": ["ip"]
"identifiedBy": ["mac"]
```

These are the attributes that uniquely identify an instance under its parent.
Use them as `filters` keys in `query` when you want a specific object.
A filter on a non-identifying attribute is valid but may match multiple objects.

### `rnFormat` — template of the object's Relative Name

```json
"rnFormat": "ctx-{name}"
"rnFormat": "subnet-[{ip}]"
"rnFormat": "node-{id}"
```

Tells you the DN component shape. If you already know the parent DN and the
identifying attribute value, you can derive `scope_dn` without a prior query:
`parent_dn + "/" + render(rnFormat, attributes)`.

### `dnFormats` — the complete DN template (quote it verbatim)

```json
"dnFormats": ["uni/tn-{name}/BD-{name}"]
```

The full path template, built by chaining every ancestor's `rnFormat`. Each
`{...}` placeholder is already named after the *schema's own* identifying
attribute — not a human-friendly label. Two placeholders legitimately read
the same name when two ancestors share an identifying attribute (a tenant
and a bridge domain are both identified by `name`, hence `{name}` appearing
twice above) — that repetition is expected, not a copy error, and must not
be "cleaned up" by renaming it to something more descriptive when you quote
it. When stating a DN pattern in an answer, quote `dnFormats` (or `rnFormat`
for just the last component) exactly, substituting only the literal values
— never reconstruct or paraphrase a DN template from memory.

### `containedBy` — the parent class(es) in `pkg:Class` notation

```json
"containedBy": ["fv:Tenant"]
"containedBy": ["fabric:Pod"]
"containedBy": ["pol:Uni", "infra:Infra"]
```

Convert `pkg:Class` → `pkgClass` (remove the colon) to get the queryable
class name: `fv:Tenant` → `fvTenant`.
To query objects of this class under a specific parent, first query the parent
to get its `dn`, then pass it as `scope_dn`.

### `contains` — the child classes this object may hold

```json
"contains": ["fvSubnet", "fvRsCtx", "fvRsBDToOut", "tagTag", "faultInst"]
```

Already in flat notation — feed a name straight to `get_schema`, `query`, or
`include_children`. This is how you discover what lives *under* an object:
answering "what can a bridge domain contain?" no longer requires guessing.
Combine with `include_children` to pull the relevant children inline.

`contains` only names the child *classes* — it says nothing about which of
them are relations or what each one targets. Do not describe a class listed
here as "the relation to X" or state its relation target: that information
only comes from `relationTo` (section below), and only for the specific
Rs classes it lists. If a class in `contains` also happens to be an Rs
class, its target is unknown until you look it up in `relationTo`.

### `property_details` — per-property constraints (opt-in)

`properties` gives only names. To learn a property's **type, allowed values,
default, and whether it is writable** *before* you set or filter on it, ask for
details — but only for the properties you care about, to stay token-efficient:

```python
get_schema("fvSubnet", properties_filter=["scope", "preferred"])
```

```json
"property_details": {
  "scope": {
    "type": "fv:RouteScp",
    "access": "read-write",
    "default": "private",
    "options": ["private", "public", "shared"],
    "comment": "The network visibility of the subnet."
  },
  "preferred": {
    "type": "scalar:Bool",
    "access": "read-write",
    "default": "false",
    "options": ["no", "yes"]
  }
}
```

Per-property fields (only `type` and `access` are always present):

| Field | Meaning |
|---|---|
| `type` | ACI model type, e.g. `scalar:Bool`, `fv:RouteScp` |
| `access` | `read-write` · `create-only` (immutable after create) · `read-only` (never settable) |
| `naming` | present when the property is part of the DN (an identifier) |
| `mandatory` | present when the property is required on create |
| `default` | the default value, when declared |
| `options` | allowed values — the **exact strings** the APIC accepts in `filters` and config |
| `comment` | one-line description |

`options` removes the guesswork behind section 8: never guess an enum's casing —
read it here. Use `include_property_details=True` to dump every property only
when you genuinely need the full picture.

If you have not called `get_schema` with `property_details` (via
`properties_filter` or `include_property_details`) for a given property, you
do not know its type, default, or allowed values — do not state them, even
ones that sound familiar from common ACI deployments. `properties` alone
(the plain name list) tells you a property *exists* and is filterable;
it tells you nothing about what it means or what it currently allows.

### `properties` — all queryable attribute names

```json
"properties": ["adminSt", "addr", "descr", "dn", "id", "name", ...]
```

Only attributes in this list are valid `filters` and `filter_expr` keys.
A filter on an attribute not in `properties` returns `[]` silently.

### `relationTo` — outgoing Rs relations (this object → another)

```json
"relationTo": {
  "fvRsCtx":  {"targetClass": "fv:Ctx",   "cardinality": ""},
  "fvRsCons": {"targetClass": "vz:BrCP",  "cardinality": ""},
  "fvRsProv": {"targetClass": "vz:BrCP",  "cardinality": ""}
}
```

Each key is a **Relation Source (Rs)** class — an intermediate object that
lives under this MO and holds the reference to the target.
See section 5 for how to traverse it.

### `relationFrom` — incoming Rt relations (another object → this one)

```json
"relationFrom": {
  "fvRtCtx": {"sourceClass": "fv:BD"}
}
```

Reverse lookups: which objects of `sourceClass` point to this one.
Traverse the same way as `relationTo` but query the `sourceClass` scoped to
the source object's DN.

### `isAbstract`

If `true`, the class cannot be directly instantiated — `query` will always
return `[]`. Use `search_classes` to find the concrete subclass instead.

---

## 5. Query parameters reference

### Simple equality filters — `filters`

```python
query("fvBD", filters={"unicastRoute": "yes", "arpFlood": "no"})
# → eq(fvBD.unicastRoute,"yes") AND eq(fvBD.arpFlood,"no")
```

Only use attribute names from `get_schema().properties`.

### Complex filter expression — `filter_expr`

Raw APIC filter string for operators beyond equality.
Combined with `filters` via `and()` if both are provided.

| Operator | Example |
|---|---|
| `eq` | `eq(fvBD.unicastRoute,"yes")` |
| `ne` | `ne(fabricNode.role,"controller")` |
| `wcard` | `wcard(fvBD.dn,"uni/tn-OT")` — substring match on DN |
| `gt` / `lt` | `gt(faultInst.severity,"minor")` |
| `and` | `and(ne(fabricNode.role,"controller"),eq(fabricNode.fabricSt,"active"))` |
| `or` | `or(eq(fvBD.arpFlood,"yes"),eq(fvBD.unicastRoute,"no"))` |

```python
# All active non-controller nodes
query("fabricNode",
      filter_expr='and(ne(fabricNode.role,"controller"),eq(fabricNode.fabricSt,"active"))')

# All BDs in tenant OT by DN wildcard
query("fvBD", filter_expr='wcard(fvBD.dn,"uni/tn-OT")')
```

### Embed direct children — `include_children`

Fetches parent objects with specified child classes embedded in `_children`.
Equivalent to `moquery -x rsp-subtree=children -x rsp-subtree-class=X,Y`.
Use to avoid N+1 query patterns.

```python
# BDs with their subnets and VRF in one call
query("fvBD", filters={"unicastRoute": "yes"},
      include_children=["fvSubnet", "fvRsCtx"])
```

### Health, faults, and stats inline — `rsp_subtree_include`

Includes APIC-computed subtrees alongside each returned object.
Only meaningful for live APIC.

| Value | Returns |
|---|---|
| `"faults"` | Active faults on each returned object |
| `"health"` | Health score (healthInst) for each object |
| `"audit-logs"` | Config change history |
| `"relations"` | All Rs/Rt relation objects |
| `"faults,no-scoped"` | Faults only, no top-level object attributes |
| `"faults,required"` | Only objects that have active faults |

```python
# BDs with their active faults
query("fvBD", scope_dn="uni/tn-OT", rsp_subtree_include="faults,required")

# All tenants with current health score
query("fvTenant", rsp_subtree_include="health")
```

### Time range for log records — `time_range`

Restricts log record queries by time window.
Valid for: `faultRecord`, `aaaModLR`, `eventRecord`, `healthRecord`.

```python
# Audit log last 24 hours
query("aaaModLR", time_range="24h", order_by="aaaModLR.created|desc")

# Fault records last week
query("faultRecord", time_range="1week")

# Custom date range
query("aaaModLR", time_range="2026-01-01|2026-01-31")
```

### Pagination — `page` and `fetch_all`

`limit` is the page size. Pages are 0-based.

```python
# First 20 faults (page 0)
query("faultInst", limit=20, order_by="faultInst.severity|desc", page=0)
# Next 20 (page 1)
query("faultInst", limit=20, order_by="faultInst.severity|desc", page=1)
```

**One-call exhaustive alternative — `fetch_all=True`.** Instead of paging
manually, walk every page in one call and get the complete matching set back:

```python
query("fvBD", include_children=["fvSubnet"], fetch_all=True)
# → {"results": [...all matching fvBD...], "total_available": N,
#    "truncated": false, "complete": true, ...}
```

`fetch_all` uses `limit` as the page size internally and stops at the first
short page (the natural end) or at a safety cap on very large result sets —
if the cap is hit, `complete` comes back `false` and `total_available` still
tells you the true size; narrow the query (e.g. `scope_dn`) and combine.

**Rule: a max/min/sum/all-of question over a class is only answerable from a
complete set.** If `truncated` is `true` in the response, do not draw that
conclusion from it — re-run with `fetch_all=True`, or page with `page`/`limit`
until `truncated` is `false`, before computing a maximum, minimum, total, or
"all of X" answer.

### Configuration only — `config_only`

A raw object carries ~40 attributes, most of them operational noise (runtime
state, timestamps, monitoring counters). Pass `config_only=True` to `query` or
`get_by_dn` to keep only user-configurable attributes — ideal for comparison,
drift detection, and backup.

```python
# Just the intended config of a BD, no operational churn
query("fvBD", filters={"name": "servers"}, config_only=True)
```

---

## 5.5 Shortcut: `get_by_dn` — fetch one object by DN

When you **already hold an exact DN** (from a previous result, or a design you
are verifying), skip `search_classes` and `get_schema` entirely and read the
object directly. The DN encodes the class, so you do not need to know it up front.

```python
get_by_dn("uni/tn-OT/BD-servers")
# → {"_class": "fvBD", "dn": "uni/tn-OT/BD-servers", "name": "servers", ...}

# config only, with children embedded
get_by_dn("uni/tn-OT/BD-servers",
          config_only=True,
          include_children=["fvSubnet", "fvRsCtx"])
```

Return shape is a single object dict (same shape as one item of `query`'s
`results` list), **not** an envelope and **not** a list. If the DN does not
exist, you get an explicit not-found instead of a silent empty result:

```json
{"found": false, "dn": "uni/tn-OT/BD-typo",
 "message": "No object exists at DN '...'. The DN may be mistyped..."}
```

A not-found usually means a stale or mistyped DN — re-derive it from a fresh
`query` result rather than reconstructing it from memory.

## 5.6 Counting: `count` — how many, cheaply

"How many BDs / EPGs / subnets?" needs a tally, not the objects. `count` answers
in one small request; filtering and scoping work exactly as in `query`.

```python
count("fvBD")                                  # all BDs in the fabric
count("fvAEPg", scope_dn="uni/tn-OT")           # EPGs in tenant OT
count("fvSubnet", filters={"scope": "public"})  # public subnets only
# → {"class_name": "fvSubnet", "count": 12, "scope_dn": null, "filters": {"scope": "public"}}
```

Verify the class name with `search_classes` first — `count` raises the same
`UnknownClassError` (with suggestions) as `query` for an unknown class.

**Counting vs. ranking.** `count` gives a scalar tally — it cannot tell you
*which* object is the extreme case. "How many subnets in total?" is `count`.
"Which bridge domain has the most subnets?" is argmax, and needs the actual
objects: fetch everything with `fetch_all=True`, then group/argmax locally.

```python
query("fvBD", include_children=["fvSubnet"], fetch_all=True)
# → {"results": [...every fvBD, each with "_children"...], "complete": true, ...}
```

```bash
# Then, locally over .results (see section 7 for the full jq recipe):
echo '<json>' | jq '.results | max_by(._children | length) | {name, count: (._children | length)}'
```

Never compute this from a plain default `query()` call — a `truncated: true`
page only contains *some* bridge domains, so its argmax can be the wrong
answer even though the call succeeded and returned data. This is the
canonical example FULL-FABRIC AGGREGATION in the server instructions guards
against.

> **An error is not an answer.** A tool call that raises an error — unknown
> class, unreachable DN, malformed filter — did not execute the query; it has
> no data to report. Never restate that absence as a count of zero, an empty
> result, or "no such objects" — those are valid answers to a query that *did*
> run. When `count` or `query` raises `UnknownClassError`, report that the
> question cannot be answered as asked (the class does not exist), not a
> number.

> **Eventual consistency.** A read taken right after a large config push reflects
> the fabric state *at that instant*. Counts and object sets can keep moving for
> a few seconds while the fabric materialises the change (BDs, EPGs, and
> subnets appear incrementally). If a `count` or `query` right after a write does
> not match what you expect, wait for stabilisation and re-read before drawing a
> conclusion — do not treat the first post-write read as final.

---

## 6. Relation navigation (Rs/Rt pattern)

Relations in ACI are **first-class objects**, not inline attributes.
To answer "what VRF does this BD use?" or "what contracts does this EPG consume?",
you must traverse the relation chain.

**General pattern:**

```
get_schema(ClassA)
→ relationTo: {RsXxx: {targetClass: "pkg:ClassB"}}

query("RsXxx", scope_dn=<objectA_dn>, limit=1)
→ results[0] attributes contain "tn{ClassB}Name": "<target_identifier>"

get_schema("pkgClassB")          ← to find containedBy for scope_dn
→ containedBy: [...]

query("pkgClassB", scope_dn=<parent_dn>, filters={"name": "<target_identifier>"})
→ the target object
```

**Shortcut with `include_children`:** when you need Rs objects alongside their
parent in one call, list the Rs class in `include_children`:

```python
query("fvBD", scope_dn="uni/tn-OT",
      include_children=["fvRsCtx", "fvRsBDToOut"])
# Each BD's _children will contain fvRsCtx (VRF) and fvRsBDToOut (L3Out)
```

The `tn{ClassName}Name` attribute naming convention: the Rs object's attribute
that holds the target's name is `tn` + `TargetClass` (CamelCase) + `Name`.
Example: `fvRsCtx` → attribute `tnFvCtxName` holds the VRF name.

---

## 7. jq quick reference (CLI exploration)

`query`'s output is an envelope — the objects are under `.results`, not at
the top level (see section 2). All recipes below read from there.

```bash
# Check before concluding anything max/min/total/all-of from this response
echo '<json>' | jq '{truncated, total_available, returned, complete}'

# All DNs from a query result
echo '<json>' | jq -r '.results[].dn'

# Specific attribute from all objects
echo '<json>' | jq -r '.results[].name'

# Filter objects where attribute matches value
echo '<json>' | jq '[.results[] | select(.severity == "critical")]'

# Extract schema field
echo '<json>' | jq '{identifiedBy, rnFormat, containedBy}'

# List all relation target classes from schema
echo '<json>' | jq '.relationTo | to_entries[] | {rel: .key, target: .value.targetClass}'

# Count objects per unique attribute value (tally over a fetched set)
echo '<json>' | jq '.results | group_by(.severity) | map({(.[0].severity): length}) | add'

# Argmax over a class — "which BD has the most subnets" (requires
# fetch_all=True first; see section 5.6, Counting vs. ranking)
echo '<json>' | jq '.results | max_by(._children | length) | {name, count: (._children | length)}'

# Extract _children of a specific class (include_children results)
echo '<json>' | jq '.results[]._children[] | select(._class == "fvSubnet") | .ip'

# Flatten parent + children into one table
echo '<json>' | jq '[.results[] | {bd: .name, subnet: (._children // [] | map(select(._class=="fvSubnet")) | .[0].ip // "-"), vrf: (._children // [] | map(select(._class=="fvRsCtx")) | .[0].tnFvCtxName // "-")}]'
```

---

## 8. Common attribute values

Attribute values in APIC are always strings. These are common enumerations
to use in `filters` and `filter_expr` — guessing the wrong casing returns `[]` silently:

| Class | Attribute | Values |
|---|---|---|
| `faultInst` | `severity` | `critical` · `major` · `minor` · `warning` · `cleared` |
| `fabricNode` | `role` | `spine` · `leaf` · `controller` |
| `fabricNode` | `fabricSt` | `active` · `inactive` · `discovering` |
| `topSystem`  | `state` | `in-service` · `out-of-service` · `unknown` |
| any          | `adminSt` | `enabled` · `disabled` |
| `fvBD`       | `unicastRoute` | `yes` · `no` |
| `fvBD`       | `arpFlood` | `yes` · `no` |

For any other class, call `get_schema` and read `properties` — then query
a sample object without filters to observe the actual values in context.

---

## 9. Workflow

```
1. search_classes(keyword)
        ↓ returns ranked list of {class_name, label, comment}
        Pick the most relevant. If ambiguous, get_schema on top 2-3
        candidates and compare containedBy to narrow down.

2. get_schema(class_name)
        ↓ read identifiedBy → filter keys
           read containedBy → need parent DN?
           read rnFormat    → can I derive scope_dn directly?
           read relationTo  → what can I navigate from here?
           read properties  → what can I filter on?

3. Plan before querying:
        - Do I need scope_dn?
            → Yes if the user named a specific parent (tenant X, node Y)
            → Query the parent class first, get its dn
        - What filters?
            → Simple equality: use filters={}
            → wcard / ne / and-or combinations: use filter_expr
        - Do I need children inline?
            → Yes if retrieving Rs relations or subnets alongside parents
            → Use include_children=["RsClass", "ChildClass"]
            → Avoids N separate queries — one call per parent
        - Is this a log/audit query?
            → Use time_range="24h" / "1week" / date range
        - Large result set?
            → Use limit + page for pagination
        - Aggregating over the WHOLE class (max/min/total/all)?
            → Use fetch_all=True, and check truncated/complete before
              concluding — see section 5's Pagination subsection and 5.6

4. query(class_name, ...)
        ↓ envelope: {"results": [...], "returned", "total_available",
                     "truncated", "next_page", "complete", "note"}
          each result item: attribute dict + "_class"
          dn is always present and is a valid scope_dn for children
          _children present when include_children was set
          truncated=true → do not conclude a max/min/total/all-of answer;
          re-run with fetch_all=True or page to exhaustion first

5. Navigate further if needed:
        - Children: query child class with scope_dn = result dn
        - Relations: follow Rs pattern (section 6), or use include_children
        - Siblings: query same class with scope_dn = parent dn

6. Synthesize and answer:
        Never dump raw JSON. Extract relevant attributes, explain what
        the data means operationally. Highlight anomalies.
        State only what is actually present in a tool result from this
        conversation. A property name, a configured value, a DN template,
        or a count is only fit to appear in your answer if you can point
        to the tool call that returned it — do not fill a gap with general
        ACI product knowledge, since a real deployment's schema and
        configuration vary by APIC version, customization, and applied
        config. If you aren't sure a detail was actually returned, say so,
        or make another call to check, rather than stating it as fact.
```

### Error handling

| Symptom | Cause | Recovery |
|---|---|---|
| `query`/`count` raises "Unknown ACI class '...'" | Wrong or nonexistent class name | The class does not exist — this is a failed lookup, not a zero result. Retry with one of the suggested closest matches or a fresh `search_classes` call; never report a count or existence answer from this error. |
| `query` returns `results: []`, class is valid | Object absent from backend OR wrong filter value | Remove filters first to confirm objects exist, then re-add filters |
| `query` returns `results: []`, class is abstract (`isAbstract: true`) | Abstract class — not instantiable | `search_classes` to find the concrete subclass |
| `query` returns `truncated: true` | This call only returned part of the matching set (`returned < total_available`) | Partial data — do not conclude a maximum, minimum, total, or complete list from it. Re-run with `fetch_all=True`, or page with `page`/`limit` until `truncated` is `false` |
| `search_classes` returns no results | Keyword too specific | Try acronym, English label, or first 3 chars of the expected class name |
| `get_schema` returns `{}` | Class not in local schema collection | Query without filters, inspect `properties` of a sample result |
| `_children` is empty despite `include_children` | Children don't exist under that parent, or wrong child class name | Query child class directly with scope_dn to verify |
| `get_by_dn` returns `{"found": false, ...}` | DN is stale, mistyped, or the object was deleted | Re-derive the DN from a fresh `query` result — never reconstruct it from memory |
| `count` disagrees with a follow-up `query` | Read taken mid-materialisation after a config push | Wait for stabilisation and re-read (eventual consistency, section 5.6) |

---
