# aci-mcp — Documentation Wiki

> Schema-driven MCP server for Cisco ACI — lets any LLM navigate the full ACI object model through three generic tools, with no hardcoded class knowledge.

---

## Contents

### Architecture

| Page | Description |
|---|---|
| [System overview](architecture/overview.md) | High-level components, monorepo layout, deployment topology |
| [Data flow](architecture/data-flow.md) | Request lifecycle, APIC query path, schema loading sequence |
| [Exception hierarchy](architecture/exceptions.md) | Typed exception tree with usage context |

### Configuration

| Page | Description |
|---|---|
| [Settings reference](configuration/settings.md) | All environment variables, defaults, validation rules |

### Deployment

| Page | Description |
|---|---|
| [Quickstart](deployment/quickstart.md) | Local development — up in 5 minutes |
| [Docker](deployment/docker.md) | Single-container deployment with the Dockerfile |
| [HTTPS with Caddy](deployment/https.md) | Production stack — TLS termination, Let's Encrypt, LAN certs |

### Tools reference

| Page | Description |
|---|---|
| [`search_classes`](tools/search_classes.md) | Keyword search over the ACI class registry |
| [`get_schema`](tools/get_schema.md) | Class schema inspection — identifiers, containment, relations |
| [`query`](tools/query.md) | Full APIC query — filters, scope, pagination, children |

### Internals

| Page | Description |
|---|---|
| [APIC client](internals/apic-client.md) | Cookie auth, re-auth, query URL construction |
| [Registry](internals/registry.md) | Descriptions index, lazy schema loader, filter builder |
| [Auth middleware](internals/auth.md) | API key validation, timing-safe comparison, no-op mode |

---

## Quick orientation

```
LLM client
    │  MCP protocol (JSON-RPC over HTTP)
    ▼
Caddy (TLS termination)          ← port 443
    │  HTTP plain
    ▼
aci-mcp FastMCP server           ← port 8000 (internal)
    │  three tools
    ├── search_classes  → in-memory descriptions index
    ├── get_schema      → jsonmeta files on disk (lazy)
    └── query           → APIC REST API (https)
                              │
                              ▼
                        Cisco APIC
```

The **mandatory tool order** is always: `search_classes` → `get_schema` → `query`.
Skipping steps produces empty results with no error.
See [data flow](architecture/data-flow.md) for the full sequence diagram.

---

## Versions

| Version | Notes |
|---|---|
| `0.2.0` | API key auth + HTTPS via Caddy |
| `0.1.0` | Initial release — three tools, custom exceptions, full test suite |

Full history: [CHANGELOG.md](../CHANGELOG.md)
