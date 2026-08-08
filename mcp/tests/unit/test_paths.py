# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Path resolution must survive being installed as a package.

Until 2.0 the data and .env locations were derived from ``__file__`` on the
assumption that the server always ran from a git checkout. Installed into
``site-packages`` that arithmetic walks out of the package and lands on a
directory that means nothing — and because a missing .env is not an error and a
missing schema directory merely yields empty results, the failure is silent.

These tests pin the three resolution paths so the regression cannot come back.
They exercise the resolver directly rather than re-importing the module, since
the constants are computed once at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from niwashi_mcp.main import _checkout_root, _first_existing


class TestFirstExisting:
    """The helper behind every path decision."""

    def test_returns_first_existing_candidate(self, tmp_path: Path):
        missing = tmp_path / "nope"
        present = tmp_path / "yes"
        present.mkdir()
        assert _first_existing(missing, present) == present

    def test_skips_none_entries(self, tmp_path: Path):
        """``None`` stands for "this candidate does not apply here".

        The checkout candidate is None when not running from a checkout; it must
        be stepped over rather than crashing the resolver.
        """
        present = tmp_path / "yes"
        present.mkdir()
        assert _first_existing(None, present) == present

    def test_returns_none_when_nothing_exists(self, tmp_path: Path):
        """No silent fallback to a bogus path — the caller decides what to do."""
        assert _first_existing(tmp_path / "a", None, tmp_path / "b") is None

    def test_prefers_earlier_candidate(self, tmp_path: Path):
        first, second = tmp_path / "first", tmp_path / "second"
        first.mkdir()
        second.mkdir()
        assert _first_existing(first, second) == first


class TestCheckoutRoot:
    """A checkout is verified by its layout, never assumed from path arithmetic."""

    def test_detects_the_real_checkout(self):
        """Running from the repository, the root is found and holds data/."""
        root = _checkout_root()
        assert root is not None, "checkout not detected while running from the repo"
        assert (root / "mcp" / "pyproject.toml").is_file()
        assert (root / "data").is_dir()

    def test_returns_none_when_layout_does_not_match(self, monkeypatch, tmp_path: Path):
        """Installed in site-packages there is no checkout — say so, don't guess.

        This is the case that produced ``/tmp/venv/lib/data/schemas`` before the
        fix: a path that exists as a string, points nowhere, and turns a clear
        configuration error into a confusing one.
        """
        fake_pkg = tmp_path / "lib" / "site-packages" / "niwashi_mcp"
        fake_pkg.mkdir(parents=True)
        monkeypatch.setattr("niwashi_mcp.main.BASE_DIR", fake_pkg)
        assert _checkout_root() is None

    def test_rejects_a_partial_layout(self, monkeypatch, tmp_path: Path):
        """Both markers are required — one of them is not a checkout."""
        root = tmp_path / "root"
        (root / "mcp").mkdir(parents=True)
        (root / "mcp" / "pyproject.toml").write_text("")
        # data/ deliberately absent
        pkg = root / "mcp" / "src" / "niwashi_mcp"
        pkg.mkdir(parents=True)
        monkeypatch.setattr("niwashi_mcp.main.BASE_DIR", pkg)
        assert _checkout_root() is None


class TestEnvironmentOverride:
    """The operator's explicit choice wins over every heuristic."""

    @pytest.mark.parametrize(
        "var,attr,value",
        [
            ("ACI_MCP_DATA_DIR", "SCHEMAS_DIR", "/opt/aci-data"),
            ("ACI_MCP_ENV_FILE", "ENV_FILE", "/opt/secrets/aci.env"),
        ],
    )
    def test_override_is_honoured(self, var, attr, value, monkeypatch):
        """Re-import with the variable set and check the constant follows it."""
        import importlib

        import niwashi_mcp.main as main_mod

        monkeypatch.setenv(var, value)
        reloaded = importlib.reload(main_mod)
        try:
            resolved = str(getattr(reloaded, attr))
            assert resolved.startswith(value), f"{attr} ignored {var}: {resolved}"
        finally:
            monkeypatch.delenv(var, raising=False)
            importlib.reload(main_mod)


def test_resolved_paths_never_point_inside_site_packages():
    """The invariant that matters, stated directly.

    Whatever the resolution strategy becomes, data must never be looked up
    inside the installed package: that directory is read-only, wiped on
    upgrade, and invisible to the operator.
    """
    from niwashi_mcp import main as m

    for name in ("SCHEMAS_DIR", "DESCRIPTIONS_FILE", "ENV_FILE"):
        value = str(getattr(m, name))
        assert "site-packages" not in value, f"{name} resolves into site-packages: {value}"
        assert os.sep + "dist-packages" not in value, f"{name} resolves into dist-packages"
