# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

### Added

- `mcp/tests/integration/test_tool_client_wiring.py` — tool-layer wiring tests
  that call `query()`/`get_by_dn()`/`count()` against a *real* `ApicClient`
  (wired to the `FakeHTTPClient` recorder pattern from
  `tests/unit/test_client.py`) instead of `StubBackend`, and assert on the
  actual URL/params `ApicClient` builds. Closes a real gap: `StubBackend`
  reimplements filtering/scoping in plain Python and never calls
  `registry.filter.build_filter()`, so nothing previously proved that
  `page`, `rsp_subtree_include`, or `time_range` reach the real APIC request,
  or that an invalid filter attribute name raises `FilterError` through the
  tool layer. 14 new tests.
- `mcp/tests/live/` — end-to-end tests against a real Cisco APIC (the
  internal lab simulator), driven through the real `ApicClient` with no
  stubs or fakes at all: `query_class` for `fvTenant`/`fvBD`, `get_by_dn`
  (found and not-found cases), `count_class`, `query_class(config_only=True)`
  attribute-set reduction, and a bad `filter_expr` asserting
  `ApicRequestError` carries the APIC's own non-empty error text. The
  session-scoped `live_client` fixture (`tests/live/conftest.py`)
  authenticates from the repo-root `.env` exactly like `main.py`'s
  `app_lifespan`, and auto-skips (never fails) the whole session when the
  simulator is unreachable. Marked `@pytest.mark.live` and excluded from the
  default `uv run pytest` run; run explicitly with
  `uv run pytest tests/live/ -m live`. 7 new tests, verified passing against
  the real simulator.
- `mcp/pyproject.toml` — registers the `live` pytest marker and defaults
  `addopts` to `-m "not live"`, so a plain `uv run pytest` in any
  environment (including a public CI runner with no route to the internal
  lab) never attempts to reach the simulator.
- `mcp/tests/__init__.py` — module docstring documenting the five test
  categories now in place (`unit/`, `integration/`, `live/`, `perf/`,
  `eval/`) and why `live/` is intentionally excluded from default CI.
- `registry/descriptions.py` — `search_classes` rewritten from raw substring
  matching to tokenized, camelCase-aware scoring: exact label/class-name
  matches dominate, token coverage rewards queries that name most of a
  concept, and property-label phrase matches surface functional queries
  (e.g. "ARP flooding" → fvBD). Structural priors (`isConfigurable` boost,
  `isAbstract`/stats-suffix/Rs-Rt penalties) replace v1's flat -3 Rs/Rt
  penalty, using the same detection plus two flags now carried into
  `class-descriptions.json`. A small curated ACI jargon/synonym table closes
  gaps that have no textual anchor in the schema (e.g. `bgpPeerP`'s real
  label is "Peer Connectivity Profile" — nowhere does it say "BGP peer").
  Measured: Recall@1 30.8% → 78.4%, Recall@5 53.8% → 94.6% (golden set also
  grew 39 → 74 queries alongside the rewrite; see
  `docs/internals/search-algorithm.md` section 6 for the full mechanics).
- `data/class-descriptions.json` — regenerated with `isConfigurable`/
  `isAbstract` flags per class (omitted when `False`, matching the file's
  existing sparse-field convention), feeding the new search priors above.
  Zero classes lost, zero regressions on existing `label`/`comment`/
  `prop_labels` fields (verified by diff against the prior file); 87
  additional bare classes surfaced that previously had none of those three
  fields but do carry one of the new flags.
- `mcp/tests/eval/test_search_quality.py` — runs the golden-set evaluation as
  a pytest test with a floor on Recall@1 (60%) and Recall@5 (85%), so a
  search-quality regression fails CI instead of only showing up if someone
  remembers to run `tests/eval_search.py` by hand.
- `mcp/tests/fixtures/search_golden.json` — grown from 39 to 74 queries
  across all four tiers, for breadth rather than to flatter the new scoring.
- `get_by_dn(dn, config_only, include_children)` — new MCP tool that fetches a
  single object directly by its Distinguished Name (`GET /api/mo/{dn}.json`),
  the shortcut path when the exact DN is already known. Returns a structured
  `{"found": false, ...}` message for a missing DN instead of a bare `[]`.
- `count(class_name, filters, scope_dn, filter_expr)` — new MCP tool that counts
  objects of a class via APIC `rsp-subtree-include=count` without transferring
  them. Validates the class name against the registry like `query` (raises
  `UnknownClassError` with suggestions).
- `get_schema` now returns `contains` — a sorted list of the child class names an
  object may hold, in flat notation ready to feed to `get_schema`/`query`/
  `include_children` (the jsonmeta `contains` field was previously dropped).
- `get_schema` gains `include_property_details` and `properties_filter`
  parameters exposing a compact per-property constraint dict (`type`, `access`,
  `naming`, `mandatory`, `default`, `options`, `comment`). Opt-in for token
  economy — request details only for the properties you intend to set.
- `query` and `get_by_dn` gain `config_only` — adds `rsp-prop-include=config-only`
  so only user-configurable attributes are returned, dropping operational noise
  for comparison, drift detection, and backup.
- `ApicClient.get_by_dn()`, `ApicClient.count_class()`, and a shared
  `_request_json()` helper backing the new tools.
- `mcp/exceptions.py` — `ApicRequestError`: wraps non-2xx, non-authentication APIC
  responses (400 for a malformed `filter_expr`, 404, 500, ...), carrying the HTTP
  status and, when present, the APIC-supplied error text from
  `imdata[0].error.attributes.text`. Previously these escaped as raw
  `httpx.HTTPStatusError`.
- `mcp/registry/schema.py` — `resolve_schemas_dir()`: resolves the actual jsonmeta
  schema directory (flat vs. versioned subdir, e.g. `mo-apic-v6.0_9c/`) once at
  server startup, so `load_schema()` never has to scan for it per call.
- `mcp/registry/schema.py` — `class_exists()`: verifies a schema-file match
  against the class's own `classPkg`/`className` fields (sourced from the JSON
  content, not the filesystem path), so the `query()` registry/schema fallback
  below cannot be fooled by a case-insensitive filesystem (the macOS/Windows
  default) silently resolving a typo to the real file.
- Boundary tests (0, -1, 1, cap, cap+1) for the `limit` parameter of both
  `search_classes` and `query`.
- Tests covering the `query()` registry/schema fallback (class present in the
  schema collection but absent from `class-descriptions.json`), the new
  `ApicRequestError` paths (400 with/without an APIC error body, 500), and
  `class_exists()`'s case-sensitivity guard.
- 25 tests: 12 unit tests for the schema `contains`/`property_details`
  projections and 13 integration tests for `get_by_dn`, `count`, and
  `config_only`.

### Changed

- FastMCP `instructions` and `mcp/client/SKILL.md`: the mandatory
  `search_classes → get_schema → query` sequence is now scoped to *discovery*,
  with documented shortcuts for the known-DN path (`get_by_dn`), counting, and
  config-only reads, plus guidance on `contains` and property details.
- `mcp/client/SKILL.md`: added an eventual-consistency warning — reads taken
  right after a large config push reflect the fabric state at that instant;
  counts can move for a few seconds while the fabric materialises the change.
- `mcp/registry/schema.py` — `load_schema()` no longer performs a
  `schemas_dir.glob(f"*/{class}.json")` scan when the flat top-level path misses.
  It now does a single direct file stat on the *resolved* directory handed to it,
  eliminating a full scandir of the 15k+-entry schema tree on every `get_schema()`
  call. Callers must resolve `schemas_dir` once via `resolve_schemas_dir()` (done
  in `main.app_lifespan` at startup) before passing it in.
- `mcp/main.py` — `query()` no longer rejects a class outright just because it is
  absent from `class-descriptions.json`: it now falls back to `class_exists()`
  before raising `UnknownClassError`, closing a ~300-class gap between the two
  collections built by separate schema-collector passes. The fallback path logs
  a warning instead of failing silently either way.
- `mcp/main.py` — `search_classes` and `query` now clamp `limit` to `max(1, min(limit, cap))`
  instead of `min(limit, cap)`, so a zero or negative `limit` can no longer reach
  the APIC as an invalid `page-size` parameter.
- `mcp/registry/descriptions.py` — `search()` clamps a non-positive `limit` to 1
  before slicing results, guarding against silent mis-slicing on a negative value.
- `mcp/tests/perf/conftest.py` — `generate_schema_files()` now writes synthetic
  schema files into a versioned subdirectory (matching the real
  `data/schemas/mo-apic-v6.0_9c/` layout) instead of flat at the fixture's top
  level, so `tests/perf/test_schema_perf.py` actually exercises the hot path this
  suite exists to guard.
- `mcp/pyproject.toml` — moved `pytest` and `pytest-asyncio` from
  `[project.optional-dependencies]` into `[dependency-groups].dev`. They were
  previously only installed via `uv sync --extra dev`; the documented `uv sync`
  workflow (and CI's `uv sync --frozen --dev`) silently skipped them, so every
  async test failed with "async def functions are not natively supported."
- `schema-collector`'s `_step_descriptions` now also extracts `isConfigurable`/
  `isAbstract` from each class's jsonmeta root into `class-descriptions.json`
  (see Added, above).
- `tests/fixtures/search_golden.json` — corrected the "QoS class" tier-3 entry:
  it previously expected `vzBrCP` (a contract has a QoS-class property, but so
  does `fvAEPg`), when the unambiguous, directly-named answer is `qosClass`
  itself — a real, configurable class literally called "QoS Class Policy".

### Fixed

- `get_schema()` cold-load latency: removed the per-call wildcard scan described
  above (measured ~8.3 ms per call against the real 15,452-file schema collection,
  against a documented < 5 ms budget).
- A negative or zero `limit` on `search_classes`/`query` no longer reaches the
  APIC as `page-size=-1`/`0`, nor silently mis-slices `registry.descriptions.search()`'s
  result list.
- The `query()` registry/schema fallback could be tricked by a typo (e.g.
  `fvBd` for `fvBD`) into passing a bogus class through as valid, because a
  case-insensitive filesystem resolves the wrong file to the same path. Fixed
  by comparing the schema's own `classPkg`/`className` content instead of
  trusting the filesystem lookup alone.

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

[Unreleased]: https://github.com/k3l0-dev/aci-mcp/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/k3l0-dev/aci-mcp/releases/tag/v0.1.0
