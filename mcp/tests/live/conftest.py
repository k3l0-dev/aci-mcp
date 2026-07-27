# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/live/conftest.py

Fixtures for tests that exercise a real Cisco APIC (or APIC simulator lab
instance) — see tests/live/__init__.py for how this suite fits into the
overall test taxonomy.

Provides:
  live_client — session-scoped, authenticated ApicClient built from the
                repo-root .env, exactly like main.py's app_lifespan.
                Auto-skips the entire live session (via pytest.skip) rather
                than failing when the simulator cannot be reached, so a CI
                runner with no network path to the internal lab never turns
                this suite into a false failure.
"""

import os
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import load_dotenv

from apic.client import ApicClient
from exceptions import ApicAuthError, ApicConnectionError

# tests/live/conftest.py -> tests/live -> tests -> mcp -> repo root
REPO_ROOT = Path(__file__).parent.parent.parent.parent
ENV_FILE = REPO_ROOT / ".env"

# Short relative to ApicClient's 30s production default: a live-suite run
# should fail fast — as a skip, not a hang — when the simulator is
# unreachable from the current environment.
_CONNECT_TIMEOUT = 8.0


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def live_client():
    """Build and authenticate a real ApicClient against the live APIC, or
    skip the whole live session when it cannot be reached.

    Reads APIC_HOST, APIC_USER, APIC_PASSWORD, and APIC_VERIFY_SSL from the
    repo-root .env — the same variables, defaults, and host-prefix-stripping
    logic main.app_lifespan uses at real server startup — so this fixture
    never drifts from how the production server actually connects.

    Calls pytest.skip() (never a bare failure) in two situations:
      1. APIC_HOST or APIC_PASSWORD is not configured at all — e.g. a bare
         checkout / CI runner with no .env for the internal lab.
      2. authenticate() fails with a connection or auth error — e.g. no
         network route to the simulator from the current environment.
    Any other failure during authenticate() (e.g. ApicResponseError from a
    genuinely malformed response) is allowed to propagate as a real test
    failure — that would indicate an actual bug, not an unreachable lab.

    Yields:
        An authenticated ApicClient, closed automatically at session end.
    """
    load_dotenv(ENV_FILE)

    host = (
        os.environ.get("APIC_HOST", "")
        .removeprefix("https://")
        .removeprefix("http://")
        .strip()
    )
    user = os.environ.get("APIC_USER", "admin")
    password = os.environ.get("APIC_PASSWORD", "")
    verify_ssl = os.environ.get("APIC_VERIFY_SSL", "false").lower() == "true"

    if not host or not password:
        pytest.skip(
            "APIC_HOST/APIC_PASSWORD not configured — skipping tests/live/ "
            "(set them in the repo-root .env to run against a real APIC)"
        )

    client = ApicClient(
        host=host,
        user=user,
        password=password,
        verify_ssl=verify_ssl,
        timeout=_CONNECT_TIMEOUT,
    )
    try:
        await client.authenticate()
    except (
        ApicAuthError,
        ApicConnectionError,
        httpx.ConnectError,
        httpx.TimeoutException,
    ) as exc:
        await client.close()
        pytest.skip(f"Real APIC at {host} is unreachable — skipping tests/live/: {exc}")

    try:
        yield client
    finally:
        await client.close()
