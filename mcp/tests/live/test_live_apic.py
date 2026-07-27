# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/live/test_live_apic.py

End-to-end tests against a real Cisco APIC (or the APIC simulator lab
instance) via apic.client.ApicClient directly — no StubBackend, no
FakeHTTPClient standing in for the transport. These are the only tests in
the whole suite that see APIC's actual error-response shape, actual DN
formats, and actual attribute sets; everything else in tests/unit and
tests/integration either tests pure logic or a Python reimplementation of
the backend (StubBackend).

All tests here depend on the session-scoped `live_client` fixture (see
conftest.py), which auto-skips the whole session when the simulator is
unreachable, and are marked @pytest.mark.live so the default `uv run
pytest` (which excludes `live` via pyproject.toml's addopts) never runs
them. Run explicitly with:

    uv run pytest tests/live/ -m live
"""

import pytest

from exceptions import ApicRequestError

# `live` keeps this suite out of the default run (see pyproject.toml
# addopts). `asyncio(loop_scope="session")` binds every test in this module
# to the same session-scoped event loop as the `live_client` fixture — an
# async resource created in one event loop (the httpx connection pool
# opened during authenticate()) cannot be reused from a different one.
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="session")]


# ── query_class() — real objects, real DN formats ─────────────────────────────


async def test_query_class_fvtenant_returns_real_objects(live_client):
    """fvTenant always has at least one instance on a live APIC (uni/tn-common
    at minimum) — assert real objects with the real 'uni/tn-<name>' DN format
    come back, not a StubBackend-shaped approximation."""
    tenants = await live_client.query_class("fvTenant", {}, limit=20)
    assert len(tenants) > 0
    for t in tenants:
        assert t["_class"] == "fvTenant"
        assert t["dn"].startswith("uni/tn-")
        assert t["name"]


async def test_query_class_fvbd_returns_real_objects(live_client):
    """fvBD objects on the lab fabric — real dn format is 'uni/tn-X/BD-Y'."""
    bds = await live_client.query_class("fvBD", {}, limit=20)
    assert len(bds) > 0
    for bd in bds:
        assert bd["_class"] == "fvBD"
        assert "/BD-" in bd["dn"]


async def test_query_class_config_only_returns_fewer_attributes(live_client):
    """rsp-prop-include=config-only must return a strict subset of the
    attribute keys the full response carries, for the *same* real object —
    filtered by name to guarantee both requests hit the identical instance
    rather than comparing two arbitrary limit=1 picks."""
    full = await live_client.query_class("fvBD", {}, limit=1)
    assert full, "no fvBD objects on the lab fabric to test against"
    name = full[0]["name"]

    config = await live_client.query_class(
        "fvBD", {"name": name}, limit=1, config_only=True
    )
    assert config
    assert len(config[0]) < len(full[0])
    assert set(config[0]) <= set(full[0])


# ── get_by_dn() — found and not-found cases ───────────────────────────────────


async def test_get_by_dn_found_matches_query_result(live_client):
    """Fetch a real DN from a query_class() result, then re-fetch it
    directly via get_by_dn() — the found case."""
    bds = await live_client.query_class("fvBD", {}, limit=1)
    assert bds, "no fvBD objects on the lab fabric to test against"
    dn = bds[0]["dn"]

    obj = await live_client.get_by_dn(dn)
    assert obj is not None
    assert obj["dn"] == dn
    assert obj["_class"] == "fvBD"


async def test_get_by_dn_not_found_returns_none(live_client):
    """A deliberately-wrong DN — the not-found case returns None, exactly
    like the APIC's real empty-imdata response for a missing DN."""
    obj = await live_client.get_by_dn(
        "uni/tn-aci-mcp-live-test-does-not-exist/BD-nope"
    )
    assert obj is None


# ── count_class() ──────────────────────────────────────────────────────────────


async def test_count_class_fvbd_returns_plausible_int(live_client):
    """count_class() must return a plausible non-negative int for fvBD, from
    an actual APIC rsp-subtree-include=count response — not StubBackend's
    len() shortcut, which cannot verify the real moCount/childCount parsing
    path in ApicClient._extract_count()."""
    total = await live_client.count_class("fvBD", {})
    assert isinstance(total, int)
    assert total >= 0


# ── error handling — real APIC error-response shape ──────────────────────────


async def test_query_class_bad_filter_expr_raises_apic_request_error(live_client):
    """A filter_expr referencing a nonexistent property on fvBD is rejected
    by the real APIC with an HTTP 400 and a non-empty, human-readable error
    body — exactly the response shape StubBackend can never produce, since
    it has no concept of APIC's own filter/property validation."""
    with pytest.raises(ApicRequestError) as exc_info:
        await live_client.query_class(
            "fvBD", {}, filter_expr='eq(fvBD.notARealPropertyXYZ,"x")'
        )
    assert exc_info.value.status == 400
    assert exc_info.value.apic_text
