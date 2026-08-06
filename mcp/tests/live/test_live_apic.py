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

from collections import Counter

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
    tenants = (await live_client.query_class("fvTenant", {}, limit=20)).objects
    assert len(tenants) > 0
    for t in tenants:
        assert t["_class"] == "fvTenant"
        assert t["dn"].startswith("uni/tn-")
        assert t["name"]


async def test_query_class_fvbd_returns_real_objects(live_client):
    """fvBD objects on the lab fabric — real dn format is 'uni/tn-X/BD-Y'."""
    bds = (await live_client.query_class("fvBD", {}, limit=20)).objects
    assert len(bds) > 0
    for bd in bds:
        assert bd["_class"] == "fvBD"
        assert "/BD-" in bd["dn"]


async def test_query_class_config_only_returns_fewer_attributes(live_client):
    """rsp-prop-include=config-only must return a strict subset of the
    attribute keys the full response carries, for the *same* real object —
    filtered by name to guarantee both requests hit the identical instance
    rather than comparing two arbitrary limit=1 picks."""
    full = (await live_client.query_class("fvBD", {}, limit=1)).objects
    assert full, "no fvBD objects on the lab fabric to test against"
    name = full[0]["name"]

    config = (
        await live_client.query_class(
            "fvBD", {"name": name}, limit=1, config_only=True
        )
    ).objects
    assert config
    assert len(config[0]) < len(full[0])
    assert set(config[0]) <= set(full[0])


# ── get_by_dn() — found and not-found cases ───────────────────────────────────


async def test_get_by_dn_found_matches_query_result(live_client):
    """Fetch a real DN from a query_class() result, then re-fetch it
    directly via get_by_dn() — the found case."""
    bds = (await live_client.query_class("fvBD", {}, limit=1)).objects
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


async def test_count_class_agrees_with_query_total_available(live_client):
    """count_class() and query_class() must report the same size for the same
    result set.

    This is the invariant the previous version of this test missed. It only
    asserted `total >= 0`, which a broken count satisfies trivially — and the
    count did break: the APIC `rsp-subtree-include=count` mechanism this
    client relied on returned a `moCount` that disagreed with reality by
    nearly 2x fabric-wide (203 vs. 403 fvBD). Comparing the two tools against
    each other catches that on any fabric without hardcoding instance counts
    that differ per lab.
    """
    counted = await live_client.count_class("fvBD", {})
    queried = await live_client.query_class("fvBD", {}, limit=1)
    assert counted == queried.total_available
    assert counted > 0, "no fvBD objects on the lab fabric to test against"


async def test_count_class_scoped_to_a_tenant_is_not_silently_zero(live_client):
    """A count scoped to a subtree must report that subtree's real size.

    Pins the sharpest edge of the old bug: a scoped count came back as 0
    against tenants holding up to 192 bridge domains. Zero is the one wrong
    answer an agent will not question — it reads as "none configured in this
    tenant" rather than as a failed lookup.

    The tenant is the *busiest* one on the fabric, not an arbitrary first
    pick. That matters, because the old idiom failed on only 5 of the 28
    tenants holding bridge domains here: a test scoped to whichever tenant
    happened to come back first passed against the broken code on this very
    lab. Targeting the largest subtree maximises the chance of landing on a
    real discrepancy instead of relying on luck of the draw — it is still a
    sampling test, not a proof, which is why the unit-level guard above pins
    the request shape directly.
    """
    bds = (await live_client.query_class("fvBD", {}, limit=200)).objects
    assert bds, "no fvBD objects on the lab fabric to test against"

    per_tenant = Counter("/".join(bd["dn"].split("/")[:2]) for bd in bds)
    tenant_dn, expected_at_least = per_tenant.most_common(1)[0]

    counted = await live_client.count_class("fvBD", {}, scope_dn=tenant_dn)
    queried = await live_client.query_class("fvBD", {}, scope_dn=tenant_dn, limit=1)
    assert counted == queried.total_available
    assert counted >= expected_at_least, (
        f"scoped count returned {counted} for {tenant_dn}, but at least "
        f"{expected_at_least} fvBD were already seen under it"
    )


async def test_count_class_with_filter_agrees_with_query(live_client):
    """A filtered count must match the filtered query's totalCount.

    The filtered case was wrong too, and less obviously so than the scoped
    one — it returned a plausible-looking number that was simply not the
    right one (99 against a real 203).
    """
    bds = (await live_client.query_class("fvBD", {}, limit=1)).objects
    assert bds, "no fvBD objects on the lab fabric to test against"
    name = bds[0]["name"]

    counted = await live_client.count_class("fvBD", {"name": name})
    queried = await live_client.query_class("fvBD", {"name": name}, limit=1)
    assert counted == queried.total_available
    assert counted > 0


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
