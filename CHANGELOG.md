# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

### Added

- `.github/dependabot.yml` schedules weekly version-update checks for the
  `mcp/` uv-managed dependencies and for GitHub Actions dependencies, on top
  of the security-only Dependabot alerts already active — so a vulnerable
  or outdated dependency surfaces on a schedule instead of only at the next
  manual audit.

### Fixed

- Documentation across `README.md` and `docs/` had drifted from the
  current implementation (mostly predating the `get_by_dn`/`count` tools,
  the `query()` envelope return, and the v2 search algorithm). Corrected,
  across architecture, internals, and tool-reference docs:
  - `query()`'s documented return shape — an envelope dict
    (`results`/`returned`/`total_available`/`truncated`/`next_page`/
    `complete`/`note`), not a bare list — including every stale code
    example built against the old shape.
  - `search_classes`'s scoring algorithm description, which still
    described the retired v1 substring scheme instead of the current
    tokenized/structural-priors v2 algorithm — including the live tool
    docstring in `main.py` itself, which is what an MCP client actually
    sees as the tool's capability description.
  - `get_schema()`'s schema-directory resolution: documented as a
    per-call glob fallback across versioned subdirectories, when
    resolution actually happens once at server startup.
  - `get_by_dn`/`count` missing from several tool-count diagrams and
    tables that still showed only 3 of the 5 tools.
  - Two invented ACI class names (`fvRsConsumedBrCP`/`fvRsProvidedBrCP`)
    in the object-model doc, replaced with the real ones (`fvRsCons`/`fvRsProv`).
  - The `ApicRequestError` exception and the retry/backoff behavior
    (exponential backoff, transient-status retries), missing from the
    documented exception hierarchy and client internals.
  - A security-relevant docstring inaccuracy in the API-key comparison
    (`middleware/auth.py`'s `_is_valid`): claimed comparison time is
    always independent of key position, which is false — `any()`
    short-circuits on the first match. Corrected the docstring and the
    two docs copying it, with the accurate (and lower-severity) framing:
    the leak only manifests once a token is already valid.
  - Smaller inaccuracies: wrong version badges (both READMEs), a wrong
    Docker volume-mount target (`/app/data` vs. the real `/data`), `.env`
    precedence documented backwards, a stale expected-log-output order in
    the quickstart, and quantitative errors in the object-model doc
    ("hundreds" vs. the real low-thousands counts for abstract/relation/
    configurable classes).
  - `registry/descriptions.py`'s own module docstring mislabeled the
    intermediate "Rs/Rt-penalty-only" measurements (Recall@1 28.2%) as
    "v1" — the real final v1 state (after the prop_labels axis was added)
    scored 30.8%, matching `search-algorithm.md`'s own numbers. The v2
    comparison in the docstring now uses the correct baseline.
  - The scoring tables in `search-algorithm.md` and `registry.md` folded
    the comment field's direct-substring-match branch (+2) into the same
    row as its squared-coverage fallback (+1) — split into two rows,
    matching how the property-label branch was already documented.

## [1.2.0] - 2026-07-20

### Added

- `mcp/client/SKILL.md` documents `dnFormats` — the full DN template chained
  from every ancestor's `rnFormat` — alongside the existing `rnFormat`
  coverage, with an explicit note that a repeated placeholder name (e.g.
  `{name}` appearing twice) reflects two ancestors sharing an identifying
  attribute and must be quoted verbatim, never renamed for readability.
- `ApicClient` retries a bounded number of times (default 3 attempts, small
  exponential backoff capped at 2s) on transient failures — connection
  errors/timeouts and HTTP 404/500/502/503/504 — before raising. A genuine
  application error (e.g. 400 for a malformed filter) is still raised
  immediately, never retried. Configurable via new `retry_attempts` /
  `retry_backoff_base` constructor arguments.
- `query()` gains `fetch_all=True` — walks every page (bounded by a safety
  cap of 25 pages / 5000 objects) and returns the complete matching set in
  one call. Closes a real gap where a default page's max/min/argmax over a
  class (e.g. "which bridge domain has the most subnets") could be silently
  wrong once the fabric grew past a single page. `count()`'s docstring and
  the server instructions now distinguish a pure tally (`count()`) from
  ranking/argmax (`fetch_all=True` plus local aggregation over `results`).

### Changed

- `apic/client.py` — `query_class()` now shares its transport/retry logic
  with `get_by_dn()`/`count_class()` via `_request_json()`, instead of
  duplicating the request-and-error-handling block. The 401/403
  re-authenticate-and-retry flow is unchanged, now isolated in a new
  `_send()` helper that the retry loop wraps.
- **Breaking:** `query()` now returns an envelope — `{results, returned,
  total_available, truncated, next_page, complete, note}` — instead of a
  bare list, so a partial page is never silently indistinguishable from a
  complete one. `total_available` is the APIC-reported true match count
  (previously parsed and discarded by `query_class()`); `truncated`/
  `complete` and an explicit `note` tell the caller when a max/min/total/
  all-of conclusion from the current response would be wrong.
  `mcp/client/SKILL.md`'s canonical response shape, pagination, and
  counting sections updated accordingly. Version bumped 1.1.0 → 1.2.0.

### Fixed

- `mcp/client/SKILL.md` and `main.py`'s server instructions now state
  explicitly that a tool error (unknown class, unreachable object, malformed
  filter) is a failed lookup, not an answer of zero or an empty result — and
  that every specific fact in a final answer (a property name, a configured
  value, a DN template, a count) must trace back to an actual tool result
  from the conversation rather than general ACI knowledge. Also fixes a
  stale row in SKILL.md's error-handling table that described a
  `{"error": ..., "closest_matches": [...]}` return shape `query` has never
  actually used — `UnknownClassError` is raised, not returned, matching the
  file's own `count` section a few paragraphs earlier.
- `mcp/client/SKILL.md` and `main.py` sharpen the grounding rule above with
  the two cases it's most often stretched past its evidence: `contains`
  lists child class names only, never what each one targets or means (that
  is `relationTo`'s job, and only for the Rs classes it lists); `properties`
  lists names only, never a property's type, default, or allowed values
  (that requires `property_details`). Naming a relation's target or a
  property's type/value without having actually requested that detail is
  the unsupported completion the rule forbids.

### Security

- Bumped locked dependencies to clear 36 known vulnerabilities flagged by
  the CI dependency audit across 10 packages (`fastmcp` 3.1.1→3.4.4, `mcp`
  1.26.0→1.28.1, `starlette` 1.0.0→1.3.1, `cryptography` 46.0.6→49.0.0,
  `authlib` 1.6.9→1.7.2, `click`, `idna`, `pydantic-settings`, `pyjwt`,
  `python-multipart`) — no direct dependency constraint changes, resolved
  within the existing `pyproject.toml` ranges via `uv lock --upgrade`.
- `pytest` bumped 8.4.2 → 9.1.1 (`pytest-asyncio` 0.26.0 → 1.4.0 for
  compatibility) to clear GHSA-6w46-j5rx-g56g (vulnerable tmpdir handling),
  a dev-only dependency vulnerability GitHub's Dependabot alerts caught but
  the CI dependency audit missed — that job ran `pip-audit` with `--no-dev`,
  excluding dev dependencies entirely. CI now audits the full dependency set
  including dev dependencies.

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

  | Metric   | Before | After |
  | -------- | ------ | ----- |
  | Recall@1 | 30.8%  | 78.4% |
  | Recall@5 | 53.8%  | 94.6% |

  See `docs/internals/search-algorithm.md` for the full mechanics.
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

[Unreleased]: https://github.com/k3l0-dev/aci-mcp/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/k3l0-dev/aci-mcp/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/k3l0-dev/aci-mcp/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/k3l0-dev/aci-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/k3l0-dev/aci-mcp/releases/tag/v0.1.0
