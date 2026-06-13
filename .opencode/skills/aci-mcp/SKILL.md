---
name: aci-mcp
description: "Use when the user asks about a Cisco ACI fabric — faults, tenants, bridge domains, EPGs, VRFs, contracts, fabric nodes, routing, endpoints, audit logs, or any ACI object. Always call search_classes → get_schema → query in that order."
trigger: /aci
---

You have three MCP tools: `search_classes`, `get_schema`, `query`.

**Mandatory order — never skip steps:**
1. `search_classes(keyword)` — verify the exact class name
2. `get_schema(class_name)` — learn valid filter attributes, containment, relations
3. `query(class_name, ...)` — execute against the APIC

Skipping step 1 or 2 causes silent empty results — the APIC returns `[]` for unknown class names or wrong attribute names without any error.

---

## The ACI Object Model

Every entity in ACI is a **Managed Object (MO)**: a typed node in a tree.

```
polUni
  fvTenant
    fvCtx                   ← VRF
    fvBD                    ← bridge domain
      fvSubnet              ← subnet
    fvAp                    ← application profile
      fvAEPg                ← EPG
        fvRsCons            ← relation: consumes contract
  fabricTopology
    fabricPod
      fabricNode            ← spine / leaf / controller
  faultSummary
    faultInst               ← individual fault
```

Every MO has:
- A **class name** (camelCase, package prefix: `fv`, `fabric`, `fault`…)
- A **Distinguished Name (DN)** — the full path from root, unique across the fabric
- A flat bag of **attributes** (all strings, even booleans and numbers)

---

## query() return shape

```json
[
  {
    "_class": "fvBD",
    "dn": "uni/tn-OT/BD-servers",
    "name": "servers",
    "unicastRoute": "yes",
    "_children": [
      { "_class": "fvSubnet", "dn": "uni/tn-OT/BD-servers/subnet-[10.0.0.1/24]", "ip": "10.0.0.1/24" },
      { "_class": "fvRsCtx",  "dn": "uni/tn-OT/BD-servers/rsctx", "tnFvCtxName": "ot.main.vrf" }
    ]
  }
]
```

`_children` only present when `include_children` is set.

---

## get_schema() — key fields

| Field | How to use |
|---|---|
| `identifiedBy` | These attributes are valid `filters` keys |
| `containedBy` | `pkg:Class` → `pkgClass` — query parent to get `scope_dn` |
| `rnFormat` | DN component template, e.g. `"BD-{name}"` |
| `properties` | Only these attribute names are valid in filters — wrong name = silent `[]` |
| `relationTo` | `{RsClass: {targetClass}}` — traverse with `include_children=[RsClass]` |

---

## query() parameters

| Parameter | Description |
|---|---|
| `filters` | `{attr: value}` — equality filters, auto-combined with `and()` |
| `scope_dn` | Parent DN — subtree query, faster than fabric-wide scan |
| `limit` | Max objects (default 20, capped at 200) |
| `include_children` | Embed child classes: `["fvSubnet", "fvRsCtx"]` |
| `filter_expr` | Raw APIC filter: `wcard`, `ne`, `gt`, `and`/`or` |
| `rsp_subtree_include` | `"faults"` · `"health"` · `"audit-logs"` · `"faults,required"` |
| `time_range` | Log records: `"24h"` · `"1week"` · `"2026-01-01\|2026-01-31"` |
| `order_by` | e.g. `"faultInst.severity\|desc"` |
| `page` | 0-based pagination |

---

## Recipes

```python
# All BDs in a tenant
tenants = await query("fvTenant", filters={"name": "OT"})
bds = await query("fvBD", scope_dn=tenants[0]["dn"])

# BD with subnets + VRF in one call
query("fvBD", filters={"name": "servers"},
      include_children=["fvSubnet", "fvRsCtx"])

# Active critical faults
query("faultInst",
      filter_expr='eq(faultInst.severity,"critical")',
      order_by="faultInst.created|desc")

# Recent audit log
query("aaaModLR", time_range="24h", order_by="aaaModLR.created|desc")

# All active non-controller nodes
query("fabricNode",
      filter_expr='and(ne(fabricNode.role,"controller"),eq(fabricNode.fabricSt,"active"))')
```

---

## Common attribute values

| Class | Attribute | Values |
|---|---|---|
| `faultInst` | `severity` | `critical` · `major` · `minor` · `warning` · `cleared` |
| `fabricNode` | `role` | `spine` · `leaf` · `controller` |
| `fabricNode` | `fabricSt` | `active` · `inactive` · `discovering` |
| `fvBD` | `unicastRoute` | `yes` · `no` |
| `fvBD` | `arpFlood` | `yes` · `no` |
| any | `adminSt` | `enabled` · `disabled` |

---

## Relation navigation (Rs/Rt)

Relations in ACI are first-class objects. To find which VRF a BD uses:

```python
# Option A — include_children (one call)
query("fvBD", scope_dn="uni/tn-OT",
      include_children=["fvRsCtx"])
# → bd["_children"][0]["tnFvCtxName"] = VRF name

# Option B — two calls
rs = query("fvRsCtx", scope_dn=bd["dn"], limit=1)
vrf_name = rs[0]["tnFvCtxName"]
query("fvCtx", scope_dn="uni/tn-OT", filters={"name": vrf_name})
```

`tn{TargetClass}Name` naming convention: `fvRsCtx` → `tnFvCtxName`.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `UnknownClassError` with suggestions | Wrong class name | Use a suggestion or `search_classes` |
| `query` returns `[]`, no error | Wrong filter attribute or value | Remove filters first, confirm objects exist |
| `query` returns `[]`, `isAbstract: true` | Abstract class | `search_classes` for concrete subclass |
| `_children` empty | Wrong child class name or no children exist | Query child class directly with `scope_dn` |
