.PHONY: test test-mcp test-collect lint lint-mcp lint-collect help

MCP_DIR     := mcp
COLLECT_DIR := schema-collector

.DEFAULT_GOAL := help

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@printf '\n  aci-mcp — root Makefile\n\n'
	@printf '  %-22s %s\n' 'make test'         'run all tests (mcp + schema-collector)'
	@printf '  %-22s %s\n' 'make test-mcp'     'run mcp tests only'
	@printf '  %-22s %s\n' 'make test-collect' 'run schema-collector tests only'
	@printf '  %-22s %s\n' 'make lint'         'ruff check + fix both subprojects'
	@printf '  %-22s %s\n' 'make lint-mcp'     'ruff check + fix mcp/'
	@printf '  %-22s %s\n' 'make lint-collect' 'ruff check + fix schema-collector/'
	@printf '\n  Subproject build targets:\n'
	@printf '  %-22s %s\n' 'make -C mcp ...'         'pass any target to mcp/Makefile (if present)'
	@printf '  %-22s %s\n' 'make -C collect build'   'build aci-collect binary (see schema-collector/Makefile)'
	@printf '\n'

# ── Tests ─────────────────────────────────────────────────────────────────────

test: test-mcp test-collect

test-mcp:
	@printf '\n── mcp ──────────────────────────────────────────────────\n'
	uv run --project $(MCP_DIR) pytest $(MCP_DIR)/tests/ -q
	@printf '\n'

test-collect:
	@printf '\n── schema-collector ─────────────────────────────────────\n'
	@if [ -d "$(COLLECT_DIR)/tests" ]; then \
		uv run --project $(COLLECT_DIR) pytest $(COLLECT_DIR)/tests/ -q; \
	else \
		printf '  (no tests)\n'; \
	fi
	@printf '\n'

# ── Lint ──────────────────────────────────────────────────────────────────────

lint: lint-mcp lint-collect

lint-mcp:
	@printf '\n── lint mcp ─────────────────────────────────────────────\n'
	(cd $(MCP_DIR) && uvx ruff check --fix .)
	@printf '\n'

lint-collect:
	@printf '\n── lint schema-collector ────────────────────────────────\n'
	(cd $(COLLECT_DIR) && uvx ruff check --fix collect.py)
	@printf '\n'
