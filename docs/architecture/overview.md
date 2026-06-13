# System Overview

## What aci-mcp does

aci-mcp is a [Model Context Protocol](https://modelcontextprotocol.io) server that gives any MCP-compatible LLM client (Claude Desktop, Cursor, custom agents) read access to a Cisco ACI fabric without any hardcoded class knowledge in the model or the server.

The server exposes **three generic tools**. The LLM calls them in sequence to discover, inspect, and query any ACI object class — even classes that did not exist when the model was trained.

---

## Monorepo layout

```mermaid
graph TD
    subgraph repo["aci-mcp (monorepo)"]
        mcp["mcp/<br/>FastMCP server"]
        sc["schema-collector/<br/>Pipeline CLI"]
        data["data/<br/>shared artifacts"]
        env[".env<br/>shared credentials"]
    end

    sc -->|"writes class-descriptions.json"| data
    sc -->|"writes schemas/ (gitignored)"| data
    mcp -->|"reads at startup"| data
    mcp -->|"reads credentials"| env
    sc -->|"reads credentials"| env
```

The two projects are independent Python packages under `uv`. They share `data/` and `.env` at the repo root.

---

## Component architecture

```mermaid
graph TB
    subgraph client["LLM Client (Claude Desktop / Cursor / Agent)"]
        llm["LLM"]
    end

    subgraph prod["Production stack (docker-compose)"]
        caddy["Caddy<br/>TLS termination<br/>port 443 / 80"]
        subgraph mcp_server["aci-mcp container (port 8000, internal)"]
            fm["FastMCP<br/>HTTP server"]
            auth["ApiKeyMiddleware<br/>Bearer / X-API-Key"]
            t1["search_classes"]
            t2["get_schema"]
            t3["query"]
            subgraph registry["Registry (in-memory / on-disk)"]
                desc["descriptions index<br/>15k+ classes"]
                schema["jsonmeta loader<br/>lazy, per-class"]
                filt["filter builder<br/>APIC eq() syntax"]
            end
            apic_client["ApicClient<br/>httpx async, cookie auth"]
        end
    end

    subgraph apic["Cisco APIC"]
        rest["REST API<br/>https"]
    end

    subgraph datastore["data/ (shared)"]
        json["class-descriptions.json"]
        schemas_dir["schemas/*.json<br/>15k+ jsonmeta files"]
    end

    llm -->|"MCP JSON-RPC"| caddy
    caddy -->|"HTTP plain (internal)"| auth
    auth -->|"validated requests"| fm
    fm --> t1
    fm --> t2
    fm --> t3
    t1 --> desc
    t2 --> schema
    t3 --> filt
    t3 --> apic_client
    desc -->|"loaded at startup"| json
    schema -->|"read on demand"| schemas_dir
    apic_client -->|"HTTPS"| rest
```

---

## Request path summary

| Step | Where | What happens |
|---|---|---|
| 1 | LLM client | Sends MCP tool call over JSON-RPC |
| 2 | Caddy | Terminates TLS, proxies to port 8000 |
| 3 | `ApiKeyMiddleware` | Validates `Authorization: Bearer` or `X-API-Key` header |
| 4 | FastMCP dispatcher | Routes to the correct tool function |
| 5 | Tool | Reads registry / calls APIC |
| 6 | `ApicClient` | Builds URL + filter params, sends HTTPS GET to APIC |
| 7 | APIC | Returns `imdata` JSON array |
| 8 | Tool | Flattens objects, adds `_class` key, returns list |
| 9 | FastMCP | Serialises response as MCP JSON-RPC result |

---

## Startup sequence

```mermaid
sequenceDiagram
    participant OS as OS / Docker
    participant server as aci-mcp main.py
    participant registry as Registry
    participant apic as Cisco APIC

    OS->>server: python main.py
    server->>server: load_dotenv(.env)
    server->>server: validate APIC_HOST, APIC_PASSWORD
    server->>registry: load_descriptions(class-descriptions.json)
    registry-->>server: 15k-class dict (in-memory)
    server->>apic: POST /api/aaaLogin.json
    apic-->>server: APIC-cookie token
    server->>server: start HTTP server on MCP_PORT
    Note over server: Ready — lifespan context shared with all tools
```

---

## Key design decisions

### Lazy schema loading

`registry/schema.py` reads individual jsonmeta files on demand — there is no upfront scan of 15 k+ files at startup. The first `get_schema("fvBD")` call reads `fvBD.json`; subsequent calls for the same class hit the file again (no in-memory cache needed — OS page cache is sufficient for the usage pattern).

Heavy jsonmeta fields (`writeAccess`, `events`, `stats`, `faults`) are discarded at load time to keep tool responses token-efficient.

### Class validation before APIC

`query()` checks `class_name` against the in-memory `descriptions` dict **before** forwarding to `ApicClient`. The APIC silently returns `[]` for unknown classes — this pre-check catches typos and returns a typed `UnknownClassError` with nearest matches so the LLM can self-correct.

### Stateless HTTP

The server runs with `stateless_http=True` — each MCP request is an independent HTTP call with no session state on the server side. This makes horizontal scaling trivial and keeps the memory footprint flat.

### Single credentials file

Both `mcp/` and `schema-collector/` read the `.env` at the monorepo root. No duplication, no sync issue.
