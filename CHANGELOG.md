# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

## [1.1.0] - 2026-07-20

Expands the tool surface with three new capabilities (direct DN lookup,
cheap counting, config-only reads), rewrites `search_classes` for a large
ranking-quality improvement, and closes a set of correctness and
reliability gaps in schema loading, error handling, and class validation.

### Added

- `get_by_dn(dn, config_only, include_children)` — fetch a single object
  directly by its Distinguished Name (`GET /api/mo/{dn}.json`), the
  shortcut path when the exact DN is already known. Returns a structured
  `{"found": false, ...}` message for a missing DN instead of a bare `[]`.
- `count(class_name, filters, scope_dn, filter_expr)` — count objects of a
  class via APIC `rsp-subtree-include=count` without transferring them.
  Validates the class name against the registry like `query` (raises
  `UnknownClassError` with suggestions).
- `get_schema` now returns `contains` — a sorted list of the child class
  names an object may hold, ready to feed to `get_schema`/`query`/
  `include_children` (previously dropped from the response).
- `get_schema` gains `include_property_details` and `properties_filter`,
  exposing a compact per-property constraint dict (`type`, `access`,
  `naming`, `mandatory`, `default`, `options`, `comment`). Opt-in, so a
  100+-property class doesn't bloat every response by default.
- `query` and `get_by_dn` gain `config_only` — adds
  `rsp-prop-include=config-only` so only user-configurable attributes are
  returned, dropping operational noise for comparison, drift detection,
  and backup.
- `search_classes` scoring rewritten from raw substring matching to
  tokenized, camelCase-aware ranking: exact label/class-name matches
  dominate, token coverage rewards a query that names most of a concept,
  and property-label phrase matches surface functional queries (e.g. "ARP
  flooding" → `fvBD`). Structural priors — a boost for `isConfigurable`
  classes, penalties for `isAbstract`, stats/telemetry, and Rs/Rt relation
  classes — resolve ties that pure text matching never could, since dozens
  of ACI classes share an identical Cisco-assigned label. A small curated
  ACI jargon/synonym table covers the handful of terms with no textual
  anchor anywhere in the schema. Measured on a 74-query golden set:
  Recall@1 30.8% → 78.4%, Recall@5 53.8% → 94.6%. See
  `docs/internals/search-algorithm.md` for the full mechanics.
- `data/class-descriptions.json` regenerated with `isConfigurable`/
  `isAbstract` flags per class, feeding the search priors above.
- `ApicRequestError`: wraps non-2xx, non-authentication APIC responses
  (400 for a malformed `filter_expr`, 404, 500, ...), carrying the HTTP
  status and, when present, the APIC-supplied error text. Previously these
  escaped as a raw, unhelpful `httpx.HTTPStatusError`.
- A search-quality gate (`mcp/tests/eval/`) runs the golden-set evaluation
  as a pytest test with a floor on Recall@1/5, so a scoring regression
  fails the suite instead of only showing up in an offline report. The
  golden set itself grew from 39 to 74 queries.
- Tool-layer wiring tests prove that `query`/`get_by_dn`/`count`'s
  parameters (`page`, `rsp_subtree_include`, `time_range`, `config_only`)
  reach the real APIC request the client builds, and that an invalid
  filter attribute raises `FilterError` all the way out through the tool.
- An end-to-end test suite (`mcp/tests/live/`) exercises the real APIC
  client against a live fabric with no stubs — object queries, `get_by_dn`
  (found and not-found), `count`, `config_only`, and real APIC error
  responses. Marked `@pytest.mark.live` and excluded from the default test
  run, since a CI runner has no network path to a real fabric; run
  explicitly with `uv run pytest tests/live/ -m live`.
- CI now generates a coverage report and runs the full test suite (minus
  `tests/perf/`), so the search-quality gate above is actually exercised
  on every push.

### Changed

- The server's discovery workflow (its startup instructions and the
  bundled skill guide) is now scoped to genuine discovery: when the exact
  class and DN are already known, `get_by_dn` is the documented shortcut
  instead of always requiring `search_classes → get_schema → query`. The
  skill guide also gained an eventual-consistency note — a read taken
  right after a large config push reflects the fabric state at that
  instant, and a count can move for a few seconds while the change
  materializes.
- Schema loading no longer scans the schema directory tree on every call;
  the real jsonmeta directory is resolved once at server startup.
- `query()` no longer rejects a class outright just because it's absent
  from `class-descriptions.json`: it falls back to a direct schema-file
  check before raising `UnknownClassError`, closing a ~300-class gap
  between the registry and the full schema collection.
- `search_classes` and `query` clamp `limit` to a floor of 1 instead of
  passing a non-positive value straight through.

### Fixed

- `get_schema()` cold-load latency: the per-call directory scan described
  above measured ~8.3 ms against the full schema collection, against a
  5 ms budget; the fix brings it under 1 ms.
- A negative or zero `limit` on `search_classes`/`query` could reach the
  APIC as an invalid `page-size`, or silently mis-slice a result list.
- The `query()` registry/schema fallback could be tricked by a typo (e.g.
  `fvBd` for `fvBD`) into treating a bogus class as valid on a
  case-insensitive filesystem (the macOS/Windows default). Fixed by
  comparing the schema file's own class-identity fields instead of
  trusting the filesystem lookup alone.
- Installing the project's declared development dependencies and running
  its test suite (`uv sync && pytest`) previously failed outright — an
  async-test dependency lived in an optional extra the plain install never
  pulled in.
- The public CI workflow's lint and dependency-audit jobs referenced a
  directory removed from the repository in an earlier cleanup, so both had
  been failing on every push since. Removed the dead steps.

---

## [1.0.0] - 2026-06-24

First public open-source release.

### Added

- `LICENSE` — GNU Affero General Public License v3 (AGPL-3.0-or-later).
- `LICENSE-COMMERCIAL.md` — commercial license terms for proprietary integrations.
- `SKILL.md` — LLM skill guide at repo root for client discovery.
- `scripts/list-configurable-classes.sh` — query configurable ACI classes from jsonmeta
  schemas. Options: `--package`, `--exclude-rsrt`, `--count`.
- SPDX license headers (`Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl /
  SPDX-License-Identifier: AGPL-3.0-or-later`) on all Python source files.
- `[project.authors]`, `[project.urls]`, `classifiers`, `keywords` in both
  `mcp/pyproject.toml` and `schema-collector/pyproject.toml`.

### Changed

- Repository scope narrowed: lab orchestration tooling (`scripts/`, `Makefile`,
  `.env.example`) extracted to the separate `ai-netlab` repository. This repo now
  contains only the MCP server (`mcp/`) and the schema collector (`schema-collector/`).
- Build system removed: Nuitka/UPX standalone binary pipeline (`schema-collector/deploy/`)
  deleted. The schema collector is distributed as Python source only.
- `.gitignore` extended: `.claude/`, `.opencode/`, `.vscode/`, `CLAUDE.md`,
  `.markdownlint*`, `.lab*`.

---

## [0.3.0] - 2026-06-13

### Added

- `mcp/middleware/oauth.py` — `OAuthDiscoveryMiddleware`: intercepts
  `/.well-known/oauth-protected-resource` and `/.well-known/oauth-protected-resource/mcp`,
  returning RFC 9728 Protected Resource Metadata JSON. Prevents spec-compliant MCP clients
  (OpenCode, Claude Desktop) from crashing on a plain-text "Not Found" response.
- `mcp/middleware/auth.py` — `KeyStore`: thread-safe, hot-reloadable key container.
  `reload()` swaps the key set atomically; in-flight requests are unaffected.
- `mcp/middleware/auth.py` — `RateLimiter`: fixed-window per-IP limiter (default 30 attempts /
  60 s). Returns 429 with `Retry-After: 60` after threshold. Successful requests do not
  consume budget.
- 21 new unit tests for `middleware.auth` (KeyStore, RateLimiter, WWW-Authenticate, hot-reload).
  Total: 199 tests.

### Changed

- `mcp/middleware/auth.py` — `ApiKeyMiddleware` now takes a `KeyStore` instead of a raw
  `frozenset`; accepts an optional `RateLimiter`.
- `mcp/middleware/auth.py` — 401 responses include
  `WWW-Authenticate: Bearer resource_metadata="<url>"` per RFC 9728, so clients locate
  the discovery endpoint without probing multiple `/.well-known/` candidates.
- `mcp/main.py` — `ApiKeyMiddleware` is always added to the middleware stack (no-op when
  `KeyStore` is empty, eliminating the conditional branch). `OAuthDiscoveryMiddleware` added
  as outermost middleware in all modes.
- `mcp/main.py` — SIGHUP handler installed at startup: sends `SIGHUP` to reload
  `MCP_API_KEYS` from `.env` without restarting the server.

### Security

- Per-IP rate limiting on failed auth attempts prevents brute-force token enumeration.
- `WWW-Authenticate` header now hints at the discovery URL, allowing clients to complete
  the MCP 2025-03-26 OAuth discovery flow without exposing internal server details.

---

## [0.2.0] - 2026-06-13

### Added

- `mcp/middleware/auth.py` — `ApiKeyMiddleware`: Starlette `BaseHTTPMiddleware` validating
  `Authorization: Bearer <token>` or `X-API-Key: <token>` on every incoming request.
  Token comparison uses `hmac.compare_digest` to prevent timing-oracle attacks.
  No-op (with startup warning) when `MCP_API_KEYS` is unset — preserves local dev UX.
- `mcp/deploy/Caddyfile` — TLS-terminating reverse proxy; supports Let's Encrypt (public domain)
  and Caddy's internal CA (LAN/self-signed). Security headers included (`HSTS`, `X-Frame-Options`, …).
- `mcp/deploy/docker-compose.yml` — two-service production stack: `mcp` (internal only) + `caddy`
  (ports 80/443). MCP container is not exposed on the host — all traffic enters via Caddy.
- `mcp/exceptions.py` — `AuthenticationError` added to the exception hierarchy.
- `docs/` — project wiki: architecture diagrams (Mermaid), deployment guides, tools reference,
  internals documentation, and full settings reference.
- 26 new unit tests for `middleware.auth` (load, extract, validate, HTTP integration).

### Changed

- `mcp/pyproject.toml` — version bumped to `0.2.0`; all dependency version constraints
  now have explicit upper bounds.
- `mcp/main.py` — `_serve()` now conditionally applies `ApiKeyMiddleware` via
  `run_http_async(middleware=[...])` when `MCP_API_KEYS` is configured.

### Security

- All MCP endpoints are now protected behind API key authentication when `MCP_API_KEYS` is set.
- TLS is provided end-to-end by Caddy when deployed via `docker-compose.yml`.
- The MCP server port (`8000`) is never exposed directly to the host in the production stack.

---

## [0.1.0] - 2026-06-12

### Added

- `mcp/` — FastMCP server exposing three tools: `search_classes`, `get_schema`, `query`
- `mcp/apic/client.py` — APIC REST client with cookie auth and auto-reauth on 401/403
- `mcp/registry/` — lazy schema loading, keyword search, query-target-filter builder
- `mcp/deploy/Dockerfile` — container image (build context: repo root)
- `mcp/client/` — ready-made MCP client config and LLM skill doc
- `schema-collector/collect.py` — unified CLI (`aci-collect`) replacing four standalone scripts
  - `run` — full pipeline with `--from`, `--concurrency`, `--force`
  - `status` — rich table showing artifact state per APIC version
  - `clean` — remove generated artifacts, with optional `--version` targeting
- Versioned artifact layout: `cobra-sdk/{apic_version}/` and `mo-schemas/{apic_version}/`
- APIC version auto-detected via `firmwareCtrlrRunning` after authentication
- Shared `data/` at monorepo root — written by `schema-collector`, read by `mcp`

### Changed

- Monorepo structure: `mcp/` and `schema-collector/` as independent Python projects
- `class-descriptions.json` centralised in `data/` (was duplicated in both subprojects)

---

[Unreleased]: https://github.com/k3l0-dev/aci-mcp/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/k3l0-dev/aci-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/k3l0-dev/aci-mcp/releases/tag/v0.1.0
