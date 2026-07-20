# Copyright (C) 2026 Khalid El-Ouiali — MONARK AIOPS srl
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""
tests/live/

End-to-end tests against a real Cisco APIC (or an APIC simulator lab
instance) via the real apic.client.ApicClient — no StubBackend, no
FakeHTTPClient. See tests/__init__.py for how this suite fits into the
overall test taxonomy, and tests/live/conftest.py for the `live_client`
fixture and its auto-skip behavior when the simulator is unreachable.

All tests here are marked @pytest.mark.live and are excluded from the
default `uv run pytest` run (see the `addopts` default in pyproject.toml).
Run them explicitly with:

    uv run pytest tests/live/ -m live
"""
