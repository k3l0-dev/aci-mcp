# Tool: get_schema

Inspect the structural schema of an ACI class — identifiers, containment, relations, and available properties. **Always call this before `query()`** to know which attributes exist.

---

## Signature

```python
get_schema(class_name: str) -> dict[str, Any]
```

| Parameter | Type | Description |
|---|---|---|
| `class_name` | `str` | Exact ACI class name from `search_classes()` |

---

## Return value

A dict with the following fields (all optional — only present when the schema contains them):

| Field | Type | Description |
|---|---|---|
| `identifiedBy` | `list[str]` | Attributes that uniquely identify an instance — use these as `filters` keys in `query()` |
| `rnFormat` | `str` | Relative-name template, e.g. `"BD-{name}"` |
| `containedBy` | `list[str]` | Parent class names in `pkg:Class` notation — use a parent object's `dn` as `scope_dn` |
| `dnFormats` | `list[str]` | Full DN pattern examples |
| `relationTo` | `dict` | Outgoing Rs relations: `{relClass: {targetClass, cardinality}}` |
| `relationFrom` | `dict` | Incoming Rt relations: `{relClass: {sourceClass}}` |
| `properties` | `list[str]` | Sorted list of all attribute names available on the class |
| `isAbstract` | `bool` | `true` when the class cannot be directly instantiated |
| `isConfigurable` | `bool` | `true` when objects can be created/modified via APIC |
| `className` | `str` | Short name without package prefix, e.g. `"BD"` |
| `classPkg` | `str` | Package prefix, e.g. `"fv"` |
| `label` | `str` | Human-readable label |

Returns `{}` when the class file is not found in the local schema collection.

---

## Example output

```json
{
  "identifiedBy": ["name"],
  "rnFormat": "BD-{name}",
  "containedBy": ["fv:Tenant"],
  "dnFormats": ["uni/tn-{name}/BD-{name}"],
  "relationTo": {
    "fvRsCtx": {
      "targetClass": "fvCtx",
      "cardinality": "One"
    }
  },
  "properties": ["arpFlood", "descr", "dn", "epMoveDetectMode", "ipLearning",
                  "limitIpLearnToSubnets", "mcastAllow", "multiDstPktAct",
                  "name", "nameAlias", "status", "type", "uid", "unicastRoute"],
  "isAbstract": false,
  "isConfigurable": true,
  "className": "BD",
  "classPkg": "fv",
  "label": "Bridge Domain"
}
```

---

## How to use the schema

```mermaid
flowchart TD
    SCHEMA["get_schema result"]

    SCHEMA -->|"identifiedBy"| F["Use as keys in query() filters\ne.g. {\"name\": \"servers\"}"]
    SCHEMA -->|"containedBy → fv:Tenant"| SCOPE["Fetch parent fvTenant objects\nuse dn as scope_dn in query()"]
    SCHEMA -->|"properties"| VALID["Only these attribute names\nare valid filter keys"]
    SCHEMA -->|"relationTo → fvCtx"| REL["fvBD is related to fvCtx via fvRsCtx\nquery fvRsCtx to find the linked VRF"]
    SCHEMA -->|"dnFormats"| DN["Understand what a dn looks like\nfor building scope_dn manually"]
```

---

## Reading containedBy

`containedBy` uses colon notation — `"fv:Tenant"` means class `fvTenant` (package `fv`, short name `Tenant`). To scope a `fvBD` query to a specific tenant:

```python
# 1. Get the tenant dn
tenants = await query("fvTenant", filters={"name": "OT"})
scope = tenants[0]["dn"]  # "uni/tn-OT"

# 2. Use it as scope_dn
bds = await query("fvBD", scope_dn=scope)
```

---

## Reading relationTo

```json
"relationTo": {
  "fvRsCtx": {
    "targetClass": "fvCtx",
    "cardinality": "One"
  }
}
```

`fvBD` has an outgoing relation to `fvCtx` (VRF) via the relation class `fvRsCtx`. To find which VRF a BD is associated with:

```python
# include the relation object as a child
results = await query("fvBD", include_children=["fvRsCtx"])
for bd in results:
    for child in bd.get("_children", []):
        if child["_class"] == "fvRsCtx":
            print(bd["name"], "→", child["tnFvCtxName"])
```

---

## When get_schema returns {}

The schema file for the class is not in the local `data/schemas/` collection. This happens when:

- `data/schemas/` was never populated (run `aci-collect run --from schemas`)
- The class was added in a newer APIC version than the one used to collect schemas
- The class name is wrong (use `search_classes` to verify)
