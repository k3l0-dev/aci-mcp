# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Monorepo structure

This repo contains two independent projects:

| Folder | Role |
| --- | --- |
| `mcp/` | FastMCP server — the MCP server consumed by LLM clients |
| `schema-collector/` | Standalone scripts to fetch APIC schemas and generate `class-descriptions.json` |

---

## mcp/

FastMCP server exposing three tools (`search_classes`, `get_schema`, `query`) so an LLM can navigate the Cisco ACI APIC object model without any hardcoded class knowledge.

### Commands

```bash
cd mcp/

# Install dependencies
uv sync

# Run the server (reads .env)
python main.py

# Run all tests
pytest

# Run a single test file
pytest tests/unit/test_filter.py

# Docker (build context is mcp/)
docker build -f deploy/Dockerfile .
docker run --env-file .env -p 8000:8000 aci-mcp
```

### Environment

Copy `mcp/.env.example` to `mcp/.env`. Set `APIC_HOST`, `APIC_USER`, `APIC_PASSWORD`. The server authenticates via cookie token on startup and auto-reauthenticates on 401/403.

### Architecture

```text
main.py                     FastMCP server, lifespan, three tool definitions
apic/
  client.py                 ApicClient — live APIC, cookie auth, httpx
registry/
  descriptions.py           Load + keyword-search class-descriptions.json
  schema.py                 Lazy jsonmeta loader — extracts query-planning fields only
  filter.py                 Build APIC query-target-filter strings from dicts
data/
  class-descriptions.json   label+comment index for all known ACI classes
  schemas/                  15 k+ jsonmeta files from /doc/jsonmeta/ (gitignored)
client/
  aci-mcp.json              MCP client config pointing to http://localhost:8002/mcp
  SKILL.md                  Full ACI object model + tool usage guide for LLM skill files
deploy/
  Dockerfile                Container image
tests/
  conftest.py               Shared fixtures (sample_imdata, schemas_dir)
  unit/                     Pure-logic tests for filter, schema, search
  integration/              Tool tests using an in-memory StubBackend
```

### Key design decisions

**Lazy schema loading.** `registry/schema.py` reads individual jsonmeta files on demand (no upfront scan of 15 k+ files). It extracts only the fields needed for query planning and discards heavy fields (`writeAccess`, `events`, `stats`, `faults`).

**Class validation before hitting the backend.** `query()` checks `class_name` against the in-memory `descriptions` dict before forwarding to `ApicClient`. Unknown classes return a structured error with closest matches — the APIC would silently return `[]` otherwise.

### Tool workflow (mandatory order)

1. `search_classes(keyword)` — find the exact ACI class name
2. `get_schema(class_name)` — inspect `identifiedBy`, `containedBy`, `properties`
3. `query(class_name, filters, scope_dn)` — execute against the backend

Skipping steps 1 or 2 silently returns empty results because APIC does not error on unknown attributes or wrong class names.

---

## schema-collector/

Standalone scripts to pull schemas from a live APIC and generate the data files consumed by `mcp/`.

```text
fetch_schemas.py    Download jsonmeta files from /doc/jsonmeta/ endpoint
gen_descriptions.py Generate mcp/data/class-descriptions.json from the schemas
gen_classes.py      Generate classes.yaml
classes.yaml        Curated class list
```

Outputs copied to `mcp/data/` after a collection run. Heavy artifacts (`mo-schemas/`, `cobra-sdk/`) are gitignored.
