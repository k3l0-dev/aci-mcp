# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""`rsp_subtree_include` must actually return the subtree it asks for.

This was the sharpest defect the pre-release audit found, because it produced a
confident wrong answer rather than an error. Two independent legs:

1. **The request went out incomplete.** `rsp-subtree-include` selects *which*
   categories come back; it does not ask for a subtree. Sent alone, the APIC
   returns bare objects. `rsp-subtree` was only set when `include_children` was
   given, so `rsp_subtree_include="faults"` asked for nothing.

2. **The response was thrown away.** Extraction was gated on
   `if include_children and "children" in obj`, so children the APIC *did*
   return — faults, health, audit-logs — were dropped while the parent object
   arrived intact.

`SKILL.md` sells this call as "BDs with their active faults". What an agent
received was the list of BDs that *have* faults, without the faults — from
which it concluded there were none. The test stub in `conftest.py` reproduced
the same gate, so the integration suite agreed with the bug.
"""

from __future__ import annotations

import httpx
import pytest

from niwashi_mcp.apic.client import ApicClient

_BD_WITH_FAULT = {
    "totalCount": "1",
    "imdata": [
        {
            "fvBD": {
                "attributes": {"dn": "uni/tn-OT/BD-srv", "name": "srv"},
                "children": [
                    {
                        "faultInst": {
                            "attributes": {
                                "code": "F0467",
                                "severity": "major",
                                "descr": "Port is down",
                            }
                        }
                    }
                ],
            }
        }
    ],
}


@pytest.fixture
def client_and_urls():
    """A real ApicClient over a mock transport, recording every URL it emits."""
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "aaaLogin" in str(request.url):
            return httpx.Response(
                200, json={"imdata": [{"aaaLogin": {"attributes": {"token": "t"}}}]}
            )
        urls.append(str(request.url))
        return httpx.Response(200, json=_BD_WITH_FAULT)

    client = ApicClient(host="apic.test", user="u", password="p", verify_ssl=False)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://apic.test"
    )
    client._token = "t"
    return client, urls


class TestRequestIsComplete:
    @pytest.mark.asyncio
    async def test_rsp_subtree_include_also_asks_for_the_subtree(self, client_and_urls):
        """Without `rsp-subtree`, the APIC returns bare objects and the flag is inert."""
        client, urls = client_and_urls
        await client.query_class("fvBD", {}, rsp_subtree_include="faults")

        assert "rsp-subtree-include=faults" in urls[0]
        assert "rsp-subtree=" in urls[0], (
            "rsp-subtree-include was sent without rsp-subtree — the APIC returns "
            "nothing and the caller sees an empty result rather than an error"
        )

    @pytest.mark.asyncio
    async def test_include_children_still_pins_the_class_list(self, client_and_urls):
        """The explicit path must keep narrowing by class."""
        client, urls = client_and_urls
        await client.query_class("fvBD", {}, include_children=["fvSubnet"])

        assert "rsp-subtree=children" in urls[0]
        assert "rsp-subtree-class=fvSubnet" in urls[0]

    @pytest.mark.asyncio
    async def test_neither_flag_asks_for_no_subtree(self, client_and_urls):
        """A plain query must not start dragging subtrees back."""
        client, urls = client_and_urls
        await client.query_class("fvBD", {})

        assert "rsp-subtree" not in urls[0]


class TestResponseIsNotDiscarded:
    @pytest.mark.asyncio
    async def test_faults_reach_the_caller(self, client_and_urls):
        """The defect in one assertion: the fault is in the response, and must arrive."""
        client, _ = client_and_urls
        result = await client.query_class("fvBD", {}, rsp_subtree_include="faults,required")

        obj = result.objects[0]
        assert "_children" in obj, (
            "the APIC returned a faultInst child and it was dropped — an agent "
            "reading this concludes the bridge domain has no faults"
        )
        assert obj["_children"][0]["code"] == "F0467"
        assert obj["_children"][0]["_class"] == "faultInst"

    @pytest.mark.asyncio
    async def test_get_by_dn_keeps_children_too(self, client_and_urls):
        """Same gate, same fix, second entry point.

        `get_by_dn` takes no `rsp_subtree_include` — it fetches one object by
        DN — but it carried the identical `include_children and …` gate, so a
        response containing children dropped them just the same. Asserted
        without passing the flag, which is the shape that used to fail.
        """
        client, _ = client_and_urls
        obj = await client.get_by_dn("uni/tn-OT/BD-srv")

        assert obj is not None
        assert "_children" in obj, (
            "children present in the response were discarded because the call "
            "did not name them by class"
        )
        assert obj["_children"][0]["code"] == "F0467"

    @pytest.mark.asyncio
    async def test_objects_without_children_are_unaffected(self, client_and_urls):
        """Taking whatever is present must not invent a key."""
        client, _ = client_and_urls

        def bare(request: httpx.Request) -> httpx.Response:
            if "aaaLogin" in str(request.url):
                return httpx.Response(
                    200, json={"imdata": [{"aaaLogin": {"attributes": {"token": "t"}}}]}
                )
            return httpx.Response(
                200,
                json={
                    "totalCount": "1",
                    "imdata": [{"fvBD": {"attributes": {"dn": "uni/tn-OT/BD-x"}}}],
                },
            )

        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(bare), base_url="https://apic.test"
        )
        result = await client.query_class("fvBD", {})
        assert "_children" not in result.objects[0]
