# Quickstart

Two things stand between you and a running server: a `.env` holding three
variables, and one command.

There is no repository to clone and no schema bundle to download. The ACI
object model — 15 452 classes, generated from APIC **6.0(9c)** — ships as a
single SQLite catalogue (36 229 120 bytes) inside the `niwaki` dependency, which
`uv` fetches along with the rest of the wheel.

## Prerequisites

- Python 3.12+
- [`uv`](https://github.com/astral-sh/uv) — `brew install uv` or `pip install uv`
- A reachable Cisco APIC — or the free [Cisco DevNet Always-On sandbox](https://devnetsandbox.cisco.com)

---

## 1 — Configure credentials

Create a `.env` in the directory you will start the server from:

```dotenv
APIC_HOST=your-apic.example.com
APIC_USER=admin
APIC_PASSWORD=your_password
```

Those three are the whole minimum. `APIC_HOST` takes a bare hostname or IP —
an `https://` or `http://` prefix is stripped if present, and the connection to
the APIC is always HTTPS. `APIC_USER` defaults to `admin` if omitted;
`APIC_HOST` and `APIC_PASSWORD` have no default and raise `ConfigurationError`
at startup when empty.

Leave `MCP_API_KEYS` unset for local work. The server then accepts unauthenticated
requests and says so, loudly, in the first line it logs.

### Where `.env` is looked for

| Order | Location |
|---|---|
| 1 | `$NIWASHI_MCP_ENV_FILE`, if set — taken verbatim, whether or not the file exists |
| 2 | `.env` in the current working directory |
| 3 | `.env` at the repository root, when running from a checkout |
| 4 | `~/.config/niwashi-mcp/.env` |

Past the override, the first location that exists wins; if none does, the server
starts on the real environment alone, which is not an error. Real environment
variables also take precedence over the file, so `MCP_PORT=9000 niwashi-mcp`
overrides whatever the `.env` says.

Every variable, with its default and its validation rule, is in the
[settings reference](../configuration/settings.md).

---

## 2 — Start the server

```bash
uvx niwashi-mcp
```

`uvx` resolves the package into a throwaway environment and runs it. To keep it
installed instead of resolving on every launch:

```bash
uv tool install niwashi-mcp
niwashi-mcp
```

Startup looks like this (the FastMCP banner is omitted):

```text
2026-08-08 10:58:42,939  WARNING   niwashi-mcp  MCP_API_KEYS is not set — server is running WITHOUT authentication. Set MCP_API_KEYS in .env before deploying to production.
2026-08-08 10:58:43,311  INFO      niwashi-mcp  Registry loaded — 15239 class descriptions (niwaki catalogue, APIC 6.0(9c))
2026-08-08 10:58:43,323  INFO      niwashi-mcp  Connected to APIC — your-apic.example.com
[08/08/26 10:58:43] INFO     Starting MCP server 'niwashi-mcp' with transport 'http' (stateless) on http://0.0.0.0:8000/mcp
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

Those lines are not printed in the order you might assume. The API-key warning
comes out before the HTTP server exists at all; the registry and APIC lines come
from the FastMCP lifespan, which only runs once the server starts serving. A
process that dies after the registry line has a credentials or reachability
problem, not a data problem.

The number in that registry line is not the number of ACI classes. **15 239** is
the size of the search index — the classes `search_classes` can find. **15 452**
classes are known to the catalogue and accepted by `query` and `count`. The
213-class difference is classes carrying no label, no comment and no usable
property label: there is nothing to index them on, so they are not searchable,
but they stay perfectly queryable once you know the name.

The MCP endpoint is at `http://localhost:8000/mcp`. Confirm the process is
alive without a token:

```bash
curl http://localhost:8000/health
```

```json
{"status": "ok"}
```

Set `MCP_PORT` to listen elsewhere. The bind address is always `0.0.0.0`.

---

## 3 — Connect your agent

### Claude Desktop

Edit `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "niwashi-mcp": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

The key is a client-side label of your choosing; the ready-made file at
[`mcp/client/niwashi-mcp.json`](../../mcp/client/niwashi-mcp.json) uses `niwashi-mcp`.
Restart Claude Desktop — the server appears under **MCP** in the tool menu.

### Claude Code (CLI)

```bash
claude mcp add niwashi-mcp --transport http http://localhost:8000/mcp
```

### OpenCode

Add to `.opencode/config.json` in your project:

```json
{
  "mcp": {
    "servers": {
      "niwashi-mcp": {
        "type": "http",
        "url": "http://localhost:8000/mcp"
      }
    }
  }
}
```

### Cursor / Windsurf / other MCP clients

Point the client at `http://localhost:8000/mcp`. Any MCP 2025-03-26-compliant
client works; the server answers the RFC 9728 discovery probe at
`/.well-known/oauth-protected-resource` so clients that check it do not fail on
an HTML 404.

---

## 4 — Load the ACI skill

[`mcp/client/SKILL.md`](../../mcp/client/SKILL.md) teaches your agent the ACI
object model: DN structure, class hierarchy, how to read a schema, and the query
patterns that go with each tool. Without it the agent guesses, and the two kinds
of guess fail differently. A wrong class name is caught — `query` and `count`
check it against the catalogue and raise `UnknownClassError` with the closest
matches. A wrong *attribute* name is not: the APIC accepts the filter and
returns `[]`, which reads exactly like "there are none". That is why the
`search_classes` → `get_schema` → `query` order is not a suggestion.

If you are running from `uvx` and have no checkout, take the file from the
repository:
[`mcp/client/SKILL.md`](https://github.com/k3l0-dev/aci-mcp/blob/main/mcp/client/SKILL.md).

### Claude Code

Claude Code picks up every `.md` file under `.claude/`:

```bash
cp mcp/client/SKILL.md .claude/niwashi-mcp.md
```

### Claude Desktop / Projects

Paste the contents into the project instructions of a
[Claude Project](https://support.anthropic.com/en/articles/9517075-what-are-projects).

### OpenCode

```bash
mkdir -p .opencode/skills/niwashi-mcp
cp mcp/client/SKILL.md .opencode/skills/niwashi-mcp/SKILL.md
```

---

## Running from a checkout

Working on the server itself is the one case that still needs the repository.

```bash
git clone https://github.com/k3l0-dev/aci-mcp.git
cd aci-mcp
cp .env.example .env      # then fill in APIC_HOST / APIC_USER / APIC_PASSWORD

cd mcp
uv sync
uv run niwashi-mcp
```

`uv sync` installs `niwaki` like any other dependency, so the catalogue lands in
the virtualenv — a checkout brings no data of its own.

Two equivalent entry points, for when the console script is inconvenient:

```bash
uv run python -m niwashi_mcp.main   # same code path, no console script
uv run python main.py               # deprecated shim, removal in 3.0
```

`mcp/main.py` exists only so deployments that started the server that way until
1.x do not break on upgrade. It prepends `src/` to `sys.path` and calls the same
`main()`. It raises a `DeprecationWarning`, which Python's default filters hide —
run `python -W always main.py` to see it.

---

## Running tests

```bash
cd mcp

# Everything except the live suite, which is excluded by default
uv run pytest

# One file
uv run pytest tests/unit/test_filter.py

# With coverage, as CI runs it
uv run pytest tests/ --ignore=tests/perf --cov=niwashi_mcp --cov-report=term-missing
```

Three markers select the suites that are not ordinary unit tests:

| Marker | Selects |
|---|---|
| `live` | Tests needing a reachable APIC. Excluded by default via `addopts`; run with `uv run pytest tests/live/ -m live` |
| `baseline` | Equality assertions against the pre-2.0 recorded behaviour — the drift net for the catalogue migration |
| `catalog` | Tests of the niwaki catalogue adapter, `registry/catalog.py` |

---

## Coming from 1.x

The command changed, and one whole step disappeared.

| | 1.2.2 | 2.0 |
|---|---|---|
| Install | `git clone` + download the schema bundle + `tar` | `uvx niwashi-mcp` |
| Download | 98.8 MB | 16.2 MB — the niwaki wheel |
| Object model on disk | 1.83 GB across 15 452 files | one 36 229 120-byte file |
| Docker image | 3.97 GB | 457 MB |
| Start | `python main.py` | `niwashi-mcp` |

The step that downloaded and extracted a jsonmeta bundle into `data/schemas/` is
gone, along with the script that did it and the `class-descriptions.json` index
beside it. There is no `data/` directory in 2.0 and no `ACI_MCP_DATA_DIR` to
point anywhere — the object model is inside the `niwaki` wheel, so it arrives
with the dependency resolution and cannot drift from the code that reads it.

The five tools — `search_classes`, `get_schema`, `query`, `get_by_dn`, `count` —
keep the signatures they had in 1.2.2. Only the source of the data changed. What
you will notice in practice:

- The APIC release the object model describes is now pinned by the `niwaki`
  dependency rather than chosen when you downloaded a bundle. It is logged at
  startup for exactly that reason.
- `query` and `count` accept 15 452 class names instead of 15 239. Validation has
  one source now, so the two-tier fallback that covered the gap is gone.
- Class names are case-sensitive as a property of the storage engine, not of a
  hand-written check: `fvBd` does not resolve to `fvBD`.
- `get_schema` no longer returns an `options` list for properties typed
  `mo:MoClassId`, `mo:StatsPropId`, `mo:StatsClassId` or `mo:PropId`. Those
  enumerated the class register — one `mo:MoClassId` carried 17 653 entries into
  the agent's context. Every other property keeps its options.
- Search is untouched: same scorer, same synonyms, same structural priors, and
  the same measured quality on the 74-query golden set — Recall@1 78.4 %,
  Recall@5 94.6 %, MRR 0.846.
- A missing or unreadable catalogue raises `DescriptionsLoadError`.
  `SchemaLoadError` meant a malformed jsonmeta file on disk; no code path can
  raise it any more.
