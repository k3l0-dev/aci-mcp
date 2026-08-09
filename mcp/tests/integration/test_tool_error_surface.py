# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
What a tool does when the backend fails — the half nothing exercised.

`StubBackend` never raises. Not a 400, not a 500, not a timeout, not malformed
JSON, not a missing `imdata`. So no integration test observed what a *tool*
does with a failing backend, only what `ApicClient` does with a failing HTTP
transport. Those are different questions: the client's job is to raise the right
exception, the tool's is to let it reach the caller intact rather than swallowing
it into an empty result.

That distinction is the whole premise of the server. An empty result and a
failed call are indistinguishable to an agent unless the failure propagates —
`count()` returning 0 because the APIC refused the request is a wrong answer
stated with confidence, and the tool docstrings tell an agent in as many words
that a tool error "is a failure to answer, not an answer of zero".

These tests use a backend that raises, which the stub could not express.
"""

from __future__ import annotations

import pytest

from niwashi_mcp.exceptions import (
    ApicConnectionError,
    ApicRequestError,
    ApicResponseError,
)
from tests.conftest import MINIMAL_DESCRIPTIONS, StubBackend, make_ctx


class RaisingBackend(StubBackend):
    """A backend whose every data call fails, the way a real APIC can.

    Subclasses the stub so the tools see the same shape everywhere else; only
    the three data methods are replaced.
    """

    def __init__(self, exc: Exception):
        super().__init__([])
        self._exc = exc

    async def query_class(self, **kwargs):
        raise self._exc

    async def get_by_dn(self, **kwargs):
        raise self._exc

    async def count_class(self, **kwargs):
        raise self._exc


def _ctx(exc: Exception):
    return make_ctx(
        {"descriptions": dict(MINIMAL_DESCRIPTIONS), "backend": RaisingBackend(exc)}
    )


_FAILURES = [
    pytest.param(
        ApicRequestError("https://apic/api/class/fvBD.json", 400, "invalid filter"),
        id="400-malformed-filter",
    ),
    pytest.param(
        ApicRequestError("https://apic/api/class/fvBD.json", 500, ""),
        id="500-server-error",
    ),
    pytest.param(
        ApicConnectionError("10.0.0.1", "request timed out"),
        id="timeout",
    ),
    pytest.param(
        ApicResponseError("https://apic/api/class/fvBD.json", "imdata missing"),
        id="malformed-body",
    ),
]


class TestFailuresReachTheCaller:
    """A backend failure must surface as itself, never as an empty answer."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", _FAILURES)
    async def test_query_propagates_rather_than_returning_an_empty_envelope(self, exc):
        from niwashi_mcp.main import query

        with pytest.raises(type(exc)):
            await query("fvBD", _ctx(exc))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", _FAILURES)
    async def test_count_propagates_rather_than_returning_zero(self, exc):
        """The one that matters most.

        `count` returning `{"count": 0}` because the APIC refused the request is
        a wrong answer delivered with total confidence, and the tool's own
        docstring tells an agent that a tool error "is a failure to answer, not
        an answer of zero". Nothing enforced it.
        """
        from niwashi_mcp.main import count

        with pytest.raises(type(exc)):
            await count("fvBD", _ctx(exc))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", _FAILURES)
    async def test_get_by_dn_propagates_rather_than_reporting_not_found(self, exc):
        """`get_by_dn` has a legitimate not-found shape — `{"found": False, …}`.

        A backend failure must not borrow it: "no object exists at that DN" and
        "I could not ask" lead an agent to opposite conclusions, and only one of
        them is a fact about the fabric.
        """
        from niwashi_mcp.main import get_by_dn

        with pytest.raises(type(exc)):
            await get_by_dn("uni/tn-OT/BD-servers", _ctx(exc))


class TestTheErrorCarriesWhatTheCallerNeeds:
    @pytest.mark.asyncio
    async def test_a_400_keeps_its_status_and_the_apic_text(self):
        """The APIC's own explanation is the actionable part.

        Without it a malformed `filter_expr` is indistinguishable from a wrong
        class name, and the agent's next move differs completely.
        """
        from niwashi_mcp.main import query

        exc = ApicRequestError("https://apic/api/class/fvBD.json", 400, "invalid filter syntax")
        with pytest.raises(ApicRequestError) as caught:
            await query("fvBD", _ctx(exc), filter_expr="not(a filter)")

        assert caught.value.status == 400
        assert "invalid filter syntax" in str(caught.value)

    @pytest.mark.asyncio
    async def test_a_timeout_names_the_host(self):
        from niwashi_mcp.main import count

        with pytest.raises(ApicConnectionError) as caught:
            await count("fvBD", _ctx(ApicConnectionError("10.41.71.11", "timed out")))

        assert "10.41.71.11" in str(caught.value)
