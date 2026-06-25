<div align="center">

<h1>ACI MCP (Model Context Protocol)</h1>

<img src="docs/assets/banner.jpg" alt="aci-mcp banner" width="800"/>

<br/><br/>

<h3>Your agent can now literally talk to your datacenter.</h3>

<p>The first open-source MCP server for Cisco ACI —<br/>
give any LLM instant, schema-aware access to your APIC fabric.</p>

<br/>

[![Version](https://img.shields.io/badge/version-1.0.0-blue)](CHANGELOG.md)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20NC-blue)](LICENSE)
[![Commercial License](https://img.shields.io/badge/license-Commercial-orange)](LICENSE-COMMERCIAL.md)
[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP](https://img.shields.io/badge/FastMCP-3.1+-00C896)](https://github.com/jlowin/fastmcp)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](mcp/deploy/Dockerfile)

<br/>

![Cisco ACI](https://img.shields.io/badge/Cisco-ACI-1BA0D7?style=flat-square&logo=cisco&logoColor=white)
![DevNet](https://img.shields.io/badge/Cisco-DevNet-6CC04A?style=flat-square&logo=cisco&logoColor=white)
![MCP](https://img.shields.io/badge/Model%20Context%20Protocol-MCP-FF6B35?style=flat-square)
![Network AIOps](https://img.shields.io/badge/Network-AIOps-7B2FBE?style=flat-square)
![Network Automation](https://img.shields.io/badge/Network-Automation-0066CC?style=flat-square)
![AI Agent](https://img.shields.io/badge/AI-Agent%20Ready-00A67E?style=flat-square)

</div>

---

## Why aci-mcp

The APIC object model has **15 000+ managed object classes**. Querying it correctly requires knowing exact class names, DN structures, and filter syntax — expertise that takes years to build and changes with every APIC release.

`aci-mcp` ships the object model itself as a live, queryable index. Your LLM discovers classes at runtime, inspects their schemas on demand, and executes typed queries against the APIC. It works with any MCP-compatible client: **Claude Desktop, Cursor, OpenCode, Windsurf**, and more.

---

## Before you start

You need:

- An APIC — or the free [Cisco DevNet Always-On sandbox](https://devnetsandbox.cisco.com) (no hardware required)
- Python 3.12+ and [`uv`](https://github.com/astral-sh/uv)
- Docker (optional, for containerized deployment)

---

## Step 1 — Get the schemas

The MCP server needs the ACI jsonmeta schema files to serve `get_schema()` requests.
Choose one of two paths:

### Option A — Download the prebuilt bundle (fastest)

```bash
# Clone the repo
git clone https://github.com/k3l0-dev/aci-mcp.git
cd aci-mcp

# Download and extract schemas from the GitHub release (~200 MB)
./scripts/download-schemas.sh
```

Done. The bundle ships with schemas collected from APIC **6.0(9c)**.

### Option B — Collect from your own APIC

Run this if you have a different APIC version, or want the freshest data:

```bash
# Configure credentials first
cp .env.example .env
# Edit .env: set APIC_HOST, APIC_USER, APIC_PASSWORD

cd schema-collector
uv sync
uv run aci-collect run
```

The pipeline fetches 15 000+ jsonmeta files directly from your APIC and writes:

```text
data/
  class-descriptions.json   ← keyword-searchable class index
  schemas/{version}/        ← full jsonmeta files, one per class
```

---

## Step 2 — Run the server

### Local (development)

```bash
cd mcp
uv sync
python main.py
```

The server starts on `http://localhost:8000`. The MCP endpoint is at `/mcp`.

### Docker

```bash
# From the repo root
docker build -f mcp/deploy/Dockerfile . -t aci-mcp
docker run --env-file .env -p 8000:8000 aci-mcp
```

### Production — MCP + Caddy TLS

The production stack runs `aci-mcp` behind a Caddy reverse proxy with automatic TLS.
The MCP port is never exposed directly — all traffic enters via Caddy on 443.

```bash
# Set MCP_DOMAIN and MCP_API_KEYS in .env, then:
docker compose -f mcp/deploy/docker-compose.yml up -d
```

Two TLS modes are supported — configure via `MCP_DOMAIN` in `.env`:

| Mode | When to use |
|---|---|
| **Let's Encrypt** | Public hostname, ports 80/443 reachable from the internet |
| **Caddy internal CA** | LAN / self-signed — run `docker compose exec caddy caddy trust` once |

See [`docs/getting-started/https.md`](docs/getting-started/https.md) for full TLS setup instructions.

---

## Step 3 — Connect your agent

Two things to do: **register the MCP server** and **load the ACI skill** so your agent knows how to navigate the object model.

### Register the MCP server

The server URL is:

```text
http://localhost:8000/mcp       # local / dev
https://your-domain.com/mcp    # production (Caddy + TLS)
```

A ready-made client config is at [`mcp/client/aci-mcp.json`](mcp/client/aci-mcp.json).

#### Claude Desktop

Edit `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "aci-mcp": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer <your-token>"
      }
    }
  }
}
```

Restart Claude Desktop. The server appears under **MCP** in the tool menu.

> Omit `headers` if `MCP_API_KEYS` is not set (dev mode).

#### Claude Code (CLI)

```bash
claude mcp add aci-mcp --transport http http://localhost:8000/mcp
```

With authentication:

```bash
claude mcp add aci-mcp --transport http http://localhost:8000/mcp \
  --header "Authorization: Bearer <your-token>"
```

#### OpenCode

Add to your project's `.opencode/config.json`:

```json
{
  "mcp": {
    "servers": {
      "aci-mcp": {
        "type": "http",
        "url": "http://localhost:8000/mcp",
        "headers": {
          "Authorization": "Bearer <your-token>"
        }
      }
    }
  }
}
```

#### Cursor / Windsurf / any MCP client

Use the same URL and bearer token in your client's MCP server settings. All MCP 2025-03-26-compliant clients are supported.

---

### Load the ACI skill

The ACI object model has 15 000+ classes. Without context, any LLM will guess class names — and guessing silently returns empty results.

The file [`mcp/client/SKILL.md`](mcp/client/SKILL.md) teaches your agent the full ACI object model, how to read schemas, how to build DN paths, and when to use each of the three tools. **Load it once and your agent stops guessing.**

#### Claude Desktop / Claude Projects

Create a [Claude Project](https://support.anthropic.com/en/articles/9517075-what-are-projects) and paste the contents of `mcp/client/SKILL.md` into the project instructions.

#### Claude Code (CLI)

```bash
# Add the skill to your project context
cp mcp/client/SKILL.md .claude/aci-mcp.md
```

Claude Code picks up any `.md` file under `.claude/` automatically.

#### OpenCode

```bash
mkdir -p .opencode/skills/aci-mcp
cp mcp/client/SKILL.md .opencode/skills/aci-mcp/SKILL.md
```

OpenCode loads skills from `.opencode/skills/` at session start.

#### Other agents

Paste the contents of `mcp/client/SKILL.md` into your agent's system prompt, project instructions, or context file — whatever your platform calls it.

---

Once both steps are done, ask your agent:

> *"Show me all bridge domains in the OT tenant with their associated VRFs."*

It will call `search_classes`, `get_schema`, and `query` in the correct order — no manual API work required.

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `APIC_HOST` | ✓ | — | APIC hostname or URL |
| `APIC_USER` | | `admin` | APIC username |
| `APIC_PASSWORD` | ✓ | — | APIC password |
| `APIC_VERIFY_SSL` | | `false` | Set `true` to enforce TLS certificate verification |
| `MCP_PORT` | | `8000` | HTTP port |
| `MCP_API_KEYS` | | — | Comma-separated bearer tokens. Unset = no auth (dev only) |
| `MCP_DOMAIN` | | — | Public hostname for Caddy TLS (production stack only) |

**Hot-reload:** send `SIGHUP` to the server process to reload `MCP_API_KEYS` from `.env` without a restart.

---

## Security

- Bearer token authentication (`Authorization: Bearer` or `X-API-Key`)
- Per-IP rate limiting on failed auth — 30 attempts / 60 s, returns `429`
- RFC 9728 `WWW-Authenticate` discovery header on `401`
- TLS termination via Caddy — the MCP port is never exposed in production
- Hot-reload of API keys via `SIGHUP` — zero-downtime key rotation

See [SECURITY.md](SECURITY.md) for the vulnerability disclosure policy.

---

## Tools

The server exposes three tools. Documentation and usage examples are in [`SKILL.md`](SKILL.md) and [`docs/tools/`](docs/tools/).

| Tool | Description |
|---|---|
| `search_classes(keyword)` | Weighted keyword search across 15 000+ ACI classes |
| `get_schema(class_name)` | Returns DN format, properties, and containment hierarchy |
| `query(class_name, ...)` | Executes a scoped, filtered query against the APIC |

---

## Documentation

Full documentation is in [`docs/`](docs/):

| Section | Contents |
|---|---|
| [`docs/getting-started/`](docs/getting-started/) | Quickstart, Docker, HTTPS / TLS setup |
| [`docs/tools/`](docs/tools/) | Tool reference with examples |
| [`docs/configuration/`](docs/configuration/) | All environment variables |
| [`docs/internals/`](docs/internals/) | Architecture, search algorithm, auth internals |

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — © 2026 Khalid El-Ouiali, MONARK AIOPS srl.

Free for personal, research, and noncommercial use. A [commercial license](LICENSE-COMMERCIAL.md)
is required for any commercial deployment or integration.

Contact: [monark.aiops@pm.me](mailto:monark.aiops@pm.me)
