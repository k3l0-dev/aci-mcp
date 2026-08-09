# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""The catalogue adapter must reproduce the jsonmeta reader exactly.

Iteration 2 of the 2.0 migration lands ``registry/catalog.py`` without wiring it
into anything. That is deliberate: the adapter can be proven — or disproven —
against the live jsonmeta corpus while the server still runs entirely on the old
path, so a failure here costs nothing.

These tests are the proof. They fall into four groups:

1. **Parity** — the adapter's output equals the jsonmeta reader's, field by
   field, across a large random sample plus every shape known to be awkward.
   The one accepted divergence (``mo:*`` register options) is asserted to be
   *exactly* that and nothing else, so a second divergence cannot hide behind it.
2. **Index equality** — the rebuilt search index equals ``class-descriptions.json``
   entry for entry. This is what protects the 78.4 % recall.
3. **Wire-only** — no ``readable`` property name can escape the adapter. This is
   the trap that returns ``[]`` from the APIC with no error.
4. **Shape** — the two known leaks (``defaultValue`` in options, ``"null"`` as a
   comment) stay filtered.

Tests that need the jsonmeta corpus skip when it is absent, so CI without the
data bundle still runs the rest rather than failing for the wrong reason.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from niwashi_mcp.registry import catalog, descriptions
from tests.fixtures import jsonmeta_oracle

pytestmark = pytest.mark.catalog

# Shapes that broke, or could plausibly break, the projection. Each is here for
# a reason, not for coverage.
AWKWARD_CLASSES = [
    "fvBD",                  # the reference class, one dnFormat
    "fvSubnet",              # twelve dnFormats — the multi-template case
    "fvTenant",              # top of the containment tree
    "fvRsCtx",               # Rs relation, colon notation
    "fvRtBd",                # Rt relation, the incoming direction
    "faultInst",             # 24,151 dnFormats
    "faultDelegate",         # 64,313 dnFormats — the non-truncation canary
    "fvATg",                 # abstract
    "eqptIngrTotal5min",     # stats class
    "actionAeSubj",          # mo:* register — the accepted divergence
    "tagTag",                # attachable everywhere, huge containedBy
    "polUni",                # empty-ish, tests the always-emitted keys
]


@pytest.fixture(scope="module")
def frozen_classes() -> list[str]:
    """Classes with a frozen jsonmeta file, from the fixture manifest.

    Shipped in the repository (2.4 MB), so parity is verifiable in CI and stays
    verifiable after `data/` is deleted — unlike the 1.7 GB corpus, which was
    never in git and is gone from the tree in 2.0.
    """
    manifest = json.loads(
        (jsonmeta_oracle.FIXTURE_DIR / "MANIFEST.json").read_text()
    )
    return manifest["classes"]


@pytest.fixture(scope="module")
def all_class_names() -> list[str]:
    return [r[0] for r in catalog._connect().execute("SELECT class_name FROM mo")]


def _divergence(a: dict, b: dict) -> set[str]:
    """Field names that differ between two schema dicts."""
    return {k for k in set(a) | set(b) if a.get(k) != b.get(k)}


# ─────────────────────────────────────────────────────────── parity


class TestSchemaParity:
    def test_awkward_shapes_match_exactly(self, frozen_classes):
        """Every deliberately-chosen difficult class, with property details.

        ``actionAeSubj`` is excluded here and asserted separately: it carries a
        ``mo:*`` register whose options the catalogue drops on purpose.
        """
        # Assert the skipped set is EXACTLY the four known to be absent from the
        # frozen fixtures, rather than skipping "whatever is missing". A fixture
        # quietly dropping out of the bundle would otherwise shrink this test in
        # silence — the docstring says "every deliberately-chosen difficult
        # class", and it should stop being true loudly.
        skipped = {c for c in AWKWARD_CLASSES if c not in frozen_classes}
        assert skipped == {"faultInst", "faultDelegate", "actionAeSubj", "tagTag"}, (
            f"the set of classes this test cannot check has changed: {sorted(skipped)}. "
            f"They are covered by baseline digests instead; update both together."
        )

        failures = []
        for cls in AWKWARD_CLASSES:
            if cls in skipped:
                continue  # covered by tests/baseline/ digests, asserted above
            old = jsonmeta_oracle.project(cls, include_property_details=True)
            new = catalog.load_schema(cls, include_property_details=True)
            if old != new:
                failures.append(f"{cls}: {sorted(_divergence(old, new))}")
        assert not failures, "parity broken:\n  " + "\n  ".join(failures)

    def test_every_frozen_class_matches_the_oracle(self, frozen_classes):
        """All 31 frozen classes, derived independently from the vendor's files.

        This is the strongest parity evidence that survives the deletion of
        ``data/``: the expected value is *computed* from raw jsonmeta by the 1.x
        projection, not read back from something this project recorded of
        itself. A snapshot cannot catch an error made in both the recording and
        the implementation; an independent derivation can.
        """
        unexpected = []
        for cls in frozen_classes:
            old = jsonmeta_oracle.project(cls, include_property_details=True)
            new = catalog.load_schema(cls, include_property_details=True)
            if old == new:
                continue
            if _divergence(old, new) != {"property_details"}:
                unexpected.append(f"{cls}: {sorted(_divergence(old, new))}")
                continue
            for name in set(old["property_details"]) | set(new["property_details"]):
                d_old = old["property_details"].get(name, {})
                d_new = new["property_details"].get(name, {})
                if d_old == d_new:
                    continue
                changed = {k for k in set(d_old) | set(d_new) if d_old.get(k) != d_new.get(k)}
                is_accepted = changed == {"options"} and str(
                    d_old.get("type", "")
                ).startswith("mo:")
                if not is_accepted:
                    unexpected.append(f"{cls}.{name}: {sorted(changed)} type={d_old.get('type')}")
        assert not unexpected, "unexpected divergence:\n  " + "\n  ".join(unexpected[:15])

    def test_plain_call_omits_property_details(self):
        """The cheap call must stay cheap — details only on request."""
        assert "property_details" not in catalog.load_schema("fvBD")

    def test_properties_filter_preserves_caller_order(self):
        result = catalog.load_schema("fvSubnet", properties_filter=["scope", "preferred"])
        assert list(result["property_details"]) == ["scope", "preferred"]

    def test_properties_filter_skips_unknown_names_silently(self):
        """Matches the previous contract: an unknown name is dropped, not an error."""
        result = catalog.load_schema("fvBD", properties_filter=["name", "notAProperty"])
        assert list(result["property_details"]) == ["name"]

    def test_always_emitted_keys_are_present_even_when_empty(self, all_class_names):
        """The nine scalar keys exist on every class, empty or not.

        The jsonmeta reader copied them whenever the source had the key, and the
        source always does. Omitting an empty one would turn ``schema["label"]``
        into a KeyError for a caller that never saw one before.
        """
        random.seed(4)
        required = {"identifiedBy", "rnFormat", "containedBy", "dnFormats", "label"}
        for cls in random.sample(all_class_names, 300):
            result = catalog.load_schema(cls)
            assert required <= set(result), f"{cls} is missing {sorted(required - set(result))}"


class TestUnknownClass:
    def test_unknown_class_returns_empty_dict(self):
        """An empty dict, never an exception — an agent recovers from one, not the other."""
        assert catalog.load_schema("fvNotARealClass") == {}

    def test_class_exists_is_false_for_unknown(self):
        assert catalog.class_exists("fvNotARealClass") is False

    def test_class_exists_is_case_sensitive(self):
        """``fvBd`` is not ``fvBD``.

        The old reader needed an explicit guard because a case-insensitive
        filesystem resolved the wrong file. SQLite's BINARY collation makes it
        structurally impossible — this test proves the property still holds, so
        that adding ``COLLATE NOCASE`` some day fails loudly.
        """
        assert catalog.class_exists("fvBD") is True
        assert catalog.class_exists("fvBd") is False
        assert catalog.load_schema("fvBd") == {}


class TestDnFormats:
    def test_high_cardinality_classes_are_not_truncated(self):
        """The anti-hallucination anchor is only worth anything if it is complete."""
        assert len(catalog.load_schema("faultDelegate")["dnFormats"]) == 64_313
        assert len(catalog.load_schema("faultInst")["dnFormats"]) == 24_151

    def test_empty_dn_formats_is_an_empty_list_not_a_missing_key(self):
        """Stored as NULL, but the source has the key on all 15,452 classes."""
        result = catalog.load_schema("aaaADomainRef")
        assert result["dnFormats"] == []

    def test_known_template_is_verbatim(self):
        assert catalog.load_schema("fvBD")["dnFormats"] == ["uni/tn-{name}/BD-{name}"]


# ─────────────────────────────────────────────────────────── index


class TestDescriptionsIndex:
    @pytest.fixture(scope="class")
    @classmethod
    def rebuilt(cls) -> dict:
        return catalog.descriptions_index()

    @pytest.fixture(scope="class")
    @classmethod
    def recorded(cls) -> dict:
        """The index as recorded from `class-descriptions.json` before 2.0.

        The file itself is deleted; its digest and class count survive in
        `tests/baseline/baseline.json`. Comparing against the recording keeps
        the guarantee testable — pointing at the deleted file made these tests
        skip silently, which is worse than not having them.
        """
        path = Path(__file__).resolve().parents[1] / "baseline" / "baseline.json"
        if not path.exists():
            pytest.skip("baseline.json not recorded")
        return json.loads(path.read_text())["index"]

    def test_same_class_count(self, rebuilt, recorded):
        """The 213-class gap is a property of the index filter, not an accident."""
        assert len(rebuilt) == recorded["class_count"]

    def test_index_is_byte_identical_to_the_recording(self, rebuilt, recorded):
        """Digest equality against the pre-2.0 recording.

        This is the claim the whole migration rests on: the rebuilt index is
        not merely equivalent, it is byte-identical to the JSON file it
        replaced.
        """
        import hashlib

        digest = hashlib.sha256(
            json.dumps(rebuilt, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest == recorded["digest"], (
            "the rebuilt index no longer matches the pre-2.0 recording"
        )

    def test_prop_labels_is_a_list(self, rebuilt):
        """Shape matters: the search tokeniser iterates it.

        A string would still "work" — it would tokenise character by character
        and silently destroy the ranking. Pinned explicitly for that reason.
        """
        with_labels = next(v for v in rebuilt.values() if "prop_labels" in v)
        assert isinstance(with_labels["prop_labels"], list)

    def test_generic_labels_are_filtered(self, rebuilt):
        """"Name" on every class would make one query match all 15,000."""
        for entry in rebuilt.values():
            assert "Name" not in entry.get("prop_labels", [])

    def test_search_still_finds_the_reference_class(self, rebuilt):
        """End-to-end: the scorer over the rebuilt index still answers correctly."""
        hits = descriptions.search("bridge domain", rebuilt, limit=5)
        assert hits[0]["class_name"] == "fvBD"


# ─────────────────────────────────────────────────────────── traps


class TestWireOnlyBoundary:
    """No ``readable`` name may cross the adapter.

    The catalogue stores both ``descr`` (wire) and ``description`` (readable).
    A readable name passed as a filter key is a valid identifier, reaches the
    APIC, and returns ``[]`` with no error — the exact silent failure this
    server exists to prevent. 2,207 properties would slip past filter.py's regex.
    """

    def test_known_renamed_properties_expose_the_wire_name(self):
        for cls, wire, readable in [
            ("fvBD", "descr", "description"),
            ("fvSubnet", "ip", "subnet"),
        ]:
            props = catalog.load_schema(cls)["properties"]
            assert wire in props, f"{cls}: wire name {wire!r} missing"
            assert readable not in props, f"{cls}: readable name {readable!r} leaked"

    def test_no_readable_name_leaks_across_configurable_classes(self):
        """Swept, not spot-checked: every readable name that differs from its wire name.

        Sampled across configurable classes, which is where filters are used.
        """
        # Actually swept. The previous version said "swept" and did
        # `LIMIT 4000` then `[:150]` — 150 of 3,010 configurable classes, 5 %,
        # chosen by an unordered SELECT. The whole sweep costs ~0.12 s, so the
        # sampling bought nothing and cost the guarantee the docstring claimed.
        rows = catalog._query(
            "SELECT m.class_name, p.wire_name FROM prop p JOIN mo m ON m.id = p.class_id "
            "WHERE m.is_configurable = 1"
        )
        by_class: dict[str, set[str]] = {}
        for cls, wire in rows:
            by_class.setdefault(cls, set()).add(wire)

        assert len(by_class) > 2_500, (
            f"only {len(by_class)} configurable classes reached the sweep — "
            f"the query stopped covering the corpus"
        )
        for cls, wires in by_class.items():
            exposed = set(catalog.load_schema(cls)["properties"])
            assert exposed <= wires, f"{cls} exposed non-wire names: {sorted(exposed - wires)}"


class TestShapeLeaks:
    def test_default_value_marker_never_appears_in_options(self):
        """Present on 90 % of enum blobs; not a value the APIC accepts."""
        details = catalog.load_schema("fvSubnet", properties_filter=["scope"])["property_details"]
        assert "defaultValue" not in details["scope"]["options"]
        assert details["scope"]["options"] == ["private", "public", "shared"]

    def test_null_sentinel_never_appears_as_a_comment(self, all_class_names):
        """4,463 property rows store the literal string "null" for "no comment"."""
        random.seed(9)
        for cls in random.sample(all_class_names, 120):
            details = catalog.load_schema(cls, include_property_details=True).get(
                "property_details", {}
            )
            for name, detail in details.items():
                assert detail.get("comment") != "null", f"{cls}.{name} leaked the null sentinel"

    def test_access_is_always_present_and_from_the_known_set(self, all_class_names):
        random.seed(13)
        allowed = {"read-write", "create-only", "read-only"}
        for cls in random.sample(all_class_names, 120):
            details = catalog.load_schema(cls, include_property_details=True).get(
                "property_details", {}
            )
            for name, detail in details.items():
                assert detail.get("access") in allowed, f"{cls}.{name}: {detail.get('access')}"


class TestCatalogueIntegrity:
    def test_flag_layout_is_read_from_the_manifest(self):
        """Hard-coding the bit layout would break silently when niwaki extends it."""
        bits = catalog._flag_bits()
        assert bits["isConfigurable"] == 1
        assert bits["isNaming"] == 32
        assert "isHidden" in bits

    def test_apic_version_is_exposed(self):
        """Pinned by the dependency from 2.0 on — it must be loggable at startup."""
        assert catalog.apic_version() == "6.0(9c)"

    def test_catalogue_ships_inside_the_installed_package(self):
        assert catalog.catalog_path().is_file()
