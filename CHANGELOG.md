# Changelog

All notable changes to this project will be documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) — versioning follows [SemVer](https://semver.org/).

---

## [Unreleased]

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
- Shared `.env` at monorepo root — single credentials file for both projects
- `LICENSE` — proprietary license, copyright Khalid El-Ouiali — MONARK AIOPS srl

### Changed

- Monorepo structure: `mcp/` and `schema-collector/` as independent Python projects
- `class-descriptions.json` centralised in `data/` (was duplicated in both subprojects)

---

[Unreleased]: https://gitlab.com/monark-aiops-group/aci-mcp/-/compare/v0.1.0...HEAD
[0.1.0]: https://gitlab.com/monark-aiops-group/aci-mcp/-/tags/v0.1.0
