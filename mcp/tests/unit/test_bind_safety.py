# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Binding a routable interface without authentication is refused.

Until 2.0 the server bound `0.0.0.0` unconditionally — there was no way to
change it, `grep MCP_HOST` found nothing — while `README.md` told the reader it
listened on localhost. The documented quickstart (`uvx niwashi-mcp`) therefore
put an unauthenticated server holding APIC credentials on every interface of
the machine, and the only guard was a log line that scrolls past.

The production path was fine: `deploy/docker-compose.yml` uses `expose:` rather
than `ports:`. It was the *documented* path that was exposed, which is the one
a first-time user takes.

Three rules are pinned here:
  - loopback by default;
  - a routable bind with no API keys is refused, not warned about;
  - the operator can still say yes explicitly, and it is logged as deliberate.
"""

from __future__ import annotations

import pytest

from niwashi_mcp.main import _is_loopback


class TestIsLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.53"])
    def test_loopback_addresses(self, host):
        assert _is_loopback(host) is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "::", "", "10.0.0.1", "192.168.1.5"])
    def test_routable_and_wildcard_addresses(self, host):
        """`0.0.0.0` and `::` are wildcards — they bind every interface.

        They feel local because you reach them at localhost, which is exactly
        why they need naming: they are the least loopback value there is.
        """
        assert _is_loopback(host) is False

    def test_unparseable_host_is_treated_as_routable(self):
        """In doubt, assume exposure. The safe reading is the restrictive one."""
        assert _is_loopback("not-an-address") is False
        assert _is_loopback("apic.internal.example") is False


class TestServeRefusesUnauthenticatedExposure:
    """The guard itself, exercised through `_serve`."""

    @staticmethod
    async def _serve_with(monkeypatch, **env) -> BaseException | None:
        """Run `_serve` far enough to hit the guard, then stop it.

        `run_http_async` is patched out: reaching it means the guard let the
        configuration through, which is the outcome under test.
        """
        from niwashi_mcp import main as m

        for key in ("MCP_HOST", "MCP_API_KEYS", "MCP_ALLOW_NO_AUTH", "MCP_PORT"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)

        monkeypatch.setattr(m, "load_dotenv", lambda *a, **k: None)

        reached: list[str] = []

        async def _fake_run(*_a, host="", **_k):
            reached.append(host)

        monkeypatch.setattr(m.mcp, "run_http_async", _fake_run)
        try:
            await m._serve()
        except BaseException as exc:
            return exc
        return None if not reached else RuntimeError(f"bound:{reached[0]}")

    @pytest.mark.asyncio
    async def test_routable_bind_without_keys_is_refused(self, monkeypatch):
        from niwashi_mcp.exceptions import ConfigurationError

        outcome = await self._serve_with(monkeypatch, MCP_HOST="0.0.0.0")
        assert isinstance(outcome, ConfigurationError), (
            f"expected a refusal, got {outcome!r} — an unauthenticated server "
            "holding APIC credentials was allowed onto every interface"
        )
        assert "MCP_API_KEYS" in str(outcome)

    @pytest.mark.asyncio
    async def test_loopback_without_keys_is_allowed(self, monkeypatch):
        """Refusing this too would make the tool unusable for its first user."""
        outcome = await self._serve_with(monkeypatch, MCP_HOST="127.0.0.1")
        assert isinstance(outcome, RuntimeError)
        assert str(outcome) == "bound:127.0.0.1"

    @pytest.mark.asyncio
    async def test_default_bind_is_loopback(self, monkeypatch):
        """No MCP_HOST set must not mean every interface."""
        outcome = await self._serve_with(monkeypatch)
        assert isinstance(outcome, RuntimeError)
        assert str(outcome) == "bound:127.0.0.1"

    @pytest.mark.asyncio
    async def test_routable_bind_with_keys_is_allowed(self, monkeypatch):
        outcome = await self._serve_with(
            monkeypatch, MCP_HOST="0.0.0.0", MCP_API_KEYS="secret-key"
        )
        assert isinstance(outcome, RuntimeError)
        assert str(outcome) == "bound:0.0.0.0"

    @pytest.mark.asyncio
    async def test_explicit_opt_out_is_honoured(self, monkeypatch):
        """The operator may accept the risk — but has to say so."""
        outcome = await self._serve_with(
            monkeypatch, MCP_HOST="0.0.0.0", MCP_ALLOW_NO_AUTH="true"
        )
        assert isinstance(outcome, RuntimeError)
        assert str(outcome) == "bound:0.0.0.0"
