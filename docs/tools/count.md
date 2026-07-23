# Tool: count

Count objects of an ACI class **without transferring them**. Answers "how many BDs / EPGs / subnets?" in a single cheap request instead of fetching everything just to measure the set.

**Verify the class name with `search_classes` first** — `count` raises the same `UnknownClassError` (with suggestions) as `query`.

---

## Signature

```python
count(
    class_name: str,
    filters: dict[str, str] | None = None,
    scope_dn: str | None = None,
    filter_expr: str | None = None,
) -> dict[str, Any]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `class_name` | `str` | — | Exact ACI class name — **must** be verified with `search_classes()` first |
| `filters` | `dict[str, str]` | `{}` | Attribute equality filters `{attr: value}`, same semantics as `query()` |
| `scope_dn` | `str` | — | DN of a parent object to scope the count to a subtree |
| `filter_expr` | `str` | — | Raw APIC filter for predicates beyond equality; combined with `filters` via `and()` |

---

## How it works

Uses the APIC `rsp-subtree-include=count` mechanism: the response carries a single `moCount` managed object whose attribute holds the tally. No object attributes are transferred, so a count is far cheaper than a full `query` on a large result set. Filtering and scoping behave exactly as in `query`.

---

## Return value

```json
{
  "class_name": "fvSubnet",
  "count": 12,
  "scope_dn": null,
  "filters": {"scope": "public"}
}
```

| Field | Type | Description |
|---|---|---|
| `class_name` | `str` | The class that was counted |
| `count` | `int` | Number of matching objects |
| `scope_dn` | `str \| null` | The scope DN, if one was supplied |
| `filters` | `dict` | The filters that were applied |

---

## Raises

| Exception | Condition |
|---|---|
| `UnknownClassError` | `class_name` is not in the registry. Includes `.suggestions` (list) and `.registry_size` (int). |
| `ApicRequestError` | APIC returned a non-2xx, non-auth response — e.g. 400 for a malformed `filter_expr`, or a transient 5xx that never recovered. Carries the HTTP status and, when present, the APIC error text. |

---

## Examples

```python
# All BDs in the fabric
await count("fvBD")

# EPGs in a specific tenant
await count("fvAEPg", scope_dn="uni/tn-OT")

# Public subnets only
await count("fvSubnet", filters={"scope": "public"})

# Non-controller fabric nodes
await count("fabricNode", filter_expr='ne(fabricNode.role,"controller")')
```

---

## Eventual consistency

A count taken right after a large config push reflects the fabric state **at that instant**. Counts can keep moving for a few seconds while the fabric materialises the change (BDs, EPGs, and subnets appear incrementally). If a count right after a write does not match what you expect, wait for stabilisation and re-read before drawing a conclusion — do not treat the first post-write read as final.

---

## Related

- [`query`](query.md) — fetch the objects themselves once you know how many there are
- [`get_by_dn`](get_by_dn.md) — fetch a single object directly by DN
