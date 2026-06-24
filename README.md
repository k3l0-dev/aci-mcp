# aci-mcp

[![Version](https://img.shields.io/badge/version-0.3.0-blue)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP](https://img.shields.io/badge/FastMCP-3.1+-00C896)](https://github.com/jlowin/fastmcp)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](mcp/deploy/Dockerfile)
[![MCP](https://img.shields.io/badge/protocol-MCP-orange)](https://modelcontextprotocol.io)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue)](LICENSE)
[![Commercial License](https://img.shields.io/badge/license-Commercial-orange)](LICENSE-COMMERCIAL.md)

**Schema-driven MCP server for Cisco ACI.** Lets any LLM navigate the full APIC object model through three generic tools — no hardcoded class knowledge, no prompt engineering per object type.

---

## Overview

The APIC exposes 15 000+ managed object classes. Querying them requires knowing exact class names, DN structures, and filter syntax — knowledge that changes across APIC versions and is impractical to embed in a prompt.

`aci-mcp` solves this by shipping the object model itself as a queryable index. The LLM discovers classes at runtime using `search_classes`, inspects their schema with `get_schema`, and executes typed queries with `query`. It learns what it needs on-demand instead of relying on baked-in knowledge.

```text
LLM                          aci-mcp                        APIC
 │                              │                              │
 │── search_classes("vrf") ────>│  scan class-descriptions     │
 │<─ [{fvCtx, "VRF", ...}] ────│                              │
 │                              │                              │
 │── get_schema("fvCtx") ──────>│  read jsonmeta file          │
 │<─ {identifiedBy, props...} ──│                              │
 │                              │                              │
 │── query("fvCtx", ...) ──────>│─── GET /api/class/fvCtx ───>│
 │<─ [{dn, name, pcEnfPref}] ───│<─── 200 [{imdata: [...]}] ──│
```

---

## Repository layout

```text
aci-mcp/
├── mcp/                        MCP server (FastMCP, Python 3.12)
│   ├── main.py                 Server entry point — tools, lifespan, middleware
│   ├── apic/
│   │   └── client.py           APIC REST client (httpx, cookie auth, auto-reauth)
│   ├── registry/
│   │   ├── descriptions.py     Class index loader + weighted keyword search
│   │   ├── schema.py           Lazy jsonmeta loader (on-demand, per class)
│   │   └── filter.py           APIC query-target-filter builder
│   ├── middleware/
│   │   ├── auth.py             Bearer token auth, KeyStore hot-reload, rate limiter
│   │   └── oauth.py            RFC 9728 OAuth Protected Resource Metadata
│   ├── client/
│   │   ├── aci-mcp.json        Ready-made MCP client config
│   │   └── SKILL.md            LLM skill doc — ACI object model + tool usage guide
│   ├── deploy/
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml  Production stack (MCP + Caddy TLS)
│   │   └── Caddyfile
│   └── tests/
│       ├── unit/               Pure-logic tests (filter, search, schema)
│       └── integration/        Tool tests using an in-memory StubBackend
├── schema-collector/           Pipeline to fetch schemas from a live APIC
├── data/
│   ├── class-descriptions.json Committed — label + keyword index (15 k+ classes)
│   └── schemas/                Gitignored — raw jsonmeta files per APIC version
├── docs/                       Architecture, deployment, tools, internals
└── scripts/
    └── list-configurable-classes.sh  Query configurable classes from jsonmeta
```

---

## Tools

The server exposes three tools. The LLM must call them in order — skipping steps silently returns empty results.

### `search_classes(keyword)`

Case-insensitive weighted search across class name (×3), label (×2), comment (×1), and property labels (×1 fallback). Returns up to 10 ranked matches.

Relation classes (`Rs`/`Rt`) receive a −3 penalty so canonical objects always rank above internal plumbing.

```python
search_classes("bridge domain")
# → [{"class_name": "fvBD", "label": "Bridge Domain", "comment": "..."}]
```

### `get_schema(class_name)`

Reads the APIC jsonmeta file for a class and returns the fields needed for query planning:

| Field | Description |
|---|---|
| `identifiedBy` | Attributes that form the RN (used in DN paths) |
| `rnFormat` | RN template, e.g. `BD-{name}` |
| `containedBy` | Parent classes |
| `dnFormats` | Valid DN patterns |
| `properties` | Configurable attributes with type + allowed values |
| `relationTo` / `relationFrom` | Rs/Rt wiring |

```python
get_schema("fvBD")
# → {identifiedBy: ["name"], containedBy: ["fv:Tenant"], ...}
```

### `query(class_name, ...)`

Executes a class query against the APIC with full filter support.

| Parameter | Description |
|---|---|
| `class_name` | Validated against the local index before hitting the APIC |
| `filters` | Dict of attribute → value equality filters |
| `filter_expr` | Raw APIC filter string for complex expressions |
| `scope_dn` | Scope the query under a specific DN subtree |
| `include_children` | Inline children of each result object |
| `order_by` | Sort expression, e.g. `faultInst.severity\|desc` |
| `page` / `page_size` | Pagination |
| `time_range` | Time-scoped stats queries |
| `rsp_subtree_include` | APIC subtree modifier (faults, health, …) |

```python
query("fvBD", scope_dn="uni/tn-Production", filters={"name": "servers"})
# → [{"_class": "fvBD", "dn": "uni/tn-Production/BD-servers", "unicastRoute": "yes", ...}]
```

---

## Quick start

### Local

```bash
# 1. Create .env at repo root
cat > .env <<EOF
APIC_HOST=https://sandboxapicdc.cisco.com
APIC_USER=admin
APIC_PASSWORD=your_password
MCP_PORT=8000
EOF

# 2. Install and run
cd mcp
uv sync
python main.py
```

### Docker

```bash
# Build
docker build -f mcp/deploy/Dockerfile mcp/ -t aci-mcp

# Run
docker run --env-file .env -p 8000:8000 aci-mcp
```

### Production (MCP + Caddy TLS)

```bash
docker compose -f mcp/deploy/docker-compose.yml up -d
```

Caddy handles TLS termination. The MCP port is never exposed directly — all traffic enters via Caddy on 443.

---

## Connect a client

Point your MCP client at `http://localhost:8000/mcp`.

A ready-made config is at [`mcp/client/aci-mcp.json`](mcp/client/aci-mcp.json). Copy it into your client's MCP server list.

If `MCP_API_KEYS` is set, pass the token as a `Authorization: Bearer <token>` header.

---

## Environment variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `APIC_HOST` | — | ✓ | APIC hostname or URL |
| `APIC_USER` | `admin` | | APIC username |
| `APIC_PASSWORD` | — | ✓ | APIC password |
| `APIC_VERIFY_SSL` | `false` | | Set `true` to enforce TLS verification |
| `MCP_PORT` | `8000` | | HTTP port |
| `MCP_API_KEYS` | — | | Comma-separated bearer tokens. If unset, auth is disabled (dev only) |

Sending `SIGHUP` to the server process reloads `MCP_API_KEYS` from `.env` without a restart.

---

## Schema collector

The `schema-collector/` project fetches the jsonmeta schema files from a live APIC and rebuilds `data/class-descriptions.json`. Run it when upgrading to a new APIC version.

```bash
cd schema-collector
uv sync
uv run aci-collect run              # full pipeline
uv run aci-collect run --from descriptions  # rebuild index only
uv run aci-collect status           # check artifact state
```

Pipeline steps:

| Step | Input | Output |
|---|---|---|
| `cobra` | APIC `/cobra/_downloads` | `cobra-sdk/*.whl` |
| `classes` | cobra wheel | `classes.yaml` |
| `schemas` | `classes.yaml` | `mo-schemas/{version}/*.json` |
| `descriptions` | `mo-schemas/` | `data/class-descriptions.json` |

### Standalone binary

The collector can be compiled to a self-contained Linux x86_64 binary (no Python on the target):

```bash
cd schema-collector
make build   # requires OrbStack on macOS (Rosetta 2), or Linux root
```

Output: `schema-collector/dist/<version>/aci-collect`

---

## Development

### Run tests

```bash
cd mcp
uv sync --extra dev
pytest                          # unit tests only
pytest tests/ -v                # all tests
pytest tests/integration/ -v    # integration (requires running server + APIC)
```

### Search algorithm

The keyword search uses weighted substring matching with two scoring adjustments:

- **Rs/Rt penalty (−3):** Relation classes inherit their target's label, causing them to outscore canonical objects on label-only queries. The penalty keeps them below canonical classes while still surfacing them when the name match is strong.
- **prop_labels fallback:** When no name/label/comment match is found, property labels (e.g. "ARP Flooding", "Unicast Routing") are scanned as a catch-all at score +1.

Measured on the 39-query golden set (`mcp/tests/fixtures/search_golden.json`):

| Strategy | R@1 | R@5 | MRR |
|---|---|---|---|
| Baseline (substring) | 15.4% | 35.9% | 0.229 |
| + Rs/Rt penalty | 28.2% | 41.0% | 0.338 |
| + prop_labels | 30.8% | 53.8% | 0.400 |

See [`docs/internals/search-algorithm.md`](docs/internals/search-algorithm.md) for full analysis.

---

## Security

- API key authentication via `Authorization: Bearer` or `X-API-Key`
- Per-IP rate limiting on failed auth attempts (default: 30 attempts / 60 s, returns 429)
- `WWW-Authenticate` header with RFC 9728 discovery URL on 401
- TLS termination via Caddy in the production stack — MCP port never exposed
- Hot-reload of API keys via SIGHUP — no downtime key rotation

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

---

## License

[AGPL v3](LICENSE) — © 2026 Khalid El-Ouiali, MONARK AIOPS srl.

Free to use and modify under AGPL v3 terms. A [commercial license](LICENSE-COMMERCIAL.md) is available for proprietary integrations and deployments that cannot comply with AGPL copyleft. Contact: [monark.aiops@pm.me](mailto:monark.aiops@pm.me)
