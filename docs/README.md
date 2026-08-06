# aci-mcp — Documentation

> Schema-driven MCP server for Cisco ACI — a small set of generic tools that let any LLM navigate the full ACI object model without hardcoded class knowledge.

---

## Contents

### Concepts

| Page | Description |
|---|---|
| [ACI object model](concepts/aci-object-model.md) | DN structure, class names, tenant hierarchy — ACI basics for non-network engineers |

### Getting started

| Page | Description |
|---|---|
| [Quickstart](getting-started/quickstart.md) | Local development — up in 5 minutes |
| [Docker](getting-started/docker.md) | Single-container deployment |
| [HTTPS with Caddy](getting-started/https.md) | Production stack — TLS termination, Let's Encrypt, LAN certs |

### Tools reference

| Page | Description |
|---|---|
| [`search_classes`](tools/search_classes.md) | Keyword search over the ACI class registry |
| [`get_schema`](tools/get_schema.md) | Class schema inspection — identifiers, containment, children, relations, per-property constraints |
| [`query`](tools/query.md) | Full APIC query — filters, scope, pagination, children, config-only |
| [`get_by_dn`](tools/get_by_dn.md) | Fetch a single object directly by DN — the known-DN shortcut |
| [`count`](tools/count.md) | Count objects of a class without transferring them |

### Configuration

| Page | Description |
|---|---|
| [Settings reference](configuration/settings.md) | All environment variables, defaults, validation rules |

### Architecture

| Page | Description |
|---|---|
| [System overview](architecture/overview.md) | Components, monorepo layout, startup sequence, key design decisions |
| [Data flow](architecture/data-flow.md) | LLM tool sequence, per-tool internal flows |

### Internals

| Page | Description |
|---|---|
| [Middleware stack](internals/middleware.md) | HealthMiddleware, OAuthDiscoveryMiddleware, ApiKeyMiddleware — stack order, SIGHUP hot-reload |
| [Auth middleware](internals/auth.md) | API key validation, timing-safe comparison, rate limiting |
| [Registry](internals/registry.md) | Descriptions index, lazy schema loader, filter builder |
| [APIC client](internals/apic-client.md) | Cookie auth, re-auth, query URL construction |
| [Exception hierarchy](internals/exceptions.md) | Typed exception tree with usage context |
| [Search algorithm](internals/search-algorithm.md) | Algorithm rationale, Rs/Rt penalty, measured gains |

---

## Quick orientation

```
LLM client
    │  MCP protocol (JSON-RPC over HTTP)
    ▼
Caddy (TLS termination)                  ← port 443
    │  HTTP plain (internal network)
    ▼
HealthMiddleware                         ← /health short-circuit (no auth)
    │
OAuthDiscoveryMiddleware                 ← /.well-known/oauth-protected-resource
    │
ApiKeyMiddleware                         ← Bearer token validation, rate limiting
    │
FastMCP dispatcher                       ← port 8000
    │
    ├── search_classes  → in-memory descriptions index (15 k+ classes)
    ├── get_schema      → jsonmeta files on disk (lazy, per class)
    ├── query           → APIC REST API (HTTPS)
    ├── get_by_dn       → APIC REST API (HTTPS) — direct DN read
    └── count           → APIC REST API (HTTPS) — tally only
                              │
                              ▼
                        Cisco APIC
```

For **discovery**, the mandatory tool order is always: `search_classes` → `get_schema` → `query`.
Skipping steps produces empty results with no error — the APIC silently returns `[]`
for unknown class names or wrong attribute names.

**Shortcut:** when you already hold an exact class name *and* DN (from a previous result or a
design), call `get_by_dn` directly — no discovery detour needed. Use `count` to answer
"how many?" without fetching the objects.

See [data flow](architecture/data-flow.md) for the complete sequence diagrams.

---

## Version

Current release: **v1.2.2**

Full history: [CHANGELOG.md](../CHANGELOG.md)
