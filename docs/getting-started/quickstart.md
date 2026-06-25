# Quickstart — Local Development

Get the server running locally in under 5 minutes.

## Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) — `brew install uv` or `pip install uv`
- A reachable Cisco APIC — or the free [Cisco DevNet Always-On sandbox](https://devnetsandbox.cisco.com)

---

## Steps

### 1 — Clone the repo

```bash
git clone https://github.com/k3l0-dev/aci-mcp.git
cd aci-mcp
```

### 2 — Configure credentials

```bash
cp .env.example .env
```

Edit `.env` — minimum required fields:

```dotenv
APIC_HOST=https://your-apic.example.com
APIC_USER=admin
APIC_PASSWORD=your_password
```

Leave `MCP_API_KEYS` empty — auth is disabled in dev mode automatically.

### 3 — Download the schema bundle

The MCP server needs the ACI jsonmeta files to serve `get_schema()` requests.
Download the prebuilt bundle from the GitHub release:

```bash
./scripts/download-schemas.sh
```

This extracts into:

```
data/
  class-descriptions.json   ← keyword-searchable class index
  schemas/{version}/        ← jsonmeta files, one per class
```

### 4 — Start the server

```bash
cd mcp
uv sync
python main.py
```

Expected output:

```
INFO  aci-mcp  Registry loaded — 15432 class descriptions
INFO  aci-mcp  Connected to APIC — your-apic.example.com
WARNING aci-mcp  MCP_API_KEYS is not set — server is running WITHOUT authentication.
INFO  fastmcp  Starting MCP server 'aci-mcp' with transport 'http' on http://0.0.0.0:8000/mcp
```

The MCP endpoint is at `http://localhost:8000/mcp`.

### 5 — Connect your agent

#### Claude Desktop

Edit `claude_desktop_config.json`:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "aci-mcp": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Restart Claude Desktop. The server appears under **MCP** in the tool menu.

#### Claude Code (CLI)

```bash
claude mcp add aci-mcp --transport http http://localhost:8000/mcp
```

#### OpenCode

Add to `.opencode/config.json` in your project:

```json
{
  "mcp": {
    "servers": {
      "aci-mcp": {
        "type": "http",
        "url": "http://localhost:8000/mcp"
      }
    }
  }
}
```

#### Cursor / Windsurf / other MCP clients

Use `http://localhost:8000/mcp` in your client's MCP server settings.
Any MCP 2025-03-26-compliant client is supported.

---

### 6 — Load the ACI skill

The file `mcp/client/SKILL.md` teaches your agent the ACI object model: DN structure, class hierarchy, how to use each tool, and common query patterns.
Without it, the agent will guess class names — and guessing silently returns empty results.

#### Claude Code

```bash
cp mcp/client/SKILL.md .claude/aci-mcp.md
```

Claude Code picks up all `.md` files under `.claude/` automatically.

#### Claude Desktop / Projects

Paste the contents of `mcp/client/SKILL.md` into the project instructions of a [Claude Project](https://support.anthropic.com/en/articles/9517075-what-are-projects).

#### OpenCode

```bash
mkdir -p .opencode/skills/aci-mcp
cp mcp/client/SKILL.md .opencode/skills/aci-mcp/SKILL.md
```

---

## Running tests

```bash
cd mcp

# All tests
uv run pytest

# Single file
uv run pytest tests/unit/test_filter.py

# With coverage
uv run pytest --cov=. --cov-report=term-missing
```

