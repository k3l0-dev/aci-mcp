<!--
Keep this short. A PR that explains itself in five lines is easier to review,
and easier to trust, than one that fills every heading.
Delete any section that genuinely does not apply — an empty heading says nothing.
-->

## What, and why

<!-- The *why* is the part a reviewer cannot reconstruct from the diff.
     What was wrong, or what became possible. -->

## What was measured

<!-- This project states numbers with their provenance: the command, the machine,
     the date. If nothing was measured, write "nothing to measure" and say why —
     that is a fine answer for a docs or rename change, and a poor one for
     anything touching search scoring, the catalogue, latency, or memory. -->

## How it is proven

<!-- Which tests cover this, and what would fail without the change.
     If this PR adds a guard, break it deliberately and say which test caught it.
     "Tests pass" is not evidence a new guard works — only a failing mutant is. -->

## What an agent sees differently

<!-- The product surface is what an LLM receives. Tick anything this PR changes,
     because a silent change here is the failure mode this server exists to
     prevent — the APIC does not error on a wrong class name, it returns nothing. -->

- [ ] A tool signature, parameter, default, or clamp
- [ ] The shape of a tool's response
- [ ] `get_schema` output, or the catalogue behind it
- [ ] Search ranking
- [ ] The server `instructions` block in `main.py`, or `mcp/client/SKILL.md`
- [ ] None of the above

## Breaking?

<!-- Who breaks, and what they do about it. Delete if nothing breaks. -->

---

- [ ] `uv run ruff check .` clean
- [ ] `uv run pytest` green — say the count
- [ ] New Python files carry the SPDX header
- [ ] `CHANGELOG.md` updated under `[Unreleased]`, in the house voice: what changed, why, and what it was measured against
