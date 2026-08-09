# Contributing to niwashi-mcp

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
- **Request an APIC release** — running something other than the shipped
  6.0(9c)? Add a row to [`SUPPORTED-APIC.md`](SUPPORTED-APIC.md). One line, no
  data from your fabric. See [APIC releases](#apic-releases) below.
- **Submit a pull request** — see the workflow below.

Not sure where to start? Look for issues labelled
[`good first issue`](https://github.com/k3l0-dev/niwashi-mcp/labels/good%20first%20issue).

---

## Development setup

```bash
git clone https://github.com/k3l0-dev/niwashi-mcp.git
cd niwashi-mcp/mcp
uv sync
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
uv run ruff check --fix .
```

`ruff check` is what CI enforces, and the rule selection is pinned deliberately
in `pyproject.toml` so an upstream release cannot change what passes. **Do not
run `ruff format`** — this codebase is not formatter-managed, and running it
today would reformat 30 of 59 files and bury your change in unrelated diff.

**Tests:**

```bash
uv run pytest -q
```

That is the whole suite — 539 tests. `tests/unit/` alone is 434 of them, so
running only that skips the tool-level integration tests, the search-quality
gate, and the recorded behavioural baseline. Live tests (`tests/live/`) need a
reachable APIC and are excluded by default.

New behaviour must be covered by tests, and PRs that reduce coverage will be
asked to add tests before merge. One local convention beyond that: **a new guard
is not trusted until it has been broken on purpose.** If your change adds a
check, sabotage it, confirm the test you wrote fails, restore, and say so in the
pull request — a green suite is not evidence that a new guard works.

**Style:**
- Type hints on all function signatures
- Docstring on every public function and module
- No commented-out code, no `print` statements

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

- [ ] `uv run ruff check .` passes cleanly
- [ ] `uv run pytest` passes — state the count
- [ ] New Python files include the SPDX license header
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] PR description explains **why**, not only what

---

## APIC releases

The ACI object model does not live in this repository. It ships inside the
`niwaki` dependency as one SQLite catalogue, currently **APIC 6.0(9c)** — see
[`SUPPORTED-APIC.md`](SUPPORTED-APIC.md) for what that means if your fabric runs
something else.

Because of that, a pull request here cannot add support for a release on its
own; the catalogue is built and published in `niwaki`. What a pull request here
does is **register the demand**, which is what decides the order releases are
built in.

You are never asked to run a collection script against a production controller,
or to send schema files. Cisco publishes model metadata for the 5.2, 6.0 and 6.1
trains itself.

---

## License

By contributing, you agree your code is released under the
[PolyForm Noncommercial License 1.0.0](LICENSE). The copyright notice
`Khalid El-Ouiali — MONARK AIOPS srl` is retained in all copies
and derivative works as required by the license.

For commercial licensing inquiries:
[monark.aiops@pm.me](mailto:monark.aiops@pm.me)

---

## Questions

Open a [GitHub Discussion](https://github.com/k3l0-dev/niwashi-mcp/discussions)
for anything not covered here.
