# niwashi-mcp — Documentation

> Schema-driven MCP server for Cisco ACI — a small set of generic tools that let any LLM navigate the full ACI object model without hardcoded class knowledge.

The server reads the object model from a SQLite catalogue embedded in the
[`niwaki`](https://pypi.org/project/niwaki/) dependency. There is no data
directory to populate, no schema bundle to download, and no checkout to keep
around: `uvx niwashi-mcp` is the whole installation.

---

## Contents

### Concepts

| Page | Description |
|---|---|
| [ACI object model](concepts/aci-object-model.md) | DN structure, class names, tenant hierarchy — ACI basics for non-network engineers |

### Getting started

| Page | Description |
|---|---|
| [Quickstart](getting-started/quickstart.md) | Install and run — up in 5 minutes |

### Tools reference

| Page | Description |
|---|---|
| [`search_classes`](tools/search_classes.md) | Keyword search over the ACI class index |
| [`get_schema`](tools/get_schema.md) | Class schema inspection — identifiers, containment, children, relations, per-property constraints |
| [`query`](tools/query.md) | Full APIC query — filters, scope, pagination, children, config-only |
| [`get_by_dn`](tools/get_by_dn.md) | Fetch a single object directly by DN — the known-DN shortcut |
| [`count`](tools/count.md) | Count objects of a class without transferring them |

### Configuration

| Page | Description |
|---|---|
| [Settings reference](configuration/settings.md) | All environment variables, defaults, resolution order, validation rules |

### Architecture

| Page | Description |
|---|---|
| [System overview](architecture/overview.md) | Components, repository layout, startup sequence, key design decisions |
| [Data flow](architecture/data-flow.md) | LLM tool sequence, plus internal flows for `search_classes`, `get_schema` and `query` |

### Internals

| Page | Description |
|---|---|
| [Middleware stack](internals/middleware.md) | HealthMiddleware, OAuthDiscoveryMiddleware, ApiKeyMiddleware — stack order, SIGHUP hot-reload |
| [Auth middleware](internals/auth.md) | API key validation, timing-safe comparison, rate limiting |
| [Registry](internals/registry.md) | Catalogue adapter, search index, filter builder |
| [APIC client](internals/apic-client.md) | Cookie auth, re-auth, query URL construction |
| [Exception hierarchy](internals/exceptions.md) | Typed exception tree with usage context |
| [Search algorithm](internals/search-algorithm.md) | Algorithm rationale, Rs/Rt penalty, measured gains |

---

## Quick orientation

```text
LLM client
    │  MCP protocol (JSON-RPC over HTTP)
    ▼
HealthMiddleware                         ← /health short-circuit (no auth)
    │
OAuthDiscoveryMiddleware                 ← /.well-known/oauth-protected-resource
    │
ApiKeyMiddleware                         ← Bearer token validation, rate limiting
    │
FastMCP dispatcher                       ← port 8000
    │
    ├── search_classes  → in-memory index built at startup (15,239 classes)
    ├── get_schema      → niwaki catalogue (SQLite, per class, on demand)
    ├── query           → APIC REST API (HTTPS)
    ├── get_by_dn       → APIC REST API (HTTPS) — direct DN read
    └── count           → APIC REST API (HTTPS) — tally only
                              │
                              ▼
                        Cisco APIC
```

Both local paths read the same file: `catalog.db`, shipped inside the installed
`niwaki` package. The catalogue holds **15,452 classes** for APIC **6.0(9c)**;
**15,239** of them carry enough text (label, comment, or a discriminating
property label) to be indexed and therefore findable by `search_classes`. The
remaining 213 are fully queryable, just not searchable — see
[ACI object model](concepts/aci-object-model.md#two-class-counts-15452-and-15239).

For **discovery**, the mandatory tool order is always: `search_classes` → `get_schema` → `query`.
Skipping steps produces empty results with no error — the APIC silently returns `[]`
for unknown class names or wrong attribute names.

**Shortcut:** when you already hold an exact class name *and* DN (from a previous result or a
design), call `get_by_dn` directly — no discovery detour needed. Use `count` to answer
"how many?" without fetching the objects.

See [data flow](architecture/data-flow.md) for the complete sequence diagrams.

---

## Version

Current release: **v2.0.0** — distribution `niwashi-mcp`, import package
`niwashi_mcp`, console command `niwashi-mcp`.

Full history: [CHANGELOG.md](../CHANGELOG.md)
