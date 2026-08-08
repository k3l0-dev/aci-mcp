# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Reference projection of raw jsonmeta — a test oracle, not production code.

This is the jsonmeta reader that served ``get_schema()`` up to 1.2.2, lifted
verbatim into the test tree when 2.0 replaced it with the catalogue adapter.
It is kept for one reason: it lets the parity tests derive the expected output
**independently**, from the vendor's own files, instead of comparing against a
snapshot this project recorded of itself.

The distinction matters. A recorded snapshot proves "nothing changed since I
wrote it down". An independent oracle proves "the output is correct", and would
still fail if the recording and the implementation were wrong in the same way.

Paired with ``tests/fixtures/jsonmeta/`` — 31 frozen APIC 6.0(9c) class files,
2.4 MB — this keeps catalogue parity automatically testable after ``data/`` is
gone. The seven high-cardinality classes are excluded from the fixture (95 MB
between them) and covered instead by the counts and digests in
``tests/baseline/baseline.json``.

**Do not import this from ``src/``.** It exists to disagree with the
implementation, which it cannot do if it becomes part of it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_SCALAR_KEYS = {
    "identifiedBy",
    "rnFormat",
    "containedBy",
    "dnFormats",
    "isAbstract",
    "isConfigurable",
    "className",
    "classPkg",
    "label",
}


def _project_property(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a raw jsonmeta property definition to a compact constraint dict.

    A raw per-property entry in a jsonmeta file carries ~25 fields
    (propGlobalId, propLocalId, uitype, validators, platformFlavors, …), most of
    which are irrelevant to an agent that only needs to build a valid config.
    This keeps just the fields that answer "what may I set here, and how":

      type      — the ACI model type, e.g. "scalar:Bool", "fv:RouteScp",
                  "naming:Name" (falls back to the primitive baseType)
      access    — write mode collapsed from the read/write/create flags:
                  "read-write", "create-only" (immutable after creation), or
                  "read-only" (never settable — internal / operational state)
      naming    — present and True when the property is part of the DN/RN
                  (an identifier — set at creation, immutable)
      mandatory — present and True when the property is required on create
      default   — the default value, when the schema declares one
      options   — allowed enumeration values (the human-usable localName of each
                  validValue, e.g. ["private", "public", "shared"] for a scope);
                  these are the exact strings the APIC accepts in filters/config
      comment   — one-line human description of the property

    Only `type` and `access` are always present; every other key appears solely
    when the schema declares it, to keep the per-property footprint minimal.

    Args:
        raw: The raw property definition dict from a jsonmeta `properties` entry.

    Returns:
        Compact per-property dict as described above.
    """
    detail: dict[str, Any] = {}

    # type — prefer the semantic ACI model type, fall back to the primitive base.
    model_type = raw.get("modelType") or raw.get("baseType")
    if model_type:
        detail["type"] = model_type

    # access — collapse isConfigurable / readOnly / createOnly / readWrite into a
    # single mode the agent can reason about directly.
    configurable = raw.get("isConfigurable", True)
    if not configurable or raw.get("readOnly"):
        access = "read-only"
    elif raw.get("createOnly"):
        access = "create-only"
    elif raw.get("readWrite"):
        access = "read-write"
    elif raw.get("isNaming"):
        # Naming properties carry no explicit read/write flag — they are set via
        # the DN at creation and are immutable thereafter.
        access = "create-only"
    else:
        access = "read-only"
    detail["access"] = access

    if raw.get("isNaming"):
        detail["naming"] = True

    if raw.get("mandatory"):
        detail["mandatory"] = True

    default = raw.get("default")
    if default not in (None, ""):
        detail["default"] = default

    # options — the localName of each enum value, minus the "defaultValue" marker
    # entry (whose localName duplicates the default). Order is preserved from the
    # schema and de-duplicated defensively.
    seen: set[str] = set()
    options: list[str] = []
    for valid_value in raw.get("validValues") or []:
        local_name = valid_value.get("localName")
        if local_name and local_name != "defaultValue" and local_name not in seen:
            seen.add(local_name)
            options.append(local_name)
    if options:
        detail["options"] = options

    # comment — jsonmeta stores the description as a list of lines; the sentinel
    # string "null" means "no comment" and is dropped.
    comment = raw.get("comment")
    if isinstance(comment, list):
        comment = " ".join(c for c in comment if c and c != "null").strip()
    if comment and comment != "null":
        detail["comment"] = comment

    return detail


FIXTURE_DIR = Path(__file__).resolve().parent / "jsonmeta"


def project(
    class_name: str,
    include_property_details: bool = False,
    properties_filter: list[str] | None = None,
    fixture_dir: Path | None = None,
) -> dict[str, Any]:
    """Project a frozen jsonmeta file exactly as the 1.x reader did."""
    path = (fixture_dir or FIXTURE_DIR) / f"{class_name}.json"
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # A broken fixture is a broken test, not a runtime condition — the
        # production exception hierarchy has no business here.
        raise AssertionError(f"fixture {path} is unreadable: {exc}") from exc

    if not raw:
        raise AssertionError(f"fixture {path} is empty")

    root: dict[str, Any] = raw[next(iter(raw))]

    result: dict[str, Any] = {k: root[k] for k in _SCALAR_KEYS if k in root}

    # containedBy in jsonmeta is a {className: ""} dict — normalise to list of keys
    if "containedBy" in result and isinstance(result["containedBy"], dict):
        result["containedBy"] = list(result["containedBy"].keys())

    # contains — the child classes this object may hold.  The raw jsonmeta value
    # is a large {"pkg:Class": ""} dict; project it to a sorted list of flat
    # class names (colon removed) so the agent can pass them straight to
    # get_schema / query / include_children without further conversion.
    raw_contains: dict[str, Any] = root.get("contains") or {}
    if raw_contains:
        result["contains"] = sorted(k.replace(":", "") for k in raw_contains)

    # relationTo values are either plain strings (target class) or dicts
    raw_rel_to: dict[str, Any] = root.get("relationTo") or {}
    if raw_rel_to:
        result["relationTo"] = {
            rel: {
                "targetClass": data
                if isinstance(data, str)
                else data.get("targetClass", ""),
                "cardinality": ""
                if isinstance(data, str)
                else data.get("cardinality", ""),
            }
            for rel, data in raw_rel_to.items()
        }

    # relationFrom values are either plain strings (source class) or dicts
    raw_rel_from: dict[str, Any] = root.get("relationFrom") or {}
    if raw_rel_from:
        result["relationFrom"] = {
            rel: {
                "sourceClass": data
                if isinstance(data, str)
                else data.get("sourceClass", "")
            }
            for rel, data in raw_rel_from.items()
        }

    # Return property names only — full property metadata is too verbose to
    # include by default.
    raw_props: dict[str, Any] = root.get("properties") or {}
    if raw_props:
        result["properties"] = sorted(raw_props.keys())

    # property_details — compact per-property constraints, on demand only.
    # A properties_filter restricts the dump to the requested names (preserving
    # their order); otherwise include_property_details projects every property
    # in the same sorted order as the cheap `properties` list.
    if raw_props and (include_property_details or properties_filter):
        if properties_filter:
            wanted = [name for name in properties_filter if name in raw_props]
        else:
            wanted = sorted(raw_props.keys())
        result["property_details"] = {
            name: _project_property(raw_props[name]) for name in wanted
        }

    return result
