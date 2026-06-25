# System Overview

## What aci-mcp does

`aci-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server that gives any MCP-compatible LLM client read access to a Cisco ACI fabric — without any hardcoded class knowledge in the model or the server.

It exposes **three generic tools**. The LLM calls them in sequence to discover, inspect, and query any ACI object class — including classes added after the model was trained.

---

## Monorepo layout

```mermaid
graph TD
    subgraph repo["aci-mcp (monorepo)"]
        mcp["mcp/\nFastMCP server"]
        data["data/\nshared artifacts"]
        env[".env\nshared credentials"]
    end

    mcp -->|"reads at startup"| data
    mcp -->|"reads credentials"| env
```

`mcp/` reads `data/` (schema bundle) and `.env` (APIC credentials) at the repo root.

---

## Component architecture

```mermaid
graph TB
    subgraph client["LLM Client (Claude Desktop / Cursor / OpenCode / Agent)"]
        llm["LLM"]
    end

    subgraph prod["Production stack (docker-compose)"]
        caddy["Caddy\nTLS termination\nport 443 / 80"]
        subgraph mcp_server["aci-mcp container (port 8000, internal)"]
            health["HealthMiddleware\nGET /health → 200"]
            oauth["OAuthDiscoveryMiddleware\n/.well-known/oauth-protected-resource"]
            auth["ApiKeyMiddleware\nBearer / X-API-Key + rate limiting"]
            fm["FastMCP dispatcher"]
            t1["search_classes"]
            t2["get_schema"]
            t3["query"]
            subgraph registry["Registry (in-memory / on-disk)"]
                desc["descriptions index\n15k+ classes"]
                schema["jsonmeta loader\nlazy, per-class"]
                filt["filter builder\nAPIC eq() syntax"]
            end
            apic_client["ApicClient\nhttpx async, cookie auth"]
        end
    end

    subgraph apic["Cisco APIC"]
        rest["REST API\nHTTPS"]
    end

    subgraph datastore["data/ (shared)"]
        json["class-descriptions.json"]
        schemas_dir["schemas/{version}/*.json\n15k+ jsonmeta files"]
    end

    llm -->|"MCP JSON-RPC"| caddy
    caddy -->|"HTTP plain (internal)"| health
    health -->|"non-health requests"| oauth
    oauth -->|"non-discovery requests"| auth
    auth -->|"authenticated requests"| fm
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

## Middleware stack

Three middleware layers wrap FastMCP, outermost first:

| Order | Middleware | Purpose |
|---|---|---|
| 1 (outermost) | `HealthMiddleware` | Intercepts any HTTP request to `/health` and returns `{"status":"ok"}` — no auth required. Pure ASGI, zero overhead. |
| 2 | `OAuthDiscoveryMiddleware` | Serves RFC 9728 Protected Resource Metadata at `/.well-known/oauth-protected-resource`. Required by MCP 2025-03-26-compliant clients before authentication. |
| 3 | `ApiKeyMiddleware` | Validates `Authorization: Bearer` or `X-API-Key` tokens. Applies per-IP rate limiting (30 failed attempts / 60 s). Returns `WWW-Authenticate: Bearer resource_metadata="..."` on 401. |

`/.well-known/*` and `/register` bypass `ApiKeyMiddleware` entirely so OAuth discovery and dynamic client registration are never blocked by auth.

---

## Request path

| Step | Where | What happens |
|---|---|---|
| 1 | LLM client | Sends MCP tool call over JSON-RPC |
| 2 | Caddy | Terminates TLS, proxies to port 8000 |
| 3 | `HealthMiddleware` | Passes through (not `/health`) |
| 4 | `OAuthDiscoveryMiddleware` | Passes through (not a discovery path) |
| 5 | `ApiKeyMiddleware` | Validates bearer token — 401 or 429 if invalid/rate-limited |
| 6 | FastMCP dispatcher | Routes to the correct tool function |
| 7 | Tool | Reads registry or calls APIC |
| 8 | `ApicClient` | Builds URL + filter params, sends HTTPS GET to APIC |
| 9 | APIC | Returns `imdata` JSON array |
| 10 | Tool | Flattens objects, adds `_class` key, returns list |
| 11 | FastMCP | Serialises response as MCP JSON-RPC result |

---

## Startup sequence

```mermaid
sequenceDiagram
    participant OS as OS / Docker
    participant server as main.py
    participant registry as Registry
    participant apic as Cisco APIC

    OS->>server: python main.py
    server->>server: load_dotenv(.env)
    server->>server: validate MCP_PORT (int)
    server->>server: load_api_keys() → KeyStore
    server->>server: register SIGHUP handler (hot-reload keys)
    server->>server: build middleware stack [Health, OAuth, ApiKey]
    server->>server: start FastMCP lifespan

    Note over server: inside app_lifespan()
    server->>server: validate APIC_HOST, APIC_PASSWORD
    server->>registry: load_descriptions(class-descriptions.json)
    registry-->>server: 15k-class dict (in-memory)
    server->>apic: POST /api/aaaLogin.json
    apic-->>server: APIC-cookie token
    server->>server: start HTTP on MCP_PORT
    Note over server: Ready — context shared with all tools
```

---

## Key design decisions

### Lazy schema loading

`registry/schema.py` reads individual jsonmeta files on demand. There is no upfront scan of 15 000+ files. The first `get_schema("fvBD")` call reads the file from disk; subsequent calls read it again — the OS page cache is the caching layer. Heavy jsonmeta fields (`writeAccess`, `events`, `stats`, `faults`) are discarded at load time to keep tool responses token-efficient.

### Class validation before APIC

`query()` checks `class_name` against the in-memory `descriptions` dict before forwarding to `ApicClient`. The APIC silently returns `[]` for unknown classes. This pre-check returns a typed `UnknownClassError` with nearest matches so the LLM can self-correct without an extra `search_classes()` round-trip.

### Stateless HTTP

`stateless_http=True` — each MCP request is an independent HTTP call. No session state on the server side. Horizontal scaling is trivial; memory footprint stays flat.

### SIGHUP hot-reload

Sending `SIGHUP` to the server process reloads `MCP_API_KEYS` from `.env` without a restart. The `KeyStore.reload()` call is atomic under a `threading.Lock`, so in-flight requests continue with the old key set uninterrupted.

```bash
kill -HUP $(pgrep -f "python main.py")
```

### Single credentials file

`mcp/` reads `.env` at the monorepo root — APIC credentials and server settings in one place.
