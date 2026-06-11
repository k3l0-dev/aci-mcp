# aci-mcp

[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP](https://img.shields.io/badge/FastMCP-3.1+-00C896?logo=python&logoColor=white)](https://github.com/jlowin/fastmcp)
[![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9?logo=astral&logoColor=white)](https://github.com/astral-sh/uv)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](mcp/deploy/Dockerfile)
[![MCP](https://img.shields.io/badge/protocol-MCP-orange)](https://modelcontextprotocol.io)
[![Cisco ACI](https://img.shields.io/badge/Cisco-ACI%20APIC-1BA0D7?logo=cisco&logoColor=white)](https://developer.cisco.com/docs/aci/)

> Schema-driven MCP server for Cisco ACI — lets any LLM navigate the full ACI object model through three generic tools, with no hardcoded class knowledge.

---

## Monorepo

| Project | Description |
| --- | --- |
| [`mcp/`](mcp/) | FastMCP server — three tools for discovering, inspecting, and querying ACI objects |
| [`schema-collector/`](schema-collector/) | Standalone scripts to pull jsonmeta schemas from an APIC and generate the data files used by the server |

---

## mcp — ACI MCP Server

### How it works

The server exposes three tools that an LLM must call in order:

```
1. search_classes("bridge domain")   →  fvBD
2. get_schema("fvBD")                →  identifiedBy, containedBy, properties, relations
3. query("fvBD", scope_dn="uni/tn-OT", filters={"name": "servers"})  →  objects
```

All ACI domain knowledge (15 k+ jsonmeta class schemas, label + description index) lives in `mcp/data/` — not in code.

### Quick start

```bash
cd mcp
cp .env.example .env        # fill in APIC_HOST, APIC_USER, APIC_PASSWORD
uv sync
python main.py              # starts on port 8000
```

### Connect an MCP client

Point your client at `http://localhost:8000/mcp`. A ready-made config is in [`mcp/client/aci-mcp.json`](mcp/client/aci-mcp.json).

### Docker

```bash
# Build (context must be mcp/)
docker build -f mcp/deploy/Dockerfile mcp/ -t aci-mcp

# Run
docker run --env-file mcp/.env -p 8000:8000 aci-mcp
```

### Tools reference

| Tool | Description |
| --- | --- |
| `search_classes(keyword)` | Case-insensitive keyword search across class name, label, and description. Returns ranked matches. |
| `get_schema(class_name)` | Returns `identifiedBy`, `rnFormat`, `containedBy`, `dnFormats`, `properties`, `relationTo`, `relationFrom` for a class. |
| `query(class_name, ...)` | Queries objects from the APIC. Supports `filters`, `scope_dn`, `filter_expr`, `include_children`, `order_by`, `page`, `time_range`, `rsp_subtree_include`. |

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `APIC_HOST` | — | APIC hostname or IP |
| `APIC_USER` | `admin` | APIC username |
| `APIC_PASSWORD` | — | APIC password |
| `APIC_VERIFY_SSL` | `false` | Set `true` to enforce TLS verification |
| `MCP_PORT` | `8000` | HTTP port |

---

## schema-collector — APIC Schema Fetcher

Standalone tooling to pull the full jsonmeta schema collection from a live APIC and generate the data files consumed by the MCP server.

```bash
cd schema-collector
cp .env.example .env        # fill in APIC credentials
uv sync

python fetch_schemas.py     # download jsonmeta files → mo-schemas/
python gen_descriptions.py  # generate class-descriptions.json
# copy outputs to mcp/data/
```

---

## Repository layout

```
aci-mcp/
├── mcp/                        MCP server
│   ├── main.py
│   ├── apic/client.py          APIC REST client (httpx, cookie auth)
│   ├── registry/               Schema loading, search, filter building
│   ├── data/
│   │   ├── class-descriptions.json
│   │   └── schemas/            (gitignored — 15 k+ jsonmeta files)
│   ├── client/                 MCP client config + LLM skill doc
│   ├── deploy/Dockerfile
│   └── tests/
├── schema-collector/           APIC schema collection scripts
├── CLAUDE.md
└── .gitignore
```
