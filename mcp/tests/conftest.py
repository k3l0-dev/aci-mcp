# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/conftest.py

Shared fixtures and helpers for all aci-mcp tests.

Provides:
  sample_imdata        — small multi-class imdata list
  catalogue_index      — search index rebuilt from niwaki's catalogue
  tool_ctx             — ready-to-use FastMCP context stub for tool tests
  apic_response()      — builder for realistic APIC JSON response bodies
  apic_login_response()— builder for APIC aaaLogin response bodies
  make_imdata_objects() — helper to build imdata lists for a single class
  StubBackend          — in-memory ApicClient replacement
  make_ctx()           — minimal FastMCP Context stub
  MINIMAL_DESCRIPTIONS — small descriptions dict, always available without data/
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from niwashi_mcp.apic.client import QueryResult

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

    Supports the full tool surface: query_class (with config_only), get_by_dn,
    and count_class.  Every call is recorded in `calls` with its parameters so
    tests can assert exactly what reached the backend.
    """

    # Operational / meta attributes an APIC config-only response strips.  Used to
    # faithfully simulate rsp-prop-include=config-only in query_class/get_by_dn.
    _OPERATIONAL_ATTRS = frozenset(
        {"modTs", "lcOwn", "monPolDn", "childAction", "extMngdBy", "uid", "rn"}
    )

    def __init__(self, imdata: list[dict]):
        self._data = imdata
        self.calls: list[dict] = []

    def _emit(
        self,
        class_name: str,
        obj: dict[str, Any],
        include_children: list[str] | None,
        config_only: bool,
    ) -> dict[str, Any]:
        """Build a result dict for one raw imdata object, mirroring ApicClient."""
        attrs = dict(obj.get("attributes", {}))
        if config_only:
            attrs = {
                k: v for k, v in attrs.items() if k not in self._OPERATIONAL_ATTRS
            }
        attrs["_class"] = class_name
        # Mirrors ApicClient: extract whatever children are present, not only
        # those requested by class. Gating this on include_children made the
        # stub agree with the bug it should have caught.
        if "children" in obj:
            children: list[dict[str, Any]] = []
            for child_item in obj["children"]:
                for child_cls, child_obj in child_item.items():
                    child_attrs = dict(child_obj.get("attributes", {}))
                    child_attrs["_class"] = child_cls
                    children.append(child_attrs)
            attrs["_children"] = children
        return attrs

    def _select(
        self, class_name: str, filters: dict[str, str], scope_dn: str
    ) -> list[dict[str, Any]]:
        """Return the raw attribute dicts matching class, scope, and filters."""
        results = []
        for item in self._data:
            obj = item.get(class_name)
            if obj is None:
                continue
            results.append(dict(obj.get("attributes", {})))

        if scope_dn:
            results = [
                o
                for o in results
                if o.get("dn") == scope_dn or o.get("dn", "").startswith(scope_dn + "/")
            ]
        for attr, val in filters.items():
            results = [o for o in results if o.get(attr) == val]
        return results

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
        config_only: bool = False,
        fetch_all: bool = False,
    ) -> QueryResult:
        """Mirror ApicClient.query_class(): same filtering/scoping/ordering,
        plus limit-as-page-size pagination and a QueryResult return, so
        truncation and fetch_all can be exercised without a live APIC.
        """
        self.calls.append(
            {
                "method": "query_class",
                "class_name": class_name,
                "filters": filters,
                "scope_dn": scope_dn,
                "limit": limit,
                "order_by": order_by,
                "include_children": include_children,
                "filter_expr": filter_expr,
                "config_only": config_only,
                "page": page,
                "fetch_all": fetch_all,
            }
        )

        results = []
        for item in self._data:
            obj = item.get(class_name)
            if obj is None:
                continue
            results.append(
                self._emit(class_name, obj, include_children, config_only)
            )

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

        total_available = len(results)

        if fetch_all:
            return QueryResult(
                objects=results, total_available=total_available, complete=True
            )

        start = (page or 0) * limit
        page_objects = results[start : start + limit]
        return QueryResult(
            objects=page_objects, total_available=total_available, complete=True
        )

    async def get_by_dn(
        self,
        dn: str,
        config_only: bool = False,
        include_children: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Return the single object whose dn matches, or None when absent."""
        self.calls.append(
            {
                "method": "get_by_dn",
                "dn": dn,
                "config_only": config_only,
                "include_children": include_children,
            }
        )
        for item in self._data:
            for cls, obj in item.items():
                if obj.get("attributes", {}).get("dn") == dn:
                    return self._emit(cls, obj, include_children, config_only)
        return None

    async def count_class(
        self,
        class_name: str,
        filters: dict[str, str],
        scope_dn: str = "",
        filter_expr: str | None = None,
    ) -> int:
        """Return the number of objects matching class, scope, and filters."""
        self.calls.append(
            {
                "method": "count_class",
                "class_name": class_name,
                "filters": filters,
                "scope_dn": scope_dn,
                "filter_expr": filter_expr,
            }
        )
        return len(self._select(class_name, filters, scope_dn))

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


@pytest.fixture(scope="session")
def catalogue_index() -> dict:
    """The real search index, rebuilt from niwaki's catalogue.

    Session-scoped on purpose: the build costs ~440 ms and, more importantly,
    `descriptions.search()` caches its tokenised index on the *identity* of the
    dict it is handed. A per-test fixture would hand out a new object each time
    and re-tokenise 15,239 entries on every call.
    """
    from niwashi_mcp.registry import catalog

    return catalog.descriptions_index()


@pytest.fixture
def tool_ctx(sample_imdata, catalogue_index):
    """Ready-to-use FastMCP context for tool integration tests.

    `schemas_dir` is gone: 2.0 reads the object model from the catalogue that
    ships inside the niwaki dependency, so there is no directory to resolve and
    no data bundle a test could be missing.
    """
    return make_ctx(
        {
            "descriptions": catalogue_index,
            "backend": StubBackend(sample_imdata),
        }
    )
