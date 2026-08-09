# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
Tests for the prompt surface — the text this server ships to every agent.

Why this file exists
--------------------
Roughly 7,800 words reach an LLM on a normal session: the `mcp.instructions`
block, the five tool docstrings FastMCP forwards as tool descriptions, and
`client/SKILL.md`. Nothing imported any of it. Line coverage cannot help —
`instructions` is a single string literal, so `main.py` reported 98 % while its
largest agent-facing artifact was unexamined.

It had already rotted. Until this file existed, `get_schema`'s docstring told
every agent it read "the APIC jsonmeta schema file" and returned `{}` when "the
class file is not found in the local schema collection". Neither has existed
since 2.0 — the object model is a SQLite catalogue inside a dependency. And
`property_details` was documented as carrying a `mandatory` key that **zero of
332,297 properties** in the shipped catalogue can produce.

Documentation that lies to a human wastes their afternoon. Documentation that
lies to an agent becomes the agent's model of the system, and this whole server
exists to stop an agent answering confidently from a wrong model.

These tests pin the claims against the code that has to honour them. They are
deliberately narrow: they check facts that can be mechanically compared, not
prose quality.
"""

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

from niwashi_mcp import main as main_mod
from niwashi_mcp.registry import catalog

pytestmark = pytest.mark.catalog

_TOOLS = ("search_classes", "get_schema", "query", "get_by_dn", "count")
_SKILL = Path(__file__).resolve().parents[2] / "client" / "SKILL.md"


def _doc(name: str) -> str:
    return inspect.getdoc(getattr(main_mod, name)) or ""


def _all_prompt_text() -> str:
    return main_mod.mcp.instructions + "\n" + "\n".join(_doc(t) for t in _TOOLS)


# ── the data layer the docs describe ─────────────────────────────────────────


def test_no_prompt_text_claims_the_server_reads_files_from_disk():
    """The 2.0 data layer is a catalogue in a dependency, not a directory.

    Any of these words reaching an agent gives it a wrong model of where its
    answers come from — and one that suggests a fix (point me at the files)
    that does not exist.
    """
    text = _all_prompt_text().lower()
    for phrase in ("jsonmeta", "schema file", "schema collection", "schemas dir", "data directory"):
        assert phrase not in text, (
            f"the prompt surface still tells an agent about {phrase!r}; "
            f"the object model has been a SQLite catalogue since 2.0"
        )


def test_skill_file_does_not_describe_a_schema_directory():
    """Same rule for SKILL.md, which is the larger half of the surface."""
    text = _SKILL.read_text().lower()
    for phrase in ("jsonmeta", "data/schemas", "schema collection"):
        assert phrase not in text, f"SKILL.md still refers to {phrase!r}"


# ── documented output fields must be producible ──────────────────────────────


def test_every_property_detail_key_documented_can_actually_appear():
    """A documented field an agent will never see teaches it to look for nothing.

    `mandatory` was documented on `property_details` and is unreachable: the
    flag bit exists in niwaki's layout but no property in the shipped catalogue
    sets it. Kept as a test rather than deleted from the projection, so that a
    future catalogue which *does* set it fails here and gets re-documented
    instead of silently appearing.
    """
    bits = catalog._flag_bits()
    total = catalog._query("SELECT COUNT(*) FROM prop")[0][0]
    with_bit = catalog._query(
        "SELECT COUNT(*) FROM prop WHERE flags & ?", (bits["mandatory"],)
    )[0][0]

    doc = _doc("get_schema")
    if with_bit == 0:
        assert '"mandatory"' not in doc, (
            f"get_schema documents a `mandatory` key, but 0 of {total} properties "
            f"in this catalogue set the bit — an agent is told to expect a field "
            f"it can never receive"
        )
    else:
        assert '"mandatory"' in doc, (
            f"{with_bit} of {total} properties now carry `mandatory` and it is "
            f"projected, but get_schema no longer documents it"
        )


# ── numbers quoted to the agent must match the code ──────────────────────────


def test_the_clamps_quoted_to_the_agent_match_the_code():
    """`query` and `search_classes` promise specific ranges; the code enforces them.

    These bounds are the only reason an agent can reason about how much it will
    get back. A change on either side without the other is the server describing
    a contract it does not keep.
    """
    src = inspect.getsource(main_mod)
    assert "max(1, min(limit, 200))" in src, "query's clamp moved"
    assert "max(1, min(limit, 50))" in src, "search_classes' clamp moved"
    assert "[1, 200]" in _doc("query")
    assert "[1, 50]" in _doc("search_classes")


def test_the_schema_list_bounds_quoted_match_the_constants():
    """`list_limit`'s default and ceiling are stated in prose and in code."""
    doc = _doc("get_schema")
    # Assert the numbers, not a phrasing — the prose may be reworded, the
    # contract may not change silently underneath it.
    assert f"default {main_mod._SCHEMA_LIST_SAMPLE}" in doc, "the default moved"
    assert f"1..{main_mod._SCHEMA_LIST_MAX}" in doc, "the ceiling moved"
    assert f"max {main_mod._SCHEMA_LIST_MAX}" in inspect.getsource(main_mod), (
        "the *Truncated note no longer tells the agent the ceiling"
    )


def test_the_envelope_keys_promised_are_the_keys_returned():
    """Every key `query` names in its docstring is one the tool actually sets.

    An agent reads this list and writes code against it. A renamed key that the
    docstring still advertises is a silent breakage in the caller, not here.
    """
    doc = _doc("query")
    returned = {
        "results", "returned", "total_available",
        "truncated", "next_page", "complete", "note",
    }
    for key in returned:
        assert f'"{key}"' in doc, f"query returns {key!r} but never documents it"

    # Look at the code with the docstring removed. The docstring spells out the
    # same `"key": value` shapes, so scanning the raw source would let the
    # documentation satisfy an assertion about the implementation — the test
    # would pass while the two disagreed, which is the one thing it exists to
    # catch. `inspect.getdoc` will not do for the strip: it returns the cleaned,
    # dedented text, which does not occur verbatim in the source.
    fn = ast.parse(textwrap.dedent(inspect.getsource(main_mod.query))).body[0]
    if isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
        fn.body = fn.body[1:]
    body = ast.unparse(fn)
    for key in returned:
        assert f"'{key}':" in body or f'"{key}":' in body, (
            f"query documents {key!r} but never sets it"
        )


# ── the skill must describe the tools that exist ─────────────────────────────


def test_skill_names_exactly_the_tools_the_server_exposes():
    text = _SKILL.read_text()
    for tool in _TOOLS:
        assert f"`{tool}`" in text, f"SKILL.md never mentions {tool}"


def test_skill_invents_no_tool_parameter():
    """Every `name=` in a SKILL.md tool call must be a real parameter.

    A kwarg the server does not accept is an error the agent cannot diagnose:
    it looks like the server rejecting a reasonable request.
    """
    import re

    text = _SKILL.read_text()
    real = {
        tool: set(inspect.signature(getattr(main_mod, tool)).parameters)
        for tool in _TOOLS
    }
    bad: list[str] = []
    for tool in _TOOLS:
        for call in re.findall(rf"{tool}\(([^)]*)\)", text):
            for kwarg in re.findall(r"(\w+)\s*=", call):
                if kwarg not in real[tool]:
                    bad.append(f"{tool}({kwarg}=…)")
    assert not bad, f"SKILL.md passes parameters that do not exist: {sorted(set(bad))}"


# ── the vocabulary lint ───────────────────────────────────────────────────────
#
# SKILL.md teaches by example, and its examples are full of class and property
# names. A typo'd class name in a teaching example is worse than one in code:
# code fails, but an agent imitates the example verbatim, queries the mistyped
# name, and the APIC answers an empty result indistinguishable from "none".
# The docs went stale exactly this way once already ("the APIC jsonmeta schema
# file"); names rot the same way words do.


def test_every_aci_name_skill_teaches_actually_exists():
    """Every backticked camelCase token is a class, a property, or allowlisted.

    Checked against the shipped catalogue itself, so the lint follows the
    corpus: a class SKILL.md cites that disappears from a future catalogue
    fails here the day the dependency is bumped, not the day a user's query
    comes back empty.
    """
    import re

    text = _SKILL.read_text()
    candidates = set(re.findall(r"`([a-z][a-z0-9]*[A-Z][A-Za-z0-9]*)`", text))
    assert len(candidates) > 30, (
        f"only {len(candidates)} camelCase tokens found — the extraction regex "
        f"no longer matches how SKILL.md marks names, so the lint is blind"
    )

    classes = {r[0] for r in catalog._query("SELECT class_name FROM mo")}
    properties = {r[0] for r in catalog._query("SELECT DISTINCT wire_name FROM prop")}

    # Every entry must carry its reason, and — asserted below — must still be
    # in use. An allowlist that only grows is how a lint stops linting.
    allowlist = {
        # get_schema's own output keys, quoted when teaching the schema shape:
        "identifiedBy", "rnFormat", "dnFormats", "containedBy", "relationTo",
        "relationFrom", "isAbstract", "sourceClass",
        # the *Truncated marker get_schema adds beside a sampled list:
        "containedByTruncated",
        # deliberate wrong-case example teaching that lookup is case-sensitive:
        "fvBd",
        # notation for colon-flattening ("`pkg:Class` → `pkgClass`"), not a name:
        "pkgClass",
    }

    unknown = sorted(
        c for c in candidates
        if c not in classes and c not in properties and c not in allowlist
    )
    assert not unknown, (
        f"SKILL.md teaches names that are neither a class nor a property in the "
        f"shipped catalogue: {unknown}. An agent will imitate them verbatim and "
        f"read the empty result as 'there are none'."
    )

    stale = sorted(a for a in allowlist if a not in candidates)
    assert not stale, (
        f"allowlist entries no longer used by SKILL.md: {stale} — prune them, "
        f"or the allowlist rots into a bypass"
    )


def test_the_wrong_case_example_is_still_wrong():
    """`fvBd` is allowlisted as a deliberate error. Verify it stays one.

    If a future catalogue ever contained a real `fvBd`, the teaching example
    would silently become a valid class and the case-sensitivity lesson would
    teach the opposite of what it says.
    """
    from niwashi_mcp.registry.catalog import class_exists

    assert not class_exists("fvBd")
    assert class_exists("fvBD")
