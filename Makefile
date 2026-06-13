.PHONY: test test-mcp test-collect lint lint-mcp lint-collect \
        deploy deploy-prod mcp-deps help

MCP_DIR     := mcp
COLLECT_DIR := schema-collector

.DEFAULT_GOAL := help

# ── Help ──────────────────────────────────────────────────────────────────────

help:
	@printf '\n  aci-mcp — root Makefile\n\n'
	@printf '  %s\n' 'Deploy'
	@printf '  %-24s %s\n' 'make deploy'       'ensure .env + deps, then start mcp server (local)'
	@printf '  %-24s %s\n' 'make deploy-prod'  'start production stack via docker-compose (Caddy + MCP)'
	@printf '\n  %s\n' 'Tests'
	@printf '  %-24s %s\n' 'make test'         'run all tests (mcp + schema-collector)'
	@printf '  %-24s %s\n' 'make test-mcp'     'run mcp tests only'
	@printf '  %-24s %s\n' 'make test-collect' 'run schema-collector tests only'
	@printf '\n  %s\n' 'Lint'
	@printf '  %-24s %s\n' 'make lint'         'ruff check + fix both subprojects'
	@printf '  %-24s %s\n' 'make lint-mcp'     'ruff check + fix mcp/'
	@printf '  %-24s %s\n' 'make lint-collect' 'ruff check + fix schema-collector/'
	@printf '\n  %s\n' 'Build'
	@printf '  %-24s %s\n' 'make -C schema-collector build' 'compile aci-collect binary (OrbStack on macOS)'
	@printf '\n'

# ── Deploy — local ─────────────────────────────────────────────────────────────
#
# Dependency chain:
#   deploy → .env (file target: runs setup-env.py only when .env is absent)
#           → mcp-deps (always: uv sync is a no-op when lock file unchanged)
#           → launch server in foreground (Ctrl-C to stop)

deploy: .env mcp-deps
	@printf '\n── starting aci-mcp (local) ─────────────────────────────\n\n'
	cd $(MCP_DIR) && uv run python main.py

# File target: Make only runs this recipe when .env does not exist.
.env:
	@printf '\n── .env not found — running interactive setup ───────────\n\n'
	python scripts/setup-env.py

mcp-deps:
	@printf '\n── syncing mcp dependencies ─────────────────────────────\n'
	uv sync --project $(MCP_DIR)
	@printf '\n'

# ── Deploy — production ────────────────────────────────────────────────────────
#
# Requires MCP_DOMAIN and MCP_API_KEYS to be set in .env.
# Caddy obtains TLS certs automatically (Let's Encrypt or internal CA).

deploy-prod: .env
	@printf '\n── starting production stack (Caddy + MCP) ──────────────\n\n'
	docker compose -f $(MCP_DIR)/deploy/docker-compose.yml up -d
	@printf '\n'
	@docker compose -f $(MCP_DIR)/deploy/docker-compose.yml ps
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
