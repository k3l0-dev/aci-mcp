"""
tests/conftest.py

Shared fixtures and helpers for all aci-mcp tests.

Provides:
  sample_imdata        — small multi-class imdata list
  schemas_dir          — path to the local jsonmeta schema collection
  tool_ctx             — ready-to-use FastMCP context stub for tool tests
  apic_response()      — builder for realistic APIC JSON response bodies
  apic_login_response()— builder for APIC aaaLogin response bodies
  make_imdata_objects() — helper to build imdata lists for a single class
  StubBackend          — in-memory ApicClient replacement
  make_ctx()           — minimal FastMCP Context stub
  MINIMAL_DESCRIPTIONS — small descriptions dict, always available without data/
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "data" / "schemas"

# ── APIC response builders ────────────────────────────────────────────────────


def apic_response(objects: list[dict[str, Any]]) -> dict:
    """Build a realistic APIC imdata response body.

    Each object must be a dict with one key (the class name) and a nested
    "attributes" dict, optionally with a "children" list.

    Example:
        apic_response([
            {"fvBD": {"attributes": {"dn": "uni/tn-OT/BD-srv", "name": "srv"}}},
        ])
    """
    return {"totalCount": str(len(objects)), "imdata": objects}


def apic_login_response(token: str = "test-token-abc123") -> dict:
    """Build an APIC aaaLogin response body."""
    return {
        "imdata": [
            {
                "aaaLogin": {
                    "attributes": {
                        "token": token,
                        "refreshTimeoutSeconds": "600",
                        "maximumLifetimeSeconds": "86400",
                    }
                }
            }
        ]
    }


def make_imdata_objects(
    class_name: str,
    attrs_list: list[dict[str, str]],
    children_map: dict[str, list[dict]] | None = None,
) -> list[dict]:
    """Build a list of imdata objects for a single class.

    Args:
        class_name:   ACI class name, e.g. "fvBD".
        attrs_list:   One attribute dict per object instance.
        children_map: Optional mapping dn → list of child imdata objects.
    """
    items = []
    for attrs in attrs_list:
        obj: dict[str, Any] = {"attributes": attrs}
        dn = attrs.get("dn", "")
        if children_map and dn in children_map:
            obj["children"] = children_map[dn]
        items.append({class_name: obj})
    return items


# ── Fixture data ──────────────────────────────────────────────────────────────

_SAMPLE_IMDATA = [
    {"fvTenant": {"attributes": {"name": "OT", "dn": "uni/tn-OT", "descr": ""}}},
    {
        "fvTenant": {
            "attributes": {"name": "common", "dn": "uni/tn-common", "descr": ""}
        }
    },
    {
        "fvBD": {
            "attributes": {
                "name": "servers",
                "dn": "uni/tn-OT/BD-servers",
                "arpFlood": "no",
            }
        }
    },
    {
        "fvBD": {
            "attributes": {
                "name": "clients",
                "dn": "uni/tn-OT/BD-clients",
                "arpFlood": "yes",
            }
        }
    },
    {
        "fvBD": {
            "attributes": {"name": "mgmt", "dn": "uni/tn-OT/BD-mgmt", "arpFlood": "no"},
            "children": [
                {
                    "fvSubnet": {
                        "attributes": {
                            "ip": "10.10.10.1/24",
                            "dn": "uni/tn-OT/BD-mgmt/subnet-[10.10.10.1/24]",
                            "scope": "private",
                        }
                    }
                },
            ],
        }
    },
    {
        "fvAEPg": {
            "attributes": {
                "name": "web",
                "dn": "uni/tn-OT/ap-prod/epg-web",
                "descr": "",
            }
        }
    },
    {
        "fvAEPg": {
            "attributes": {"name": "db", "dn": "uni/tn-OT/ap-prod/epg-db", "descr": ""}
        }
    },
    {
        "faultInst": {
            "attributes": {
                "code": "F0532",
                "severity": "critical",
                "dn": "uni/tn-OT/fault-F0532",
            }
        }
    },
    {
        "faultInst": {
            "attributes": {
                "code": "F1123",
                "severity": "minor",
                "dn": "uni/tn-OT/fault-F1123",
            }
        }
    },
]

# Minimal descriptions registry — always available without data/ files.
# Covers the classes present in _SAMPLE_IMDATA plus a few extras.
MINIMAL_DESCRIPTIONS = {
    "fvBD": {
        "label": "Bridge Domain",
        "comment": "A bridge domain is a unique layer 2 forwarding domain.",
    },
    "fvTenant": {"label": "Tenant", "comment": "A policy owner in the virtual fabric."},
    "fvAEPg": {
        "label": "Application EPG",
        "comment": "A set of requirements for the application-level EPG.",
    },
    "faultInst": {
        "label": "Fault Instance",
        "comment": "Contains detailed information of a fault instance.",
    },
    "vzBrCP": {
        "label": "Contract",
        "comment": "A contract governs communication between EPGs.",
    },
    "fvCtx": {
        "label": "VRF",
        "comment": "A VRF instance defines a layer 3 address domain.",
    },
    "fvRsCtx": {"label": "Relation to VRF", "comment": "Resolves the BD to a VRF."},
    "fabricNode": {
        "label": "Fabric Node",
        "comment": "Represents a node in the ACI fabric.",
    },
    "l3extOut": {
        "label": "L3 Outside",
        "comment": "Represents an external L3 routing domain.",
    },
    "fvSubnet": {
        "label": "Subnet",
        "comment": "A subnet associated with a bridge domain.",
    },
}

# ── StubBackend ───────────────────────────────────────────────────────────────


class StubBackend:
    """In-memory ApicClient replacement for tool integration and perf tests.

    Simulates the same filtering, scoping, ordering, and child-embedding logic
    as the real ApicClient without any network calls.  Exposes `calls` for
    asserting what was actually requested.
    """

    def __init__(self, imdata: list[dict]):
        self._data = imdata
        self.calls: list[dict] = []

    async def query_class(
        self,
        class_name: str,
        filters: dict[str, str],
        scope_dn: str = "",
        limit: int = 20,
        order_by: str = "",
        include_children: list[str] | None = None,
        filter_expr: str | None = None,
        rsp_subtree_include: str | None = None,
        time_range: str | None = None,
        page: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(
            {
                "class_name": class_name,
                "filters": filters,
                "scope_dn": scope_dn,
                "limit": limit,
                "order_by": order_by,
                "include_children": include_children,
                "filter_expr": filter_expr,
            }
        )

        results = []
        for item in self._data:
            obj = item.get(class_name)
            if obj is None:
                continue
            attrs = dict(obj.get("attributes", {}))
            attrs["_class"] = class_name
            if include_children and "children" in obj:
                children: list[dict[str, Any]] = []
                for child_item in obj["children"]:
                    for child_cls, child_obj in child_item.items():
                        child_attrs = dict(child_obj.get("attributes", {}))
                        child_attrs["_class"] = child_cls
                        children.append(child_attrs)
                attrs["_children"] = children
            results.append(attrs)

        if scope_dn:
            results = [
                o
                for o in results
                if o.get("dn") == scope_dn or o.get("dn", "").startswith(scope_dn + "/")
            ]

        for attr, val in filters.items():
            results = [o for o in results if o.get(attr) == val]

        if order_by:
            parts = order_by.split("|")
            attr_key = parts[0].split(".")[-1]
            reverse = len(parts) > 1 and parts[1].lower() == "desc"
            results.sort(key=lambda o: o.get(attr_key, ""), reverse=reverse)

        return results[:limit]

    async def close(self) -> None:
        pass


# ── Context stub ──────────────────────────────────────────────────────────────


def make_ctx(lifespan_ctx: dict) -> SimpleNamespace:
    """Minimal stand-in for FastMCP Context — accepts info/warning calls."""
    ctx = SimpleNamespace()
    ctx.lifespan_context = lifespan_ctx
    ctx.info = AsyncMock()
    ctx.warning = AsyncMock()
    return ctx


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_imdata() -> list[dict]:
    """Small flat imdata list covering fvTenant, fvBD (with children), fvAEPg, faultInst."""
    return list(_SAMPLE_IMDATA)


@pytest.fixture
def schemas_dir() -> Path:
    """Path to the aci-mcp/data/schemas/ jsonmeta collection."""
    return SCHEMAS_DIR


@pytest.fixture
def tool_ctx(sample_imdata, schemas_dir):
    """Ready-to-use FastMCP context for tool integration tests.

    Uses the real class-descriptions.json when available; falls back to
    MINIMAL_DESCRIPTIONS so tests always run without the full data/ collection.
    """
    desc_file = Path(__file__).parent.parent.parent / "data" / "class-descriptions.json"
    if desc_file.exists():
        from registry.descriptions import load_descriptions

        descriptions = load_descriptions(desc_file)
    else:
        descriptions = dict(MINIMAL_DESCRIPTIONS)

    return make_ctx(
        {
            "descriptions": descriptions,
            "backend": StubBackend(sample_imdata),
            "schemas_dir": schemas_dir,
        }
    )
