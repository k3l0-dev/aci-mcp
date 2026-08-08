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

Issues the same class or subtree request as `query`, with a page size of 1, and reads the APIC-reported `totalCount` — the true size of the matching set, independent of how many objects were fetched. Exactly one object comes back instead of the whole set, so a count stays far cheaper than a full `query` on a large result set. Filtering and scoping behave exactly as in `query`.

Because the tally is the very same `total_available` that `query` reports, the two tools can never disagree about the size of the same result set.

> **Why not `rsp-subtree-include=count`?** That is the obvious idiom for a count, and it is what this tool used until v1.2.1. Its `moCount` tally was measured against reality on APIC 6.0(9c) and found to disagree — silently, and depending on the data: fabric-wide it reported 203 `fvBD` against a real 403, and for 5 of the 28 tenants holding bridge domains it reported a scoped count of **0** against subtrees really holding 1 to 192. The behaviour is deterministic rather than flaky, so a retry does not help. See [`CHANGELOG.md`](../../CHANGELOG.md) and the `count_class()` docstring for the full measurements.

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
| `UnknownClassError` | `class_name` is not in the catalogue (15,452 classes). Includes `.suggestions` (list, drawn from the search index) and `.registry_size` (int — the 15,239-entry search index). The guard is identical to `query`'s, so the two tools can never disagree about whether a class is known. |
| `FilterError` | `class_name` or a `filters` key contains characters outside the expected ACI identifier format. Filter values are always escaped, never rejected. |
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
