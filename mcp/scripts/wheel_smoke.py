"""Public-surface smoke against an *installed* niwashi-mcp wheel.

Run with the interpreter of a pristine venv where the wheel was installed —
never with the repository's own environment, which would import the source tree
and prove nothing about the artefact:

    uv venv /tmp/smoke
    uv pip install --python /tmp/smoke/bin/python dist/niwashi_mcp-*.whl
    /tmp/smoke/bin/python mcp/scripts/wheel_smoke.py

What it checks is what a consumer touches in their first five minutes, and what
the packaging could plausibly break without any unit test noticing:

  - the package imports at all, and from site-packages rather than a checkout;
  - the five tools are exported and callable;
  - the embedded catalogue is reachable and answers — the wheel is 50 KB, the
    data lives in the `niwaki` dependency, and a wrong pin or a missing
    dependency would only show up here;
  - none of the flat 1.x module names leaked into the root namespace;
  - the console entry point resolves.

Exits non-zero on the first failure.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "ok  " if condition else "FAIL"
    print(f"  [{mark}] {label}{f' — {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def main() -> None:
    print("niwashi-mcp wheel smoke")

    # 1. The package imports, and from an installed location.
    import niwashi_mcp

    origin = Path(niwashi_mcp.__file__).resolve()
    installed = "site-packages" in origin.parts or "dist-packages" in origin.parts
    check("imports from an installed location", installed, str(origin))

    # 2. The five tools exist on the server module. They are FastMCP-decorated,
    #    so they are objects rather than plain functions — presence is what
    #    matters here, behaviour is covered by the test suite.
    from niwashi_mcp import main as server

    for tool in ("search_classes", "get_schema", "query", "get_by_dn", "count"):
        check(f"tool exposed: {tool}", hasattr(server, tool))

    # 3. The catalogue answers. This is the one that fails if the niwaki pin is
    #    wrong, if the dependency did not install, or if the wheel shipped
    #    without its data layer.
    from niwashi_mcp.registry import catalog

    version = catalog.apic_version()
    check("catalogue reachable", catalog.catalog_path().is_file(), str(catalog.catalog_path()))
    check("catalogue declares an APIC version", bool(version) and version != "unknown", version)

    # The startup guard, against the niwaki the resolver actually chose. This is
    # the check that fails if a release moved the catalogue's private schema —
    # exactly the scenario the `niwaki>=1.8,<1.9` pin exists to prevent, and the
    # only place in this script where the *resolved* dependency is under test
    # rather than the one in the lockfile.
    try:
        catalog.verify_catalogue()
    except Exception as exc:  # any failure is a failed smoke — report, don't raise
        check("catalogue schema verifies", False, str(exc))
    else:
        check("catalogue schema verifies", True)

    schema = catalog.load_schema("fvBD")
    check("get_schema('fvBD') returns a schema", bool(schema))
    check(
        "the schema carries its DN template",
        schema.get("dnFormats") == ["uni/tn-{name}/BD-{name}"],
        str(schema.get("dnFormats")),
    )
    check("an unknown class returns {}", catalog.load_schema("fvNotARealClass") == {})
    check("class_exists is case-sensitive", catalog.class_exists("fvBD") and not catalog.class_exists("fvBd"))

    index = catalog.descriptions_index()
    check("search index builds", len(index) > 15_000, f"{len(index):,} classes")

    # 4. Search answers correctly end to end.
    from niwashi_mcp.registry.descriptions import search

    hits = search("bridge domain", index, limit=5)
    check(
        "search('bridge domain') ranks fvBD first",
        bool(hits) and hits[0]["class_name"] == "fvBD",
        hits[0]["class_name"] if hits else "no result",
    )

    # 5. No flat 1.x module leaked into the root namespace. Publishing the old
    #    layout would have claimed `exceptions` and `main` on PyPI; this asserts
    #    the src/ move actually prevents that in the built artefact.
    for leaked in ("main", "exceptions", "registry", "middleware", "apic"):
        try:
            importlib.import_module(leaked)
        except ImportError:
            check(f"root namespace clean: {leaked}", True)
        else:
            check(f"root namespace clean: {leaked}", False, "importable from the root")

    # 6. The console entry point resolves to something callable.
    check("entry point `main` is callable", callable(getattr(server, "main", None)))

    print()
    if FAILURES:
        print(f"FAILED — {len(FAILURES)}: {', '.join(FAILURES)}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
