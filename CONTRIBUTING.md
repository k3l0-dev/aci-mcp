# Contributing to aci-mcp

Contributions are welcome — bug reports, documentation improvements,
and pull requests alike. This guide explains how to get started.

---

## Ways to contribute

- **Report a bug** — open an issue with steps to reproduce and the
  expected vs. actual behaviour.
- **Suggest a feature** — open an issue with the `enhancement` label.
  Discuss before implementing to avoid wasted effort.
- **Improve documentation** — typos, clarifications, examples — all
  appreciated. No issue needed for small fixes.
- **Submit a pull request** — see the workflow below.

Not sure where to start? Look for issues labelled
[`good first issue`](https://github.com/monark-aiops/aci-mcp/labels/good%20first%20issue).

---

## Development setup

```bash
git clone https://github.com/monark-aiops/aci-mcp.git
cd aci-mcp/mcp
uv sync --extra dev
cp ../.env.example ../.env   # fill in APIC_HOST, APIC_USER, APIC_PASSWORD
```

The Cisco DevNet [Always-On ACI sandbox](https://devnetsandbox.cisco.com)
is a free APIC instance you can use for testing without hardware.

---

## Workflow

Trunk-based branching — all work branches from `main`:

```
feature/<slug>   new features and improvements
hotfix/<slug>    critical fixes
```

1. Fork and create your branch from `main`.
2. Make changes, add or update tests.
3. Lint and test locally (see below).
4. Open a pull request against `main`.

---

## Code standards

**Linting** — must pass before every commit:

```bash
cd mcp
uvx ruff check --fix .
uvx ruff format .
```

**Tests:**

```bash
uv run pytest tests/unit/ -q
```

New behaviour must be covered by tests. PRs that reduce coverage will
be asked to add tests before merge.

**Style:**
- Type hints on all function signatures
- Docstring on every public function and module
- No commented-out code, no `print` statements

**License header** — every new Python file must start with:

```python
# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later
```

---

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat(registry): add fuzzy matching fallback for search_classes
fix(client): retry on APIC 503 with exponential backoff
docs: clarify get_schema identifiedBy field
chore: bump fastmcp to 3.2.0
```

---

## Pull request checklist

- [ ] `ruff check` passes cleanly
- [ ] Tests pass (`pytest tests/unit/`)
- [ ] New Python files include the SPDX license header
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] PR description explains **why**, not only what

---

## License

By contributing, you agree your code is released under
[AGPL v3](LICENSE). The copyright notice
`Khalid El-Ouiali — MONARK AIOPS srl` is retained in all derivative
works as required by the license.

For commercial licensing inquiries:
[monark.aiops@pm.me](mailto:monark.aiops@pm.me)

---

## Questions

Open a [GitHub Discussion](https://github.com/monark-aiops/aci-mcp/discussions)
for anything not covered here.
