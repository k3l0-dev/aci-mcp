# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
registry/schema.py

Lazy loader for APIC jsonmeta class schemas.

Each jsonmeta file (one per ACI class) describes the full object model for
that class: how its DN is built, which attributes uniquely identify it, its
parent in the containment hierarchy, and its relations to other classes.

This module extracts only the fields relevant to query planning and returns
them in a flattened structure suitable for consumption by an LLM tool call.
"""

import json
import logging
from pathlib import Path
from typing import Any

from niwashi_mcp.exceptions import SchemaLoadError

logger = logging.getLogger("aci-mcp.registry")

# Keys extracted from the raw jsonmeta root object.
# Heavy fields (writeAccess, events, stats, faults, …) are intentionally omitted
# to keep the tool response token-efficient.
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


def resolve_schemas_dir(schemas_dir: Path) -> Path:
    """Resolve the directory that actually holds *.json jsonmeta files.

    schema-collector writes each collection run into its own versioned
    subdirectory (e.g. ``data/schemas/mo-apic-v6.0_9c/``) so multiple APIC
    versions can coexist on disk. That means the configured top-level
    directory (``data/schemas/``) itself is normally empty of *.json files,
    and the real files live exactly one level down.

    This function performs that one-time discovery so `load_schema()` never
    has to: call it once at server startup (see `main.app_lifespan`) and pass
    the *returned* directory to every `load_schema()` call afterwards.
    Doing the discovery per-call via `Path.glob("*/{class}.json")` means
    scanning every entry of a 15k+-file directory tree on every single
    `get_schema()` tool invocation — this function scans the (small) list of
    top-level subdirectories once and never again.

    Resolution order:
      1. Flat layout — `schemas_dir` itself directly contains one or more
         *.json files: returned unchanged. Keeps backward compatibility with
         a hand-populated flat directory (e.g. in tests or ad-hoc setups).
      2. Single versioned subdirectory — exactly one immediate subdirectory
         contains *.json files: that subdirectory is returned.
      3. Multiple versioned subdirectories — several immediate subdirectories
         each contain *.json files (schema-collector has been run against more
         than one APIC version over time): the subdirectory whose name sorts
         last is returned. schema-collector names these `mo-apic-v{version}`,
         and lexicographic sort of that naming scheme tracks chronological
         "newest version" closely enough in practice; this is a heuristic,
         not a semantic-version comparison, so an operator who needs a
         specific older version pinned should point `schemas_dir` directly at
         that subdirectory instead of the shared parent.
      4. Nothing found — `schemas_dir` does not exist, or exists but holds no
         *.json files anywhere: `schemas_dir` is returned unchanged, and
         every subsequent `load_schema()` call will simply report the class
         as not found — identical to today's behaviour for a missing or
         not-yet-collected schema set.

    Args:
        schemas_dir: The configured top-level schema directory, e.g.
                     `REPO_ROOT / "data" / "schemas"`.

    Returns:
        The directory to pass to `load_schema()` for direct, non-wildcard
        file access.
    """
    if not schemas_dir.is_dir():
        return schemas_dir

    if any(schemas_dir.glob("*.json")):
        return schemas_dir

    versioned = sorted(
        d for d in schemas_dir.iterdir() if d.is_dir() and any(d.glob("*.json"))
    )
    return versioned[-1] if versioned else schemas_dir


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


def load_schema(
    class_name: str,
    schemas_dir: Path,
    include_property_details: bool = False,
    properties_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Load and simplify the jsonmeta schema for a single ACI class.

    Reads the schema file from `schemas_dir/{class_name}.json` and returns a
    dict containing only the fields useful for query planning:

      identifiedBy  — list of attribute names that uniquely identify an instance
      rnFormat      — RN template string, e.g. "BD-{name}"
      containedBy   — list of parent class names in colon notation, e.g. ["fv:Tenant"]
      contains      — sorted list of child class names this object may hold, in
                      flat notation ready to feed to get_schema/query/
                      include_children, e.g. ["fvSubnet", "fvRsCtx", "tagTag"]
      dnFormats     — list of full DN pattern strings
      relationTo    — {relClass: {targetClass, cardinality}} for outgoing Rs
                      relations.  Keys and targetClass keep the raw "pkg:Class"
                      colon notation — they are deliberately NOT flattened the
                      way `contains` is, because the jsonmeta uses that form and
                      round-tripping it is lossy for packages whose name already
                      contains a capital.  `cardinality` is "" for every entry
                      on the current bundle: the raw value is a plain string
                      (the target class), so the dict branch below never fires.
                      The real cardinality lives on the relation class itself,
                      under its own relationInfo.
      relationFrom  — {relClass: {sourceClass}} for incoming Rt relations, also
                      in colon notation
      properties    — sorted list of attribute names available on the class
      property_details — compact per-property constraints, present ONLY when
                      include_property_details or properties_filter is supplied
                      (see _project_property for the per-property shape)
      isAbstract    — True when the class cannot be directly instantiated
      isConfigurable — True when objects of this class can be created/modified via APIC
      className     — short class name without package prefix, e.g. "BD"
      classPkg      — package prefix, e.g. "fv"
      label         — human-readable label, e.g. "Bridge Domain"

    This is the hot path — it is called on every `get_schema()` tool
    invocation — so it performs a single direct file stat/open with no
    wildcard scanning. `schemas_dir` must already be the *resolved* schema
    directory (see `resolve_schemas_dir()`, called once at server startup);
    this function does not search subdirectories.

    Property details are opt-in for token economy: many classes carry 100+
    properties, so a full constraint dump would bloat every response.  Request
    them only for the properties you intend to set.

    Args:
        class_name:  Flat ACI class name, e.g. "fvBD", "faultInst".
        schemas_dir: Resolved directory containing one JSON file per ACI
                     class — see `resolve_schemas_dir()`.
        include_property_details: When True, add `property_details` covering
                     every property.  Ignored when `properties_filter` is given.
        properties_filter: When provided, add `property_details` restricted to
                     these property names (unknown names are silently skipped).
                     This is the token-efficient path — prefer it over the full
                     dump whenever you know which properties you care about.

    Returns:
        Populated dict, or an empty dict when the class file is not found.
    """
    path = schemas_dir / f"{class_name}.json"
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaLoadError(class_name, str(path), str(exc)) from exc
    except OSError as exc:
        raise SchemaLoadError(class_name, str(path), str(exc)) from exc

    if not raw:
        raise SchemaLoadError(
            class_name, str(path), "file is empty or contains an empty object"
        )

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


def class_exists(class_name: str, schemas_dir: Path) -> bool:
    """Check whether a schema file resolves for class_name with an exact match.

    `load_schema()` alone is not a safe existence check on case-insensitive
    filesystems — the default on macOS (APFS) and Windows (NTFS). There,
    `schemas_dir / f"{name}.json"` resolves through a filesystem stat call
    that some filesystems match case-insensitively, so a typo such as "fvBd"
    would silently resolve to the real "fvBD.json" file and be treated as a
    valid class. This defeats the purpose of using schema resolution as a
    fallback existence check (see `main.query()`), which exists precisely to
    catch typos before they reach the APIC.

    jsonmeta's `className`/`classPkg` root fields, in contrast, come from the
    JSON content itself, not the filesystem path — comparing them here in
    Python is always case-sensitive and behaves identically on every OS.

    Args:
        class_name:  Flat ACI class name to verify, e.g. "fvBD".
        schemas_dir: Resolved schema directory (see `resolve_schemas_dir()`).

    Returns:
        True only when a schema file resolves AND its className/classPkg
        reconstruct to exactly `class_name`.
    """
    schema = load_schema(class_name, schemas_dir)
    if not schema:
        return False
    return schema.get("classPkg", "") + schema.get("className", "") == class_name
