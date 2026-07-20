# Tool: get_by_dn

Fetch a single ACI object **directly by its Distinguished Name**. This is the shortcut path — it skips the `search_classes → get_schema → query` discovery sequence when you already hold an exact DN.

---

## Signature

```python
get_by_dn(
    dn: str,
    config_only: bool = False,
    include_children: list[str] | None = None,
) -> dict[str, Any]
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `dn` | `str` | — | Full Distinguished Name, e.g. `"uni/tn-OT/BD-servers"` |
| `config_only` | `bool` | `False` | Return only user-configurable attributes (`rsp-prop-include=config-only`) — ideal for backup and comparison |
| `include_children` | `list[str]` | — | Child class names to embed. The returned object gains a `_children` list |

---

## When to use it

Use `get_by_dn` whenever you **already have an exact DN** — from a previous tool result, or from a design you are verifying. The DN encodes the class, so you do not need to know the class name up front, and no `search_classes` / `get_schema` round-trip is required.

For an **unknown** object, use the discovery workflow instead: `search_classes → get_schema → query`.

Issues `GET /api/mo/{dn}.json` against the APIC.

---

## Return value

### Found

The object's attribute dict — the **same element shape as a `query()` result** (a single object, not a list):

```json
{
  "_class": "fvBD",
  "dn": "uni/tn-OT/BD-servers",
  "name": "servers",
  "arpFlood": "no",
  "unicastRoute": "yes"
}
```

With `include_children` set, the object also carries a `_children` list:

```json
{
  "_class": "fvBD",
  "dn": "uni/tn-OT/BD-servers",
  "name": "servers",
  "_children": [
    {"_class": "fvSubnet", "dn": "uni/tn-OT/BD-servers/subnet-[10.0.1.1/24]", "ip": "10.0.1.1/24"},
    {"_class": "fvRsCtx",  "dn": "uni/tn-OT/BD-servers/rsctx", "tnFvCtxName": "ot.main.vrf"}
  ]
}
```

### Not found

The APIC returns an empty result for a missing DN. `get_by_dn` turns that into an explicit, structured message rather than a bare `[]`:

```json
{
  "found": false,
  "dn": "uni/tn-OT/BD-typo",
  "message": "No object exists at DN '...'. The DN may be mistyped, or the object may have been deleted. Verify it with search_classes() and query(), or re-derive the DN from a fresh query result."
}
```

A not-found usually means a stale or mistyped DN — re-derive it from a fresh `query` result rather than reconstructing it from memory.

---

## Examples

```python
# Direct read
bd = await get_by_dn("uni/tn-OT/BD-servers")

# Config-only, with children embedded
bd = await get_by_dn(
    "uni/tn-OT/BD-servers",
    config_only=True,
    include_children=["fvSubnet", "fvRsCtx"],
)

# Handle the not-found case
obj = await get_by_dn(dn)
if obj.get("found") is False:
    # DN is stale — re-derive it via query
    ...
```

---

## Related

- [`query`](query.md) — class-scoped queries when you do not have a DN
- [`get_schema`](get_schema.md) — inspect a class before building a query
