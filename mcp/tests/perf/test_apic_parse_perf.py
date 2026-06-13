"""
Performance tests for APIC response parsing in ApicClient.query_class().

Simulates what happens when the APIC returns large result sets:
  - 1 000 flat objects (typical class query)
  - 200 objects each with 5 children (rsp-subtree=children)
  - 50 concurrent async queries (simulates parallel LLM tool calls)

Thresholds:
  parse 1 000 flat objects          < 50 ms
  parse 200 objects × 5 children    < 50 ms
  50 concurrent queries             < 500 ms total wall time
"""

import asyncio
import time

import httpx
import pytest
from apic.client import ApicClient
from tests.conftest import apic_response
from tests.perf.conftest import generate_imdata


# ── Fake transport (reused from unit/test_client.py concept) ──────────────────


class _MockResponse:
    def __init__(self, body: dict):
        self.status_code = 200
        self.is_success = True
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        pass


class _StaticFakeClient:
    """Always returns the same pre-built response — no queue exhaustion."""

    def __init__(self, response_body: dict):
        self._resp = _MockResponse(response_body)
        self.cookies = httpx.Cookies()
        self.timeout = 30.0

    async def get(self, url, **kwargs):
        return self._resp

    async def post(self, url, **kwargs):
        return self._resp

    async def aclose(self):
        pass


def _client_with_static_response(body: dict) -> ApicClient:
    client = ApicClient("10.0.0.1", "admin", "secret")
    client._client = _StaticFakeClient(body)
    return client


# ── Flat object parsing ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_1000_flat_objects_under_50ms(large_imdata):
    body = apic_response(large_imdata)
    client = _client_with_static_response(body)

    t0 = time.perf_counter()
    results = await client.query_class("fvBD", {})
    elapsed = time.perf_counter() - t0

    assert len(results) == 1_000
    assert all(r["_class"] == "fvBD" for r in results)
    assert elapsed < 0.050, (
        f"Parsing 1000 objects took {elapsed * 1000:.1f}ms — must be < 50ms"
    )


@pytest.mark.asyncio
async def test_parse_200_objects_with_5_children_each():
    children_per_object = [
        {
            "fvSubnet": {
                "attributes": {
                    "ip": f"10.0.{j}.1/24",
                    "dn": f"uni/tn-OT/BD-obj-{{i}}/subnet-[10.0.{j}.1/24]",
                }
            }
        }
        for j in range(5)
    ]
    objects = [
        {
            "fvBD": {
                "attributes": {"dn": f"uni/tn-OT/BD-obj-{i}", "name": f"obj-{i}"},
                "children": children_per_object,
            }
        }
        for i in range(200)
    ]
    body = apic_response(objects)
    client = _client_with_static_response(body)

    t0 = time.perf_counter()
    results = await client.query_class("fvBD", {}, include_children=["fvSubnet"])
    elapsed = time.perf_counter() - t0

    assert len(results) == 200
    assert all(len(r["_children"]) == 5 for r in results)
    assert elapsed < 0.050, (
        f"Parsing 200 objects × 5 children took {elapsed * 1000:.1f}ms — must be < 50ms"
    )


# ── Concurrent queries ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_50_concurrent_queries_complete_under_500ms():
    """Simulate 50 parallel LLM tool calls hitting the same ApicClient."""
    body = apic_response(generate_imdata("fvBD", 20))
    client = _client_with_static_response(body)

    async def one_query(i: int):
        return await client.query_class(
            "fvBD",
            filters={"name": f"obj-{i % 20}"},
            limit=10,
        )

    t0 = time.perf_counter()
    results = await asyncio.gather(*[one_query(i) for i in range(50)])
    elapsed = time.perf_counter() - t0

    assert len(results) == 50
    assert elapsed < 0.500, (
        f"50 concurrent queries took {elapsed:.3f}s — must be < 500ms"
    )


@pytest.mark.asyncio
async def test_concurrent_queries_do_not_share_state():
    """Each query must return its own result list — no accidental sharing."""
    body = apic_response(generate_imdata("fvBD", 5))
    client = _client_with_static_response(body)

    results = await asyncio.gather(*[client.query_class("fvBD", {}) for _ in range(20)])

    for r in results:
        assert len(r) == 5
        # Mutate one result and verify others are not affected
    results[0].clear()
    for r in results[1:]:
        assert len(r) == 5, "Mutation of one result affected another — shared state bug"


# ── Throughput benchmark ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sequential_query_throughput():
    """Measure sequential query throughput with a 20-object response."""
    body = apic_response(generate_imdata("fvBD", 20))
    client = _client_with_static_response(body)

    count = 500
    t0 = time.perf_counter()
    for _ in range(count):
        await client.query_class("fvBD", {})
    elapsed = time.perf_counter() - t0

    qps = count / elapsed
    assert qps > 500, (
        f"Sequential throughput is {qps:.0f} queries/s — expected > 500 q/s"
    )
