# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The catalogue schema guard.

`registry/catalog.py` reads niwaki's embedded SQLite catalogue with hand-written
SQL against tables — `mo`, `prop`, `comment_pool`, `label_pool`, `type_pool`,
`enum`, `manifest` — that are **private** to niwaki. Its public API is
`Niwaki` / `AsyncNiwaki` / `models`, and none of these appear in it, so any 1.x
release may restructure them without breaking SemVer.

Some of those changes fail loudly on their own: a renamed table raises
`OperationalError` on the first query. The ones worth building a guard for are
the quiet ones — a repurposed column, a changed blob encoding — where every
query still runs and the server answers questions about a production fabric
from silently empty fields. `verify_catalogue()` runs at startup so that never
reaches a user.

Each test builds a deliberately broken catalogue in a temp file and points the
module at it, rather than mutating the real one.
"""

from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path

import pytest

from niwashi_mcp.exceptions import DescriptionsLoadError
from niwashi_mcp.registry import catalog


def _clone_catalogue(tmp_path: Path) -> Path:
    """A real, working copy of the shipped catalogue that tests can damage.

    Copied through SQLite's backup API rather than the filesystem so the result
    is a valid database even if the source is being read concurrently.
    """
    dest = tmp_path / "catalog.db"
    source = sqlite3.connect(f"file:{catalog.catalog_path()}?immutable=1", uri=True)
    target = sqlite3.connect(dest)
    with target:
        source.backup(target)
    source.close()
    target.close()
    return dest


@pytest.fixture
def broken_catalogue(tmp_path, monkeypatch):
    """Point `catalog` at a writable clone and hand back a damage function."""
    path = _clone_catalogue(tmp_path)

    def damage(*statements: str) -> None:
        conn = sqlite3.connect(path)
        with conn:
            for statement in statements:
                conn.execute(statement)
        conn.close()
        # Every accessor is lru_cached on the process, including the connection
        # itself — clear them or the tests read the real catalogue.
        for cached in (
            catalog._connect,
            catalog._flag_bits,
            catalog.apic_version,
            catalog._pool,
            catalog._pool_blob,
        ):
            cached.cache_clear()
        monkeypatch.setattr(catalog, "catalog_path", lambda: path)

    yield damage

    for cached in (
        catalog._connect,
        catalog._flag_bits,
        catalog.apic_version,
        catalog._pool,
        catalog._pool_blob,
    ):
        cached.cache_clear()


class TestTheGuardPasses:
    """It must not cry wolf on the catalogue actually shipped."""

    def test_the_real_catalogue_verifies(self):
        catalog.verify_catalogue()

    def test_it_is_idempotent(self):
        """Called twice — a reload, a second lifespan — it stays quiet."""
        catalog.verify_catalogue()
        catalog.verify_catalogue()


class TestStructuralDamage:
    """Tables and columns the queries name."""

    def test_a_dropped_table_is_named(self, broken_catalogue):
        broken_catalogue("DROP TABLE label_pool")
        with pytest.raises(DescriptionsLoadError, match="label_pool"):
            catalog.verify_catalogue()

    def test_a_renamed_column_is_named(self, broken_catalogue):
        """The realistic refactor: niwaki tidies a column name in a minor."""
        broken_catalogue("ALTER TABLE mo RENAME COLUMN rn_format TO rnFormat")
        with pytest.raises(DescriptionsLoadError, match="rn_format"):
            catalog.verify_catalogue()

    def test_a_dropped_prop_column_is_named(self, broken_catalogue):
        broken_catalogue("ALTER TABLE prop DROP COLUMN default_val")
        with pytest.raises(DescriptionsLoadError, match="default_val"):
            catalog.verify_catalogue()

    def test_the_error_names_the_niwaki_release(self, broken_catalogue):
        """A user reading the log must know which package to pin."""
        broken_catalogue("DROP TABLE enum")
        with pytest.raises(DescriptionsLoadError) as excinfo:
            catalog.verify_catalogue()
        assert "niwaki" in str(excinfo.value)
        assert "1.9" in str(excinfo.value), "the message should say what to pin"

    def test_a_missing_table_reports_the_table_not_its_columns(self, broken_catalogue):
        """Pins the table branch specifically.

        The column check alone would also catch a dropped table — `PRAGMA
        table_info` on a table that is gone returns nothing, so every column
        reads as missing. But it would report "table 'enum' is missing
        ['content', 'id']", which sends the reader looking for columns in a
        table that no longer exists. The distinct message is the point of the
        branch, so assert the message, not merely that something raised.
        """
        broken_catalogue("DROP TABLE type_pool")
        with pytest.raises(DescriptionsLoadError) as excinfo:
            catalog.verify_catalogue()
        assert "no 'type_pool' table" in str(excinfo.value)


class TestManifestDamage:
    def test_a_missing_manifest_key_is_named(self, broken_catalogue):
        broken_catalogue("DELETE FROM manifest WHERE key='apic_version'")
        with pytest.raises(DescriptionsLoadError, match="apic_version"):
            catalog.verify_catalogue()

    def test_a_changed_flag_layout_is_caught(self, broken_catalogue):
        """`prop.flags` is a bitfield whose order lives in the manifest.

        Dropping a flag shifts every bit above it. Nothing would raise; write
        access and `mandatory` would just quietly become wrong.
        """
        broken_catalogue(
            "UPDATE manifest SET value='isConfigurable,readWrite' WHERE key='prop_flags'"
        )
        with pytest.raises(DescriptionsLoadError, match=r"prop_flags|layout"):
            catalog.verify_catalogue()


class TestEncodingDamage:
    """The quiet failures — the reason the guard is functional, not just structural."""

    def test_a_changed_residual_encoding_is_caught(self, broken_catalogue):
        """`residual` is zlib+JSON and carries containedBy/contains/relations.

        Were niwaki to store it as plain JSON, every column would still be
        present, every query would still run, and `containedBy` would come back
        empty on all 15,452 classes — the server would report that a bridge
        domain has no parent.
        """
        plain = b'{"containedBy": {"fv:Tenant": ""}}'
        broken_catalogue(
            f"UPDATE mo SET residual = x'{plain.hex()}' WHERE class_name = 'fvBD'"
        )
        with pytest.raises(DescriptionsLoadError, match=r"containedBy|encoding"):
            catalog.verify_catalogue()

    def test_a_changed_dn_formats_encoding_is_caught(self, broken_catalogue):
        broken_catalogue(
            "UPDATE mo SET dn_formats = x'{}' WHERE class_name = 'fvBD'".format(
                b'["uni/tn-{name}/BD-{name}"]'.hex()
            )
        )
        with pytest.raises(DescriptionsLoadError, match=r"dnFormats|encoding"):
            catalog.verify_catalogue()

    def test_a_severed_label_join_is_caught(self, broken_catalogue):
        """Repointing label_id at a row that does not exist breaks nothing
        structurally — `_pool` simply returns None and the label becomes ""."""
        broken_catalogue("UPDATE mo SET label_id = 999999999 WHERE class_name = 'fvBD'")
        with pytest.raises(DescriptionsLoadError, match=r"label|encoding"):
            catalog.verify_catalogue()

    def test_a_missing_probe_class_is_caught(self, broken_catalogue):
        broken_catalogue("DELETE FROM mo WHERE class_name = 'fvBD'")
        with pytest.raises(DescriptionsLoadError, match="fvBD"):
            catalog.verify_catalogue()

    def test_an_emptied_prop_table_is_caught(self, broken_catalogue):
        """Structurally intact, functionally useless: every class would report
        zero properties, and `query()` would accept any filter key."""
        broken_catalogue("DELETE FROM prop")
        with pytest.raises(DescriptionsLoadError, match=r"properties|encoding"):
            catalog.verify_catalogue()


class TestUnzipReportsBrokenBlobsDirectly:
    """`verify_catalogue()` only probes one class — `_unzip` covers the rest.

    A corrupt blob on any of the other 15,451 classes passes startup and only
    surfaces when an agent asks for that class. What the user sees then is
    whatever `_unzip` raises, so it has to be a message about the catalogue,
    not a bare `zlib.error` from three frames down.
    """

    def test_a_bad_blob_raises_a_catalogue_error(self):
        with pytest.raises(DescriptionsLoadError, match="zlib"):
            catalog._unzip(b"not-compressed-at-all", "mo.residual")

    def test_the_message_names_the_column(self):
        """Which column moved is the whole diagnostic value."""
        with pytest.raises(DescriptionsLoadError, match=r"mo\.dn_formats"):
            catalog._unzip(b"\x00\x01\x02", "mo.dn_formats")

    def test_valid_zlib_carrying_invalid_json_is_caught(self):
        """Decompression succeeding is not proof the payload is what we expect."""
        with pytest.raises(DescriptionsLoadError, match=r"enum\.content"):
            catalog._unzip(zlib.compress(b"{not json"), "enum.content")

    def test_an_empty_blob_is_not_an_error(self):
        """NULL columns are ordinary — `dn_formats` is NULL when a class has none."""
        assert catalog._unzip(None) is None
        assert catalog._unzip(b"") is None

    def test_a_corrupt_blob_on_a_non_probe_class_surfaces_at_request_time(
        self, broken_catalogue
    ):
        """End to end: damage a class the startup probe never looks at.

        `load_schema("fvTenant")` must report a broken catalogue rather than
        crashing with `zlib.error`, and startup must have let it through —
        which is exactly why `_unzip` needs its own guard.
        """
        broken_catalogue(
            f"UPDATE mo SET dn_formats = x'{b'[]'.hex()}' WHERE class_name = 'fvTenant'"
        )
        catalog.verify_catalogue()  # fvBD is untouched, so startup is happy
        with pytest.raises(DescriptionsLoadError, match=r"mo\.dn_formats"):
            catalog.load_schema("fvTenant")


def test_a_still_valid_residual_blob_passes(broken_catalogue):
    """The guard must reject broken encodings, not merely *changed bytes*.

    Rewriting `residual` with different but correctly-encoded content has to
    pass, or the guard is asserting the catalogue's contents rather than its
    shape — and would fail on every legitimate APIC model update.
    """
    payload = zlib.compress(
        b'{"containedBy": {"fv:Tenant": ""}, "contains": {"fv:Subnet": ""}}'
    )
    broken_catalogue(f"UPDATE mo SET residual = x'{payload.hex()}' WHERE class_name = 'fvBD'")
    catalog.verify_catalogue()
