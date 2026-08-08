<div align="center">

<h1>niwashi-mcp</h1>

<img src="docs/assets/banner.jpg" alt="niwashi-mcp banner" width="800"/>

<br/><br/>

<h3>One command, and your agent can read the fabric.</h3>

<p>A schema-driven MCP server for Cisco ACI —<br/>
it reads the fabric's own object model instead of a fixed list of endpoints,<br/>
so any LLM can reach all 15,452 classes with no hardcoded class knowledge.</p>

<br/>

[![Version](https://img.shields.io/badge/version-2.0.0-blue)](CHANGELOG.md)
[![License: PolyForm NC](https://img.shields.io/badge/license-PolyForm%20NC-blue)](LICENSE)
[![Commercial License](https://img.shields.io/badge/license-Commercial-orange)](LICENSE-COMMERCIAL.md)
[![Python](https://img.shields.io/badge/python-3.12+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastMCP](https://img.shields.io/badge/FastMCP-3.4+-00C896)](https://github.com/jlowin/fastmcp)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)](mcp/deploy/Dockerfile)
<br/>

![Cisco ACI](https://img.shields.io/badge/Cisco-ACI-1BA0D7?style=flat-square&logo=cisco&logoColor=white)
![MCP](https://img.shields.io/badge/Model%20Context%20Protocol-MCP-FF6B35?style=flat-square)
![Network AIOps](https://img.shields.io/badge/Network-AIOps-7B2FBE?style=flat-square)
![Network Automation](https://img.shields.io/badge/Network-Automation-0066CC?style=flat-square)
![AI Agent](https://img.shields.io/badge/AI-Agent%20Ready-00A67E?style=flat-square)

</div>

---

## Install

```bash
uvx niwashi-mcp
```

That is the whole installation. No checkout, no schema bundle, no data directory
— the ACI object model travels inside the server's own dependencies.

You need Python 3.12+, [`uv`](https://github.com/astral-sh/uv), and an APIC to
point at. If you do not have one, the free
[Cisco DevNet Always-On ACI sandbox](https://devnetsandbox.cisco.com) works and
requires no hardware. Cisco publishes its hostname and credentials on that page —
read them there rather than from here, since Cisco rotates them:

```bash
export APIC_HOST=<host from the DevNet sandbox page>
export APIC_USER=<user from the DevNet sandbox page>
export APIC_PASSWORD=<password from the DevNet sandbox page>

uvx niwashi-mcp
```

The server listens on `http://127.0.0.1:8000` — loopback only by default. Set `MCP_HOST` to expose it, and set `MCP_API_KEYS` first: it holds your APIC credentials, so a routable bind without authentication is refused.
`/health` answers without authentication for container and load-balancer probes.
Startup logs how many classes were indexed and which APIC release the object
model was built from.

Credentials can come from a `.env` file instead of the environment. The server
reads the first one that exists:

| Order | Location |
|---|---|
| 1 | `$NIWASHI_MCP_ENV_FILE` — an explicit path, which wins over everything below |
| 2 | `./.env` in the working directory |
| 3 | `.env` at the root of a git checkout, when the server runs from one |
| 4 | `~/.config/niwashi-mcp/.env` |

Variables already present in the environment are never overwritten by the file.

---

## Connect your agent

Two steps: **register the server**, then **load the ACI skill** so your agent
knows how to navigate the object model.

### Register the server

The server speaks MCP over HTTP. Point your client at:

```text
http://localhost:8000/mcp      # local
https://your-domain.com/mcp    # production, behind Caddy TLS
```

#### Claude Desktop

Edit `claude_desktop_config.json`:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "niwashi-mcp": {
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

> Omit `headers` when `MCP_API_KEYS` is unset (development only).

#### Claude Code (CLI)

```bash
claude mcp add niwashi-mcp --transport http http://localhost:8000/mcp
```

With authentication:

```bash
claude mcp add niwashi-mcp --transport http http://localhost:8000/mcp \
  --header "Authorization: Bearer <your-token>"
```

#### OpenCode

Add to your project's `.opencode/config.json`:

```json
{
  "mcp": {
    "servers": {
      "niwashi-mcp": {
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

#### Cursor, Windsurf, any other client

Same URL, same bearer token, in whatever the client calls its MCP server
settings. Any client implementing MCP 2025-03-26 works: the server publishes
RFC 9728 protected-resource metadata at
`/.well-known/oauth-protected-resource`, so a spec-compliant client discovers
that it needs a pre-shared token rather than attempting an OAuth redirect.

A ready-made client config file lives in [`mcp/client/`](mcp/client/).

### Load the ACI skill

The object model has 15,452 classes. Without context, an LLM guesses class names
— and a wrong class name returns an empty result rather than an error.

[`mcp/client/SKILL.md`](mcp/client/SKILL.md) teaches an agent the object model,
how to read a schema, how DNs are built, and when to reach for each tool.

| Client | How to load it |
|---|---|
| Claude Desktop / Projects | Paste `SKILL.md` into the project instructions |
| Claude Code | `cp mcp/client/SKILL.md .claude/niwashi-mcp.md` |
| OpenCode | `cp mcp/client/SKILL.md .opencode/skills/niwashi-mcp/SKILL.md` |
| Anything else | Paste it into the system prompt or context file |

Then ask:

> *"List all tenants configured in this fabric."*

The agent calls `search_classes`, `get_schema` and `query` in that order, with
no hand-written API work.

---

## Where the object model comes from

The server does not ship the ACI object model, and it does not download one. It
reads a SQLite catalogue embedded in its [`niwaki`](https://pypi.org/project/niwaki/)
dependency — a single 36 MB file covering all **15,452** managed-object classes,
built from APIC **6.0(9c)**. `get_schema()` reads that file directly; the keyword
index behind `search_classes()` is rebuilt from it once at startup.

That is the whole of 2.0. The five tools, their signatures and their output are
unchanged; only the source of the data moved.

| | 1.2.2 | 2.0 |
|---|---|---|
| Install | `git clone`, download a schema bundle, extract it | `uvx niwashi-mcp` |
| Download | 98.8 MB tarball | the `niwaki` wheel (18.8 MB at 1.8.0) |
| Object model on disk | 1.82 GB across 15,452 jsonmeta files | one 36 MB SQLite file |
| Container image | 3.97 GB | 457 MB |
| Data directory to manage | `data/` | none |

The APIC release is now pinned by the `niwaki` dependency rather than chosen by
whoever ran the collector, which is why the server logs it at startup.

### Why two class counts

Both numbers below are correct, and they mean different things:

| Number | Meaning |
|---|---|
| **15,452** | Classes that exist. `query()` and `count()` accept every one of them. |
| **15,239** | Classes that are *searchable*. `search_classes()` reaches these. |

The 213-class difference is classes carrying no label, no comment and no usable
property label — there is nothing to index on. They remain fully queryable once
you know their name.

Class lookup is case-sensitive, structurally: the catalogue uses SQLite's BINARY
collation, so `fvBd` does not resolve to `fvBD`.

---

## The five tools

| Tool | What it does |
|---|---|
| `search_classes(keyword)` | Weighted keyword search across the 15,239 indexed classes |
| `get_schema(class_name, ...)` | Identifiers, DN formats, containment, children, relations, and on demand per-property constraints |
| `query(class_name, ...)` | A scoped, filtered query against the APIC, returning a paging envelope |
| `get_by_dn(dn, ...)` | Fetches one object directly by DN — the known-DN shortcut |
| `count(class_name, ...)` | Tallies objects of a class without transferring them |

Discovery follows a fixed order — `search_classes` → `get_schema` → `query` —
because the APIC does not reject an unknown class name or a misspelled
attribute; it returns nothing at all. Two shortcuts skip it: `get_by_dn` when
the DN is already known, and `count` when the question is only "how many".

`get_schema()` returns `className`, `classPkg`, `containedBy`, `contains`,
`dnFormats`, `identifiedBy`, `isAbstract`, `isConfigurable`, `label`,
`properties`, `relationFrom`, `relationTo` and `rnFormat`, plus
`property_details` when you ask for it. Property details are opt-in because many
classes carry over a hundred properties; `properties_filter=[...]` asks for only
the ones you intend to set or filter on.

Search quality on the 74-query golden set: **Recall@1 78.4 %, Recall@5 94.6 %,
MRR 0.846**. Those figures are asserted as equalities in `mcp/tests/baseline/`,
not as floors — any movement is treated as a bug rather than a trade-off.

Per-tool reference with worked examples: [`docs/tools/`](docs/tools/).

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `APIC_HOST` | ✓ | — | APIC hostname or URL (a `https://` prefix is stripped) |
| `APIC_USER` | | `admin` | APIC username |
| `APIC_PASSWORD` | ✓ | — | APIC password |
| `APIC_VERIFY_SSL` | | `false` | Set `true` to enforce TLS certificate verification |
| `MCP_PORT` | | `8000` | HTTP port the server listens on |
| `MCP_API_KEYS` | | — | Comma-separated bearer tokens. Unset means no authentication (development only) |
| `NIWASHI_MCP_ENV_FILE` | | — | Explicit path to the `.env` file to load |
| `MCP_DOMAIN` | | — | Public hostname for Caddy TLS. Read by the production compose stack, not by the server |

**Hot reload:** send `SIGHUP` to the process to reload `MCP_API_KEYS` from the
`.env` file without restarting — key rotation with no downtime.

Full reference: [`docs/configuration/settings.md`](docs/configuration/settings.md).

---

## Docker

```bash
# The build context must be the repository root
docker build -f mcp/deploy/Dockerfile . -t niwashi-mcp
docker run --env-file .env -p 8000:8000 niwashi-mcp
```

The image installs the package rather than copying modules in, so a container
runs exactly what a wheel produces. There is no volume to mount: the object
model is inside the image, in the `niwaki` dependency.

### Production — MCP behind Caddy

The production stack puts the server behind a Caddy reverse proxy that
terminates TLS. The MCP port is never published on the host; all traffic enters
through Caddy on 443.

```bash
# Set MCP_DOMAIN and MCP_API_KEYS in .env, then:
docker compose -f mcp/deploy/docker-compose.yml up -d
```

| TLS mode | When to use it |
|---|---|
| **Let's Encrypt** | Public hostname, ports 80/443 reachable from the internet |
| **Caddy internal CA** | LAN or self-signed — run `docker compose exec caddy caddy trust` once |

Details: [`docs/getting-started/https.md`](docs/getting-started/https.md).

---

## Security

- Bearer token authentication — `Authorization: Bearer` or `X-API-Key`, compared
  with `hmac.compare_digest` so token values leak nothing through timing
- Per-IP rate limiting on failed authentication — 30 attempts per 60 s, then `429`
- RFC 9728 discovery metadata, and a `WWW-Authenticate` header on `401` that
  points at it
- Only three paths bypass authentication, by design: `/health` for liveness
  probes, and `/.well-known/*` plus `/register` so spec-compliant clients can
  complete discovery before they hold a token
- TLS terminated by Caddy in production; the MCP port is never exposed
- API keys reloadable via `SIGHUP`

Vulnerability disclosure policy: [SECURITY.md](SECURITY.md).

---

## Migrating from 1.x

Upgrading is mostly deletion. The tools an agent calls did not change, so a
client config only needs its URL to keep working.

| What changed | What to do |
|---|---|
| Distribution is `niwashi-mcp`, import package `niwashi_mcp` | Install with `uvx niwashi-mcp` |
| `python main.py` is deprecated | Use `niwashi-mcp`, or `python -m niwashi_mcp.main`. The shim still works and goes away in 3.0 |
| `data/` and the schema download are gone | Delete the directory; drop any `/data` volume mount from your compose file |
| `query()` and `count()` now accept 15,452 classes instead of 15,239 | Nothing — strictly more classes are valid than before |
| `mo:MoClassId`, `mo:PropId`, `mo:StatsClassId` and `mo:StatsPropId` properties no longer carry an `options` list | Nothing. One of them listed 17,653 entries — the entire class list — into the agent's context |
| `SchemaLoadError` can no longer be raised | A missing or unreadable catalogue raises `DescriptionsLoadError` instead |

Everything else holds: the same five tools, the same search scoring, the same
APIC client with its cookie authentication, retry and pagination, and
`relationTo[*].cardinality` still empty on every entry — the real cardinality
lives on the relation class itself.

Full detail, including what was measured to prove the swap changed nothing:
[CHANGELOG.md](CHANGELOG.md).

---

## Working from a checkout

Only needed to develop the server or run its test suite:

```bash
git clone https://github.com/k3l0-dev/niwashi-mcp.git
cd niwashi-mcp/mcp

uv sync
uv run niwashi-mcp     # reads ../.env
uv run pytest
```

---

## Documentation

Full documentation is in [`docs/`](docs/):

| Section | Contents |
|---|---|
| [`docs/getting-started/`](docs/getting-started/) | Quickstart, Docker, HTTPS / TLS setup |
| [`docs/concepts/`](docs/concepts/) | The ACI object model, for readers who are not network engineers |
| [`docs/tools/`](docs/tools/) | Tool reference with examples |
| [`docs/configuration/`](docs/configuration/) | Every environment variable |
| [`docs/architecture/`](docs/architecture/) | Components, startup sequence, data flow |
| [`docs/internals/`](docs/internals/) | Catalogue adapter, search algorithm, middleware, APIC client |

---

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) to get started.

---

## License

[PolyForm Noncommercial 1.0.0](LICENSE) — © 2026 Khalid El-Ouiali, MONARK AIOPS srl.

Free for personal, research, and noncommercial use. A [commercial license](LICENSE-COMMERCIAL.md)
is required for any commercial deployment or integration.

Contact: [monark.aiops@pm.me](mailto:monark.aiops@pm.me)

Cisco, Cisco ACI and APIC are trademarks of Cisco Systems, Inc.  niwashi-mcp
is an independent project, not affiliated with or endorsed by Cisco Systems, Inc.
