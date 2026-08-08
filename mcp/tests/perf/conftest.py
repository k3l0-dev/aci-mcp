# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/perf/conftest.py

Fixtures for performance tests — generates large datasets that mirror
production scale: 15 k+ classes, 1 000-object APIC responses, 100+ schemas.
"""


import pytest

# ── Generators ────────────────────────────────────────────────────────────────

ACI_PKGS = ["fv", "vz", "l2", "l3", "fabric", "infra", "aaa", "fault", "mgmt", "phys"]
WORDS = [
    "Bd",
    "Tenant",
    "Ctx",
    "AEPg",
    "Contract",
    "Node",
    "Port",
    "Path",
    "Policy",
    "Profile",
    "Subnet",
    "Out",
    "Inst",
    "Def",
    "Rel",
    "Rs",
    "Rt",
    "Grp",
    "Set",
    "Map",
]


def _make_class_name(i: int) -> str:
    pkg = ACI_PKGS[i % len(ACI_PKGS)]
    word = WORDS[(i // len(ACI_PKGS)) % len(WORDS)]
    return f"{pkg}{word}{i}"


def generate_descriptions(count: int = 15_000) -> dict:
    """Generate a descriptions dict of `count` entries — matches production scale."""
    desc = {}
    for i in range(count):
        cls = _make_class_name(i)
        desc[cls] = {
            "label": f"{cls} Object",
            "comment": f"Represents a {cls.lower()} configuration object in the ACI fabric policy model.",
        }
    # Always include the real classes used by integration tests
    from tests.conftest import MINIMAL_DESCRIPTIONS

    desc.update(MINIMAL_DESCRIPTIONS)
    return desc


def generate_imdata(class_name: str, count: int = 1_000) -> list[dict]:
    """Generate `count` APIC imdata objects for `class_name`."""
    return [
        {
            class_name: {
                "attributes": {
                    "dn": f"uni/tn-OT/obj-{i}",
                    "name": f"object-{i}",
                    "descr": f"Auto-generated object number {i} for performance testing",
                    "status": "created,modified",
                    "modTs": "2026-06-01T00:00:00.000+00:00",
                }
            }
        }
        for i in range(count)
    ]


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def large_descriptions():
    """15 k-class descriptions dict — session-scoped so it's built once."""
    return generate_descriptions(15_000)


@pytest.fixture(scope="session")
def large_imdata():
    """1 000-object APIC imdata list for fvBD — session-scoped."""
    return generate_imdata("fvBD", 1_000)
