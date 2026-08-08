# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""A DN must not be able to leave its URL path segment.

`dn` and `scope_dn` are interpolated straight into `/api/mo/{dn}.json`, and both
arrive from an LLM — whose input can be attacker-influenced through the fabric
data it reads, a hostile document, or a crafted prompt. So this is a
confused-deputy hazard, not a theoretical one: the server holds an authenticated
APIC session, usually admin-capable.

Measured before the guard existed:

    get_by_dn("uni/tn-OT/../../api/aaaListDomains")
      → GET https://apic/api/mo/api/aaaListDomains.json

The traversal resolved and reached a different endpoint entirely.

The guard **rejects** rather than sanitises. Silently rewriting a DN would
answer a question the caller did not ask, which is the failure mode this server
exists to prevent; a malformed DN is a mistake worth surfacing.
"""

from __future__ import annotations

import httpx
import pytest

from niwashi_mcp.apic.client import ApicClient, validate_dn
from niwashi_mcp.exceptions import FilterError


class TestValidateDn:
    @pytest.mark.parametrize(
        "dn",
        [
            "uni/tn-OT",
            "uni/tn-OT/BD-servers",
            "uni/tn-OT/ap-app/epg-web",
            "topology/pod-1/node-101/sys/phys-[eth1/1]",
            "uni/tn-OT/BD-servers/subnet-[10.0.0.1/24]",
            "uni/tn-a..b/BD-x",  # `..` inside a name is legitimate
            "uni/tn-with space/BD-x",
            "uni/tn-OT/rsctx",
        ],
    )
    def test_legitimate_dns_pass_through_unchanged(self, dn):
        """The guard must not become a second source of empty results."""
        assert validate_dn(dn) == dn

    @pytest.mark.parametrize(
        "dn,reason",
        [
            ("uni/tn-OT/../../api/aaaListDomains", "traversal segment"),
            ("../api/aaaLogin", "traversal at the start"),
            ("uni/..", "traversal at the end"),
            ("uni/tn-X?query-target=subtree", "query separator"),
            ("uni/tn-X#frag", "fragment separator"),
            ("/uni/tn-X", "absolute path"),
            ("uni/tn-X\nGET /api/aaaLogin", "newline injection"),
            ("uni/tn-X\x00", "null byte"),
            ("", "empty"),
            ("   ", "whitespace only"),
        ],
    )
    def test_dangerous_dns_are_rejected(self, dn, reason):
        with pytest.raises(FilterError):
            validate_dn(dn)

    def test_the_error_names_the_offending_parameter(self):
        """`scope_dn` and `dn` are different arguments; the message must say which."""
        with pytest.raises(FilterError, match="scope_dn"):
            validate_dn("../x", field="scope_dn")

    def test_the_error_quotes_the_value(self):
        """A caller cannot fix what the message does not show."""
        with pytest.raises(FilterError, match="aaaListDomains"):
            validate_dn("uni/../api/aaaListDomains")


@pytest.fixture
def client_and_urls():
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"totalCount": "0", "imdata": []})

    client = ApicClient(host="apic.test", user="u", password="p", verify_ssl=False)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://apic.test"
    )
    client._token = "t"
    return client, urls


class TestGuardIsWiredIn:
    """A validator nothing calls is decoration."""

    @pytest.mark.asyncio
    async def test_get_by_dn_rejects_traversal_before_any_request(self, client_and_urls):
        client, urls = client_and_urls
        with pytest.raises(FilterError):
            await client.get_by_dn("uni/tn-OT/../../api/aaaListDomains")
        assert urls == [], "a request was sent before the DN was checked"

    @pytest.mark.asyncio
    async def test_query_class_rejects_a_traversal_in_scope_dn(self, client_and_urls):
        client, urls = client_and_urls
        with pytest.raises(FilterError):
            await client.query_class("fvBD", {}, scope_dn="uni/../api/aaaListDomains")
        assert urls == []

    @pytest.mark.asyncio
    async def test_count_class_rejects_a_traversal_in_scope_dn(self, client_and_urls):
        """count() builds its own URL — the third interpolation site."""
        client, urls = client_and_urls
        with pytest.raises(FilterError):
            await client.count_class("fvBD", {}, scope_dn="uni/../api/aaaListDomains")
        assert urls == []

    @pytest.mark.asyncio
    async def test_a_legitimate_dn_still_reaches_the_expected_endpoint(self, client_and_urls):
        client, urls = client_and_urls
        await client.get_by_dn("uni/tn-OT/BD-srv")
        assert urls[0].startswith("https://apic.test/api/mo/uni/tn-OT/BD-srv.json")
