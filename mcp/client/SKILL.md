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
`get_by_dn(dn)` directly — no search/schema detour. See section 6.

**Scope.** This MCP answers object lookup and relation-traversal questions —
including multi-hop chains you compose yourself from `relationTo`/
`relationFrom` and repeated queries. It has no primitive for causal or impact
reasoning (blast-radius, root-cause propagation across domains, predicting
what a change will do downstream). Do not synthesize that kind of conclusion
from traversal results — report the structure you found and say impact
analysis is out of scope.

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

`options` removes the guesswork behind section 10: never guess an enum's casing —
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
  "fv:RsCtx":       {"targetClass": "fv:Ctx",       "cardinality": ""},
  "fv:RsBDToOut":   {"targetClass": "l3ext:Out",    "cardinality": ""},
  "fv:RsBdFloodTo": {"targetClass": "vz:Filter",     "cardinality": ""}
}
```

(Real `get_schema("fvBD")` output — keys keep their colon notation, e.g.
`fv:RsCtx`, not the flattened `fvRsCtx` form used by `contains`.)

Each key is a **Relation Source (Rs)** class — an intermediate object that
lives under this MO and holds the reference to the target.
See section 8 for how to traverse it.

### `relationFrom` — incoming Rt relations (another object → this one)

```json
"relationFrom": {
  "fv:RtCtx": {"sourceClass": "fv:ABDPol"}
}
```

Note the key keeps its colon notation (`fv:RtCtx`), unlike `contains`, which is flattened.

Reverse lookups: which objects of `sourceClass` point to this one. This is the
equivalent of the APIC UI's **Show Usage** — the APIC materialises an Rt object
as a *child of the target*, one per referring object, and each Rt's `tDn` is
the DN of the source that refers to it. So "who uses this BD?" is answered by
listing the BD's Rt children:

```python
get_by_dn("uni/tn-OT/BD-servers", include_children=["fvRtBd"])
# each _children entry's tDn is a source object's DN, e.g. an fvAEPg
```

**Rt objects carry no `state`.** Their attributes are `tDn`, `tCl`, `dn`, `rn`,
`status`, `lcOwn`, `modTs` — no `state`, no `stateQual`. Relation health exists
only on the *outgoing* (Rs) side, so `relationTo` and `relationFrom` are not
symmetric: you can ask "is my reference to X healthy?" but not "is the
reference to me healthy?" from this side. To judge that, fetch the source's own
Rs object and read its `state` (section 8).

### `isAbstract`

If `true`, the class cannot be directly **created** (no POST/config target)
— but `query` does NOT always return `[]` for it. An abstract superclass
(e.g. `fvABDPol`) can still be queried, returning the polymorphic union of
its concrete subclasses' instances — this is standard APIC MIT behavior,
not an error. If you specifically need the concrete class name, use
`search_classes` to find it instead of assuming an abstract query is empty.

---

## 5. `query` parameters reference

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
Use to avoid N+1 query patterns — see the batching rule in section 11's
Workflow before looping this per object.

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

## 6. Shortcut: `get_by_dn` — fetch one object by DN

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

---

## 7. Counting: `count` — how many, cheaply

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
# Then, locally over .results — the full recipe:
echo '<json>' | python3 -c '
import json, sys
d = json.load(sys.stdin)
top = max(d["results"], key=lambda r: len(r.get("_children", [])))
print(top["name"], len(top.get("_children", [])))
'
```

This is exactly the case section 5's Pagination rule guards against — do not
compute an argmax from a plain default `query()` call; see there for why.

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

## 8. Relation navigation (Rs/Rt pattern)

Relations in ACI are **first-class objects**, not inline attributes.
To answer "what VRF does this BD use?" or "what contracts does this EPG consume?",
you must traverse the relation chain.

### The rule that governs this whole section

> **Never report what an object points to without reading the relation's
> `state`.** An Rs object records the target that was *configured*. That
> record survives the target being deleted, renamed, or never created — so
> the target's name being present is not evidence that the target exists.
> `state` is the APIC's own verdict on whether the reference actually
> resolved, and it is the only field that carries that verdict.

This is not a theoretical edge case. Observed on a live fabric:

```
spanRsSrcGrpToFilterGrp   state = missing-target
                          tDn   = uni/infra/filtergrp-niwaki-it-fg

get_by_dn("uni/infra/filtergrp-niwaki-it-fg")
  → returns 1 object, class spanFilterGrp
```

The `tDn` is not empty, and fetching it **returns a real object**. An agent
that "double-checks" by resolving the target concludes everything is fine,
while the APIC is saying the relation never formed. No sequence of follow-up
queries substitutes for reading `state`.

### Reading `state` and `stateQual`

Every Rs class inherits these two from the abstract `relnTo`. They are
orthogonal — read `state` first, then let `stateQual` qualify it:

| `state` | Meaning |
|---|---|
| `formed` | Resolved. The reference points at a real object. |
| `missing-target` | **Not resolved.** The configured target was not found. |
| `invalid-target` | **Not resolved.** The target exists but is not valid here. |
| `cardinality-violation` | **Not resolved.** Too many/few targets for this relation. |
| `unformed` | **Ambiguous — do not report as a fault on its own.** It is the property's default value, and it is also what many internal/system relations sit at permanently. |

`missing-target`, `invalid-target` and `cardinality-violation` are definite:
the APIC tried and failed. `unformed` is not. Sweeping all 48 tenants of the
lab fabric — 4753 relations — only 24 were not `formed`, and **22 of those
were `unformed`**, most with a `tDn` that resolves perfectly well:

```
vzRsRFltPOwner    state=unformed  tDn=uni/tn-mgmt/flt-...   → resolves (vzFilter)
mgmtRsInBStNode   state=unformed  tDn=topology/pod-1/node-101 → resolves (fabricNode)
```

Calling those "broken" would be a false positive. What distinguishes a real
problem is the target *not* existing:

```
fvRsPathAtt  state=unformed  tDn=topology/pod-1/paths-999/pathep-[eth1/99]
             → get_by_dn(tDn) returns 0 objects   ← configured against a path
                                                    that does not exist
```

So for `unformed`, report "not resolved / not confirmed" and check whether
the target exists before calling it a fault. Never report it as a healthy,
configured target either.

| `stateQual` | Meaning |
|---|---|
| `none` | Nothing to add. |
| `default-target` | Resolved to an **inherited default policy**, not to anything configured on this object. |
| `mismatch-target` | Resolved, but not to the kind of target expected. Suspect. |

`default-target` matters more than it looks: on the lab fabric **2220 of
4753 tenant relations — 47% — resolve that way**. Reporting "this BD uses
IGMP snooping policy `default`" as a design decision is wrong there — nobody
chose it, it was inherited. Say "inherited default" or do not mention it.

A relation with `state` absent (an Rt object, or a `config_only` response)
is **unknown**, never healthy. Do not infer "fine" from a missing field.

### Finding the target: `tDn` first, `tn*Name` only sometimes

`tDn` is the canonical field. It holds the resolved DN of the target and is
present on every Rs class.

`tn{TargetClass}Name` — e.g. `fvRsCtx` → `tnFvCtxName` — exists **only on
relations the model declares as `named`: 310 of the 1531 Rs classes, about
20%.** The other 1189 are `explicit` and carry no `tn*Name` at all. Reading
`tnFvCtxName` off one of those returns nothing, which is easy to mistake for
"not configured".

So: read `tDn`. Fall back to a `tn*Name` only when `tDn` is empty and you
have confirmed the attribute exists in `get_schema().properties`. A handful
of classes carry two (`fvRsBDToProfile` has `tnL3extOutName` **and**
`tnRtctrlProfileName`), so do not assume there is exactly one.

**General pattern:**

```
get_schema(ClassA)
→ relationTo: {RsXxx: {targetClass: "pkg:ClassB"}}

query("RsXxx", scope_dn=<objectA_dn>, limit=1)
→ results[0]: read state FIRST.
    state != "formed"  → stop. Report it as unresolved. Do not resolve tDn
                         and do not present the configured name as the target.
    state == "formed"  → tDn holds the target's DN; check stateQual for
                         "default-target" before calling it a configured choice.

get_by_dn(<tDn>)                 ← the target object, in one call
```

**Shortcut with `include_children`:** when you need Rs objects alongside their
parent in one call, list the Rs class in `include_children`:

```python
query("fvBD", scope_dn="uni/tn-OT",
      include_children=["fvRsCtx", "fvRsBDToOut"])
# Each BD's _children will contain fvRsCtx (VRF) and fvRsBDToOut (L3Out),
# each with its own state/stateQual/tDn — read them.
```

### Two traps, both measured on a real fabric

**Do not filter on relation properties.** `filter_expr` predicates against
`state`, `stateQual`, `tDn` or `tn*Name` come back **HTTP 200 with zero
results**, even when matching objects demonstrably exist:

```python
# Subtree really holds 192 fvRsCtx, all of them state="formed":
query("fvRsCtx", scope_dn="uni/tn-X",
      filter_expr='eq(fvRsCtx.state,"formed")')   # → 0 results. Not an error.
query("fvRsCtx", scope_dn="uni/tn-X",
      filter_expr='ne(fvRsCtx.state,"formed")')   # → 0 results. Also not an error.
```

Both directions return nothing, so neither result can be believed. Fetch the
relation objects unfiltered and inspect `state` locally instead.

**Do not sweep relations fabric-wide.** A subtree query for `relnTo` over
`uni` returns a handful of objects rather than the real population, with no
error and no `truncated` signal — the count is simply wrong. Scope relation
work to one tenant (or one object) at a time, and never conclude "there are
no broken relations" from a wide sweep.

---

## 9. Local post-processing with python3 (CLI exploration)

`query`'s output is an envelope — the objects are under `.results`, not at
the top level (see section 2). All recipes below read from there.

```bash
# Check before concluding anything max/min/total/all-of from this response
echo '<json>' | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k: d[k] for k in ("truncated","total_available","returned","complete")})'

# All DNs from a query result
echo '<json>' | python3 -c 'import json,sys; [print(r["dn"]) for r in json.load(sys.stdin)["results"]]'

# Specific attribute from all objects
echo '<json>' | python3 -c 'import json,sys; [print(r["name"]) for r in json.load(sys.stdin)["results"]]'

# Filter objects where attribute matches value
echo '<json>' | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps([r for r in d["results"] if r.get("severity")=="critical"]))'

# Extract schema field
echo '<json>' | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k: d.get(k) for k in ("identifiedBy","rnFormat","containedBy")})'

# List all relation target classes from schema
echo '<json>' | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(rel, info["targetClass"]) for rel, info in d.get("relationTo", {}).items()]'

# Count objects per unique attribute value (tally over a fetched set)
echo '<json>' | python3 -c '
import json, sys
from collections import Counter
d = json.load(sys.stdin)
print(dict(Counter(r["severity"] for r in d["results"])))
'

# Argmax over a class ("which BD has the most subnets") — see section 7
# (`count`, Counting vs. ranking) for the full recipe; needs fetch_all=True first.

# Extract _children of a specific class (include_children results)
echo '<json>' | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(c["ip"]) for r in d["results"] for c in r.get("_children", []) if c["_class"] == "fvSubnet"]'

# Flatten parent + children into one table
echo '<json>' | python3 -c '
import json, sys
d = json.load(sys.stdin)
for r in d["results"]:
    ch = r.get("_children", [])
    subnet = next((c["ip"] for c in ch if c["_class"] == "fvSubnet"), "-")
    vrf = next((c["tnFvCtxName"] for c in ch if c["_class"] == "fvRsCtx"), "-")
    print(r["name"], subnet, vrf)
'
```

---

## 10. Common attribute values

Attribute values in APIC are always strings. These are common enumerations
to use in `filters` and `filter_expr` — guessing the wrong casing returns `[]` silently:

| Class | Attribute | Values |
|---|---|---|
| `faultInst` | `severity` | `critical` · `major` · `minor` · `warning` · `cleared` |
| `fabricNode` | `role` | `spine` · `leaf` · `controller` |
| `fabricNode` | `fabricSt` | `active` · `inactive` · `discovering` |
| `topSystem`  | `state` | `in-service` · `out-of-service` · `downloading-boot-script` · `downloading-firmware` · `invalid-ver` · `requesting-tep` |
| any          | `adminSt` | `enabled` · `disabled` |
| `fvBD`       | `unicastRoute` | `yes` · `no` |
| `fvBD`       | `arpFlood` | `yes` · `no` |
| any Rs relation | `state` | `formed` · `missing-target` · `invalid-target` · `cardinality-violation` · `unformed` |
| any Rs relation | `stateQual` | `none` · `default-target` · `mismatch-target` |

`state` and `stateQual` are readable but **not filterable** — a `filter_expr`
against either returns zero results without erroring. See section 8.

For any other class, call `get_schema` and read `properties` — then query
a sample object without filters to observe the actual values in context.

---

## 11. Workflow

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
        - Do I need children inline, or would I otherwise loop per object?
            → "For each X in this scope, give me Y" is ONE call: scope_dn
              (+ include_children or fetch_all), then aggregate locally.
            → RULE: never loop a tool call per object — never N separate
              get_by_dn/query calls, one per X. Reach for a per-object call
              only for an object nothing else already returned.
        - Is this a log/audit query?
            → Use time_range="24h" / "1week" / date range
        - Large result set?
            → Use limit + page for pagination
        - Aggregating over the WHOLE class (max/min/total/all)?
            → Use fetch_all=True, and check truncated/complete before
              concluding — see section 5's Pagination subsection and
              section 7

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
        - Relations: follow Rs pattern (section 8), or use include_children.
          Read `state` on every Rs object before reporting what it points
          to — a configured target name outlives the target itself.
        - Who uses this object: list its Rt children (section 4)
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
        This also covers class-name/property-name trivia: do not explain
        what a class prefix (fv, vz, mo...) "stands for" historically, or
        why a property is named the way it is — that etymology was never
        returned by any tool, no matter how plausible it sounds.
```

### Error handling

| Symptom | Cause | Recovery |
|---|---|---|
| `query`/`count` raises "Unknown ACI class '...'" | Wrong or nonexistent class name | The class does not exist — this is a failed lookup, not a zero result. Retry with one of the suggested closest matches or a fresh `search_classes` call; never report a count or existence answer from this error. |
| `query` returns `results: []`, class is valid | Object absent from backend OR wrong filter value — this applies even to an abstract class (`isAbstract: true`): querying one returns the union of its concrete subclasses' instances, not an automatic `[]` | Remove filters first to confirm objects exist, then re-add filters |
| `query` returns `truncated: true` | This call only returned part of the matching set (`returned < total_available`) | Partial data — do not conclude a maximum, minimum, total, or complete list from it. Re-run with `fetch_all=True`, or page with `page`/`limit` until `truncated` is `false` |
| `search_classes` returns no results | Keyword too specific | Try acronym, English label, or first 3 chars of the expected class name |
| `get_schema` returns `{}` | Class not in local schema collection | Query without filters, inspect `properties` of a sample result |
| `_children` is empty despite `include_children` | Children don't exist under that parent, or wrong child class name | Query child class directly with scope_dn to verify |
| `get_by_dn` returns `{"found": false, ...}` | DN is stale, mistyped, or the object was deleted | Re-derive the DN from a fresh `query` result — never reconstruct it from memory |
| `count` disagrees with a follow-up `query` | Read taken mid-materialisation after a config push | Wait for stabilisation and re-read (eventual consistency, section 7) |
| An Rs relation is `missing-target` / `invalid-target` / `cardinality-violation` | The reference is configured but the APIC tried and failed to resolve it | Report the relation as unresolved. Do **not** resolve the name or DN and present the result as the object's target: a `missing-target` DN can still answer `get_by_dn` (section 8) |
| An Rs relation is `state: unformed` | Ambiguous — the property's default value, and also the permanent resting state of many internal relations | Report "not resolved", not "broken". Check whether `tDn` resolves before calling it a fault; most observed `unformed` relations had targets that exist |
| An Rs relation is `state: formed` with `stateQual: default-target` | Resolved to an inherited default policy, not to anything configured here | Say "inherited default", or leave it out. Do not present it as a design decision — 43% of tenant relations resolve this way |
| An Rs relation has no `state` field at all | It is an Rt object (Rt carries no health), or the response was `config_only` | Treat as unknown, never as healthy. Re-fetch without `config_only`, or read the source's own Rs object |
| A `filter_expr` on `state`/`stateQual`/`tDn` returns `[]` | Relation properties are not filterable server-side — both `eq` and `ne` return zero without erroring | Fetch the relation objects unfiltered and inspect `state` locally. Never read this `[]` as "nothing is broken" |

---

## 12. Worked example: "What VRF does BD `servers` in tenant `OT` use?"

```
1. search_classes("bridge domain")
   → confirms fvBD

2. get_schema("fvBD")
   → identifiedBy=["name"], containedBy=["fv:Tenant"],
     relationTo={"fvRsCtx": {"targetClass": "fv:Ctx", ...}}

3. query("fvTenant", filters={"name": "OT"})
   → results[0].dn = "uni/tn-OT"

4. query("fvBD", scope_dn="uni/tn-OT", filters={"name": "servers"},
         include_children=["fvRsCtx"])
   → results[0]._children[0] = {"_class": "fvRsCtx",
                                 "state": "formed", "stateQual": "none",
                                 "tDn": "uni/tn-OT/ctx-ot.main.vrf",
                                 "tnFvCtxName": "ot.main.vrf", ...}

5. Read state BEFORE concluding.
   state == "formed" and stateQual == "none"
   → the reference really resolved, to a target nobody inherited.

6. Synthesize: "BD `servers` in tenant OT uses VRF `ot.main.vrf`
   (uni/tn-OT/ctx-ot.main.vrf)."
```

Step 4 combines the relation lookup with the parent fetch via
`include_children` in one call. The full unshortcut Rs-traversal pattern
(query the `fvRsCtx` object directly, scoped under the BD) is in section 8
and is only needed when you must inspect the Rs object itself — its own DN
or other attributes — not just the target's identifier.

### The same example when the relation is broken

Step 4 returns a child that looks almost identical:

```
results[0]._children[0] = {"_class": "fvRsCtx",
                            "state": "missing-target", "stateQual": "none",
                            "tDn": "uni/tn-OT/ctx-ot.main.vrf",
                            "tnFvCtxName": "ot.main.vrf", ...}
```

Only `state` changed. `tnFvCtxName` still says `ot.main.vrf` and `tDn` still
holds a full DN, because both record what was *configured* — they outlive the
VRF they name. Answering "BD `servers` uses VRF `ot.main.vrf`" from this
response is wrong: the BD has **no** VRF, and that is very likely the
operational problem being investigated.

> This particular response is illustrative — the shape is what matters. The
> mechanism behind it is real and measured: see the `spanRsSrcGrpToFilterGrp`
> case in section 8, where a `missing-target` relation's `tDn` not only
> persists but successfully resolves to a live object.

The correct answer names the fault, not the ghost target:

> BD `servers` in tenant OT has **no resolved VRF**. Its `fvRsCtx` relation
> is in `state=missing-target`, still configured to point at
> `ot.main.vrf`, which the APIC cannot resolve — the VRF is missing or was
> deleted.

Whenever `state` is not `formed`, report the unresolved relation. Do not
resolve the name yourself and present the result as the object's target;
see the `spanRsSrcToPathEp` case in section 8, where the DN behind a
`missing-target` relation still answers a direct fetch.

---
