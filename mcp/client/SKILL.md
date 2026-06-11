---
name: aci-mcp-query
description: Query the ACI APIC controller via MCP tools. Use when the user asks about ACI infrastructure — faults, tenants, bridge domains, EPGs, VRFs, contracts, fabric nodes, routing, endpoints, audit logs.
---

You have three MCP tools: `search_classes`, `get_schema`, `query`.
This skill explains the ACI object model, the data structures the tools
return, how to read a schema, and how to navigate the object tree.

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

The `query` tool returns a flat list. Each item is the object's attributes
plus a `_class` key injected by the MCP server:

```json
[
  {
    "_class": "<ClassName>",
    "dn": "<full/path/to/object>",
    "name": "<identifier>",
    "<attr>": "<value>",
    ...
  }
]
```

All attribute values are **strings** (including numbers and booleans).

When `include_children` is set, each object also contains `_children` — a flat
list of child attribute dicts, each with their own `_class` key:

```json
[
  {
    "_class": "fvBD",
    "dn": "uni/tn-OT/BD-servers",
    "unicastRoute": "yes",
    "_children": [
      {"_class": "fvSubnet", "dn": "uni/tn-OT/BD-servers/subnet-[10.0.0.1/24]", "ip": "10.0.0.1/24"},
      {"_class": "fvRsCtx",  "dn": "uni/tn-OT/BD-servers/rsctx", "tnFvCtxName": "ot.main.vrf"}
    ]
  }
]
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

### Pagination — `page`

Works with `limit` (= page-size). Pages are 0-based.

```python
# First 20 faults (page 0)
query("faultInst", limit=20, order_by="faultInst.severity|desc", page=0)
# Next 20 (page 1)
query("faultInst", limit=20, order_by="faultInst.severity|desc", page=1)
```

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
→ result attributes contain "tn{ClassB}Name": "<target_identifier>"

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

```bash
# All DNs from a query result
echo '<json>' | jq -r '.[].dn'

# Specific attribute from all objects
echo '<json>' | jq -r '.[].name'

# Filter objects where attribute matches value
echo '<json>' | jq '[.[] | select(.severity == "critical")]'

# Extract schema field
echo '<json>' | jq '{identifiedBy, rnFormat, containedBy}'

# List all relation target classes from schema
echo '<json>' | jq '.relationTo | to_entries[] | {rel: .key, target: .value.targetClass}'

# Count objects per unique attribute value
echo '<json>' | jq 'group_by(.severity) | map({(.[0].severity): length}) | add'

# Extract _children of a specific class (include_children results)
echo '<json>' | jq '.[].`_children`[] | select(._class == "fvSubnet") | .ip'

# Flatten parent + children into one table
echo '<json>' | jq '[.[] | {bd: .name, subnet: (._children // [] | map(select(._class=="fvSubnet")) | .[0].ip // "-"), vrf: (._children // [] | map(select(._class=="fvRsCtx")) | .[0].tnFvCtxName // "-")}]'
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

4. query(class_name, ...)
        ↓ results: list of attribute dicts + "_class"
          dn is always present and is a valid scope_dn for children
          _children present when include_children was set

5. Navigate further if needed:
        - Children: query child class with scope_dn = result dn
        - Relations: follow Rs pattern (section 6), or use include_children
        - Siblings: query same class with scope_dn = parent dn

6. Synthesize and answer:
        Never dump raw JSON. Extract relevant attributes, explain what
        the data means operationally. Highlight anomalies.
```

### Error handling

| Symptom | Cause | Recovery |
|---|---|---|
| `query` returns `{"error": ..., "closest_matches": [...]}` | Wrong class name | `search_classes` with a closest_match or synonym |
| `query` returns `[]`, class is valid | Object absent from backend OR wrong filter value | Remove filters first to confirm objects exist, then re-add filters |
| `query` returns `[]`, class is abstract (`isAbstract: true`) | Abstract class — not instantiable | `search_classes` to find the concrete subclass |
| `search_classes` returns no results | Keyword too specific | Try acronym, English label, or first 3 chars of the expected class name |
| `get_schema` returns `{}` | Class not in local schema collection | Query without filters, inspect `properties` of a sample result |
| `_children` is empty despite `include_children` | Children don't exist under that parent, or wrong child class name | Query child class directly with scope_dn to verify |

---
