# Tool: get_schema

Inspect the structural schema of an ACI class — identifiers, containment, relations, and available properties. **Always call this before `query()`** to know which attributes exist.

The schema comes from the SQLite catalogue embedded in the installed `niwaki`
package, read per class on demand. No APIC round trip, no data directory, no
network access.

---

## Signature

```python
get_schema(
    class_name: str,
    include_property_details: bool = False,
    properties_filter: list[str] | None = None,
) -> dict[str, Any]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `class_name` | `str` | — | Exact ACI class name from `search_classes()`. **Case-sensitive** — see [Case sensitivity](#case-sensitivity). |
| `include_property_details` | `bool` | `False` | Include `property_details` for **every** property. Prefer `properties_filter` unless you truly need all. |
| `properties_filter` | `list[str]` | — | Include `property_details` only for these property names. The token-efficient path — unknown names are silently skipped, and the caller's order is preserved. |

---

## Return value

Ten fields are present on **every** one of the 15,452 classes, empty or not — so
`schema["label"]` never raises a `KeyError`. The remaining four appear only when
the class actually has something to report:

| Field | Type | Always present | Description |
|---|---|---|---|
| `identifiedBy` | `list[str]` | yes | Attributes that uniquely identify an instance — use these as `filters` keys in `query()`. Empty for classes with a fixed RN (`fvRsCtx` → `rsctx`). |
| `rnFormat` | `str` | yes | Relative-name template, e.g. `"BD-{name}"` |
| `containedBy` | `list[str]` | yes | Parent class names in `pkg:Class` notation — use a parent object's `dn` as `scope_dn` |
| `dnFormats` | `list[str]` | yes | Complete DN templates — see [Reading dnFormats](#reading-dnformats) |
| `properties` | `list[str]` | yes | Sorted list of all attribute names available on the class |
| `isAbstract` | `bool` | yes | `true` when the class cannot be directly instantiated |
| `isConfigurable` | `bool` | yes | `true` when objects can be created/modified via APIC |
| `className` | `str` | yes | Short name without package prefix, e.g. `"BD"` |
| `classPkg` | `str` | yes | Package prefix, e.g. `"fv"` |
| `label` | `str` | yes | Human-readable label |
| `contains` | `list[str]` | no — 6,167 of 15,452 classes | Sorted child class names this object may hold, in **flat** notation (e.g. `"fvSubnet"`, `"tagTag"`) — ready to pass to `get_schema`, `query`, or `include_children` |
| `relationTo` | `dict` | no — 1,017 classes | Outgoing Rs relations: `{relClass: {targetClass, cardinality}}`. Keys and `targetClass` keep `pkg:Class` colon notation (unlike `contains`); `cardinality` is always empty — see [Reading relationTo](#reading-relationto) |
| `relationFrom` | `dict` | no — 994 classes | Incoming Rt relations: `{relClass: {sourceClass}}`, also in colon notation |
| `property_details` | `dict` | no — on request | Compact per-property constraints. **Present only** when `include_property_details=True` or `properties_filter` is set. |

Returns `{}` when the class is not in the catalogue — see
[When get_schema returns {}](#when-get_schema-returns-).

---

## Property details

`properties` gives only names. To learn a property's **type, allowed values, default, and write access** before setting or filtering on it, request details — for token economy, ask only for the properties you care about. A class carries 21 properties on average and up to 68, each with a type, a default, an enumeration and a comment attached:

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
    "options": ["no", "yes"],
    "comment": "Indicates if the subnet is preferred (primary) over the available alternatives. Only one preferred subnet is allowed."
  }
}
```

Each entry carries only the fields the schema declares (`type` and `access` are always present):

| Field | Meaning |
|---|---|
| `type` | ACI model type, e.g. `scalar:Bool`, `fv:RouteScp`, `naming:Name` |
| `access` | `read-write` · `create-only` (immutable after create, and the mode of every naming property) · `read-only` (never settable) |
| `naming` | present when the property is part of the DN (an identifier) |
| `mandatory` | present when the property is required on create |
| `default` | the default value, when declared |
| `options` | allowed values — the exact strings the APIC accepts in `filters` and config |
| `comment` | one-line description |

Use `include_property_details=True` to dump every property at once — only when you genuinely need the full picture.

### Register properties carry no `options`

Four internal ACI model types are *registers*: their enumeration is the entire
class or property namespace rather than a short list of settable values.
`mo:MoClassId`, `mo:StatsClassId`, `mo:StatsPropId` and `mo:PropId` — 274
properties across the catalogue — are returned without an `options` key.

```json
"oCl": {
  "type": "mo:MoClassId",
  "access": "read-only",
  "default": "unspecified",
  "comment": "An internally used property that indicates on which MO class the tag is defined."
}
```

One such register listed 17,653 values — the class namespace itself. Reproducing
that in a schema response spends a large block of an agent's context on an
enumeration of the whole model rather than on what the property accepts in this
position. Nothing else about these properties changed: `type`, `access`,
`default` and `comment` are still reported, and `options` was never read by
`query()` or `count()`. Other `mo:*` types (`mo:Owner`,
`mo:ModificationStatus`, …) keep their short option lists.

---

## Example output

`fvBD`, abridged — the real `contains` holds 315 entries, `relationTo` 12, and
`properties` 45:

```json
{
  "identifiedBy": ["name"],
  "rnFormat": "BD-{name}",
  "containedBy": ["fv:Tenant"],
  "contains": ["dhcpLbl", "fvRsBDToOut", "fvRsCtx", "fvSubnet", "tagTag"],
  "dnFormats": ["uni/tn-{name}/BD-{name}"],
  "relationTo": {
    "fv:RsCtx": {
      "targetClass": "fv:Ctx",
      "cardinality": ""
    },
    "fv:RsBDToOut": {
      "targetClass": "l3ext:Out",
      "cardinality": ""
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
scope = tenants["results"][0]["dn"]  # "uni/tn-OT"

# 2. Use it as scope_dn
bds = await query("fvBD", scope_dn=scope)
```

---

## Reading dnFormats

```json
"dnFormats": ["uni/tn-{name}/BD-{name}"]
```

The full path template, built by chaining every ancestor's `rnFormat`. The key
is present on all 15,452 classes; it is `[]` for the classes that genuinely
have no template, and it is never truncated — `faultInst` returns all 24,151 of
its templates and `faultDelegate` all 64,313.

Each `{...}` placeholder is named after the *schema's own* identifying
attribute, not a human-friendly label. Two placeholders legitimately read the
same name when two ancestors share an identifying attribute — a tenant and a
bridge domain are both identified by `name`, hence `{name}` twice above. That
repetition is expected, not a copy error.

**Quote `dnFormats` verbatim.** When stating a DN pattern in an answer, copy the
template exactly (or `rnFormat` for just the last component), substituting only
the literal values. Never rename a placeholder to something more descriptive,
and never reconstruct or paraphrase a DN template from memory — the template is
the anti-hallucination anchor, and it stops being one the moment it is rewritten.

---

## Reading relationTo

```json
"relationTo": {
  "fv:RsCtx": {
    "targetClass": "fv:Ctx",
    "cardinality": ""
  }
}
```

Two things to note before using this, because both differ from `contains`:

- **The keys and `targetClass` keep their `pkg:Class` colon notation.** Unlike
  `contains`, which is flattened for you, these are not. Drop the colon to get
  the queryable class name: `fv:RsCtx` → `fvRsCtx`, `fv:Ctx` → `fvCtx`.
- **`cardinality` is always empty.** It is `""` for all 2,992 `relationTo`
  entries in the catalogue, so do not branch on it and do not report it. The
  key is kept for shape stability only; `get_schema()` on the relation class
  itself does not expose it either.

`fvBD` has an outgoing relation to `fvCtx` (VRF) via the relation class `fvRsCtx`. To find which VRF a BD is associated with:

```python
# include the relation object as a child
result = await query("fvBD", include_children=["fvRsCtx"])
for bd in result["results"]:
    for child in bd.get("_children", []):
        if child["_class"] == "fvRsCtx":
            print(bd["name"], "→", child["tnFvCtxName"])
```

A populated `tnFvCtxName` records what was *configured*, not what resolves —
read the relation object's `state` before reporting a target as real.

---

## Case sensitivity

Class lookup is exact and case-sensitive. `fvBd` is not `fvBD`: `get_schema`
returns `{}` for it, and `query` / `count` reject it as an unknown class. This is
structural rather than a guard in the code — the catalogue is SQLite with its
default BINARY collation, so a case-folded match is not something the lookup can
accidentally do.

Take the class name from a `search_classes()` result rather than retyping it.

---

## When get_schema returns {}

An empty dict means the exact string you passed is not a class name in the
catalogue. It is not a transient failure and a retry will not change it. The
causes, in order of likelihood:

- **The name is misspelled, or the case is wrong.** `fvBd`, `fvbd` and `FvBD`
  are all unknown. Re-run `search_classes()` and copy the `class_name` field.
- **The name is a `pkg:Class` string that was not flattened.** `relationTo`
  keys and `containedBy` entries keep their colon: pass `fvCtx`, not `fv:Ctx`.
- **The class is newer than the catalogue.** The catalogue ships inside the
  `niwaki` dependency and is built from APIC **6.0(9c)**; a class introduced in
  a later APIC release is not in it. That release is logged at startup — check
  it against your fabric's own version before assuming a typo.

A class being absent from `search_classes()` results is *not* one of the causes:
213 catalogue classes are unindexed and still resolve here. See
[`search_classes`](search_classes.md#what-the-index-covers).

A missing class never raises. An exception here means the catalogue itself
could not be opened, which is a broken installation rather than a bad argument.

---

## Raises

| Exception | Condition |
|---|---|
| `DescriptionsLoadError` | The niwaki catalogue is missing or unreadable — the file is absent from the installed package, or its manifest no longer declares the property-flag layout this server reads. A broken installation, not a missing class (a missing class returns `{}` instead, see above). Fix with `pip install --force-reinstall niwaki`. |

`SchemaLoadError` was the documented exception until 2.0. It meant "a jsonmeta
file exists on disk but is malformed" — a condition that can no longer arise,
since there are no jsonmeta files. Nothing raises it any more.
