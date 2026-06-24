# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: AGPL-3.0-or-later

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


def load_schema(class_name: str, schemas_dir: Path) -> dict[str, Any]:
    """Load and simplify the jsonmeta schema for a single ACI class.

    Reads the schema file from `schemas_dir/{class_name}.json` and returns a
    dict containing only the fields useful for query planning:

      identifiedBy  — list of attribute names that uniquely identify an instance
      rnFormat      — RN template string, e.g. "BD-{name}"
      containedBy   — list of parent class names in colon notation, e.g. ["fv:Tenant"]
      dnFormats     — list of full DN pattern strings
      relationTo    — {relClass: {targetClass, cardinality}} for outgoing Rs relations
      relationFrom  — {relClass: {sourceClass}} for incoming Rt relations
      properties    — sorted list of attribute names available on the class
      isAbstract    — True when the class cannot be directly instantiated
      isConfigurable — True when objects of this class can be created/modified via APIC
      className     — short class name without package prefix, e.g. "BD"
      classPkg      — package prefix, e.g. "fv"
      label         — human-readable label, e.g. "Bridge Domain"

    Args:
        class_name:  Flat ACI class name, e.g. "fvBD", "faultInst".
        schemas_dir: Directory containing one JSON file per ACI class.

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

    # Return property names only — full property metadata is too verbose
    raw_props: dict[str, Any] = root.get("properties") or {}
    if raw_props:
        result["properties"] = sorted(raw_props.keys())

    return result
