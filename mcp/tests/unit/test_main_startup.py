# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""
Unit tests for main.py — startup validation logic.

Tests cover:
  - _serve()        MCP_PORT validation before the server starts
  - app_lifespan()  APIC_HOST / APIC_PASSWORD guards, prefix stripping,
                    context dict structure, and clean shutdown
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main
from exceptions import ConfigurationError


# ── _serve() — port validation ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_raises_config_error_on_non_integer_port(monkeypatch):
    """_serve() raises ConfigurationError when MCP_PORT is not an integer."""
    monkeypatch.setenv("MCP_PORT", "not-a-number")
    with pytest.raises(ConfigurationError, match="MCP_PORT"):
        await main._serve()


@pytest.mark.asyncio
async def test_serve_raises_config_error_on_float_port(monkeypatch):
    """_serve() rejects float strings — only strict integers are valid."""
    monkeypatch.setenv("MCP_PORT", "8000.5")
    with pytest.raises(ConfigurationError, match="MCP_PORT"):
        await main._serve()


@pytest.mark.asyncio
async def test_serve_logs_warning_when_no_api_keys(monkeypatch, caplog):
    """_serve() logs a WARNING when MCP_API_KEYS is not set."""
    monkeypatch.setenv("MCP_PORT", "8000")

    # Patch load_dotenv so the real .env doesn't override our controlled env vars,
    # and patch load_api_keys to return empty list directly.
    with patch("main.load_dotenv"), \
         patch("main.load_api_keys", return_value=[]), \
         patch.object(main.mcp, "run_http_async", new_callable=AsyncMock):
        with caplog.at_level(logging.DEBUG):
            await main._serve()

    assert "WITHOUT authentication" in caplog.text


@pytest.mark.asyncio
async def test_serve_logs_key_count_when_api_keys_set(monkeypatch, caplog):
    """_serve() logs the number of loaded keys when MCP_API_KEYS is set."""
    monkeypatch.setenv("MCP_PORT", "8000")

    with patch("main.load_dotenv"), \
         patch("main.load_api_keys", return_value=["key-a", "key-b"]), \
         patch.object(main.mcp, "run_http_async", new_callable=AsyncMock):
        with caplog.at_level(logging.DEBUG):
            await main._serve()

    assert "2 key(s)" in caplog.text


# ── app_lifespan() — APIC_HOST / APIC_PASSWORD guards ────────────────────────


@pytest.mark.asyncio
async def test_lifespan_raises_when_apic_host_missing(monkeypatch):
    """app_lifespan raises ConfigurationError when APIC_HOST is empty."""
    monkeypatch.setenv("APIC_HOST", "")
    monkeypatch.setenv("APIC_PASSWORD", "secret")

    # load_dotenv must be patched so the real .env cannot supply APIC_HOST.
    with patch("main.load_dotenv"), patch("main.load_descriptions", return_value={}):
        with pytest.raises(ConfigurationError, match="APIC_HOST"):
            async with main.app_lifespan(MagicMock()):
                pass


@pytest.mark.asyncio
async def test_lifespan_raises_when_apic_host_blank(monkeypatch):
    """app_lifespan treats a whitespace-only APIC_HOST as missing."""
    monkeypatch.setenv("APIC_HOST", "   ")
    monkeypatch.setenv("APIC_PASSWORD", "secret")

    with patch("main.load_dotenv"), patch("main.load_descriptions", return_value={}):
        with pytest.raises(ConfigurationError, match="APIC_HOST"):
            async with main.app_lifespan(MagicMock()):
                pass


@pytest.mark.asyncio
async def test_lifespan_raises_when_apic_password_missing(monkeypatch):
    """app_lifespan raises ConfigurationError when APIC_PASSWORD is empty."""
    monkeypatch.setenv("APIC_HOST", "10.0.0.1")
    monkeypatch.setenv("APIC_PASSWORD", "")

    with patch("main.load_dotenv"), patch("main.load_descriptions", return_value={}):
        with pytest.raises(ConfigurationError, match="APIC_PASSWORD"):
            async with main.app_lifespan(MagicMock()):
                pass


# ── app_lifespan() — APIC_HOST prefix stripping ───────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_strips_https_prefix(monkeypatch):
    """app_lifespan strips https:// from APIC_HOST before passing it to ApicClient."""
    monkeypatch.setenv("APIC_HOST", "https://my-apic.example.com")
    monkeypatch.setenv("APIC_PASSWORD", "secret")

    mock_backend = AsyncMock()
    captured: list[str] = []

    def _capture_host(*args, host="", **kwargs):
        captured.append(host)
        return mock_backend

    with patch("main.load_dotenv"), \
         patch("main.load_descriptions", return_value={}), \
         patch("main.ApicClient", side_effect=_capture_host):
        async with main.app_lifespan(MagicMock()):
            pass

    assert captured == ["my-apic.example.com"]


@pytest.mark.asyncio
async def test_lifespan_strips_http_prefix(monkeypatch):
    """app_lifespan strips http:// from APIC_HOST."""
    monkeypatch.setenv("APIC_HOST", "http://10.0.0.1")
    monkeypatch.setenv("APIC_PASSWORD", "secret")

    mock_backend = AsyncMock()
    captured: list[str] = []

    def _capture_host(*args, host="", **kwargs):
        captured.append(host)
        return mock_backend

    with patch("main.load_dotenv"), \
         patch("main.load_descriptions", return_value={}), \
         patch("main.ApicClient", side_effect=_capture_host):
        async with main.app_lifespan(MagicMock()):
            pass

    assert captured == ["10.0.0.1"]


# ── app_lifespan() — happy path context dict ──────────────────────────────────


@pytest.mark.asyncio
async def test_lifespan_yields_expected_context_keys(monkeypatch):
    """app_lifespan context contains 'descriptions', 'backend', and 'schemas_dir'."""
    monkeypatch.setenv("APIC_HOST", "10.0.0.1")
    monkeypatch.setenv("APIC_PASSWORD", "secret")

    fake_descs = {"fvBD": {"label": "Bridge Domain", "comment": ""}}
    mock_backend = AsyncMock()

    with patch("main.load_dotenv"), \
         patch("main.load_descriptions", return_value=fake_descs), \
         patch("main.ApicClient", return_value=mock_backend):
        async with main.app_lifespan(MagicMock()) as ctx:
            assert set(ctx.keys()) >= {"descriptions", "backend", "schemas_dir"}
            assert ctx["descriptions"] is fake_descs
            assert ctx["backend"] is mock_backend


@pytest.mark.asyncio
async def test_lifespan_closes_backend_on_shutdown(monkeypatch):
    """app_lifespan calls backend.close() in the finally block."""
    monkeypatch.setenv("APIC_HOST", "10.0.0.1")
    monkeypatch.setenv("APIC_PASSWORD", "secret")

    mock_backend = AsyncMock()

    with patch("main.load_dotenv"), \
         patch("main.load_descriptions", return_value={}), \
         patch("main.ApicClient", return_value=mock_backend):
        async with main.app_lifespan(MagicMock()):
            pass

    mock_backend.close.assert_called_once()
