# niwashi-mcp

**MCP server for Cisco ACI (APIC).** It reads the fabric's own object model
rather than a fixed list of endpoints, so an LLM agent can navigate all 15,452
ACI classes through five generic tools — with no hardcoded class knowledge.

```bash
uvx niwashi-mcp
```

No repository to clone, no schema bundle to download. The object model ships
inside the [`niwaki`](https://pypi.org/project/niwaki/) dependency as an
embedded catalogue.

## Configure

Three variables, in a `.env` file or the environment:

```bash
APIC_HOST=10.0.0.1
APIC_USER=admin
APIC_PASSWORD=…
```

Optional: `APIC_VERIFY_SSL` (default `false`), `MCP_PORT` (default `8000`),
`MCP_API_KEYS` (comma-separated bearer tokens — **unset means no
authentication**, and the server says so loudly at startup),
`NIWASHI_MCP_ENV_FILE` (explicit path to the `.env`).

## The five tools

| Tool | Purpose |
|---|---|
| `search_classes` | find the exact ACI class name from a keyword |
| `get_schema` | identifiers, containment, relations, properties of a class |
| `query` | filtered class query against the APIC |
| `get_by_dn` | fetch one object directly by DN |
| `count` | count objects without transferring them |

The intended order is `search_classes` → `get_schema` → `query`. Skipping the
first two returns empty results silently, because the APIC answers `200 OK` with
an empty list for an unknown class or attribute — it does not error.

## Documentation

Full documentation, architecture notes and deployment guides:
**<https://github.com/k3l0-dev/niwashi-mcp>**

The repository also carries `mcp/client/SKILL.md` — the operating guide to give
an LLM client alongside the server. It teaches the ACI object model, the
mandatory tool order, and how to read a relation without concluding wrongly.

## Requirements

Python ≥ 3.12. A reachable APIC and a read-only service account.

## Licence

[PolyForm Noncommercial 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0/)
— free for personal, research and noncommercial use. Commercial deployment or
integration requires a commercial licence: <monark.aiops@pm.me>.

Cisco, Cisco ACI and APIC are trademarks of Cisco Systems, Inc. niwashi-mcp is
an independent project, not affiliated with or endorsed by Cisco Systems, Inc.
