"""
tests/conftest.py

Shared pytest fixtures for aci-mcp tests.

Provides:
  sample_imdata  — small flat imdata list for unit/integration tests
  schemas_dir    — path to the local jsonmeta schema collection
"""

from pathlib import Path

import pytest

SCHEMAS_DIR = Path(__file__).parent.parent / "data" / "schemas"

# ── In-memory fixture data (flat imdata format) ───────────────────────────────

_SAMPLE_IMDATA = [
    {"fvTenant": {"attributes": {"name": "OT", "dn": "uni/tn-OT", "descr": ""}}},
    {"fvTenant": {"attributes": {"name": "common", "dn": "uni/tn-common", "descr": ""}}},
    {"fvBD": {"attributes": {"name": "servers", "dn": "uni/tn-OT/BD-servers", "arpFlood": "no"}}},
    {"fvBD": {"attributes": {"name": "clients", "dn": "uni/tn-OT/BD-clients", "arpFlood": "yes"}}},
    {"fvAEPg": {"attributes": {"name": "web", "dn": "uni/tn-OT/ap-prod/epg-web", "descr": ""}}},
    {"fvAEPg": {"attributes": {"name": "db", "dn": "uni/tn-OT/ap-prod/epg-db", "descr": ""}}},
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

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_imdata() -> list[dict]:
    """Small flat imdata list covering fvTenant, fvBD, fvAEPg, faultInst."""
    return list(_SAMPLE_IMDATA)


@pytest.fixture
def schemas_dir() -> Path:
    """Path to the aci-mcp/schemas/ jsonmeta collection."""
    return SCHEMAS_DIR
