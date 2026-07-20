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

from exceptions import SchemaLoadError

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
      relationTo    — {relClass: {targetClass, cardinality}} for outgoing Rs relations
      relationFrom  — {relClass: {sourceClass}} for incoming Rt relations
      properties    — sorted list of attribute names available on the class
      property_details — compact per-property constraints, present ONLY when
                      include_property_details or properties_filter is supplied
                      (see _project_property for the per-property shape)
      isAbstract    — True when the class cannot be directly instantiated
      isConfigurable — True when objects of this class can be created/modified via APIC
      className     — short class name without package prefix, e.g. "BD"
      classPkg      — package prefix, e.g. "fv"
      label         — human-readable label, e.g. "Bridge Domain"

    Property details are opt-in for token economy: many classes carry 100+
    properties, so a full constraint dump would bloat every response.  Request
    them only for the properties you intend to set.

    Args:
        class_name:  Flat ACI class name, e.g. "fvBD", "faultInst".
        schemas_dir: Directory containing one JSON file per ACI class.
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
        # schemas may live one level down (versioned subdir, e.g. mo-apic-v6.0_9c/)
        matches = list(schemas_dir.glob(f"*/{class_name}.json"))
        if not matches:
            return {}
        path = matches[0]

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
