"""Unit tests for registry.schema.load_schema."""

import pytest
from registry.schema import load_schema


def test_unknown_class_returns_empty_dict(schemas_dir):
    assert load_schema("nonExistentClassXYZ", schemas_dir) == {}


@pytest.mark.skipif(
    not __import__("pathlib").Path(__file__).parent.parent.parent.joinpath("data", "schemas").exists(),
    reason="schemas/ collection not available",
)
class TestWithSchemas:
    def test_known_class_returns_non_empty(self, schemas_dir):
        schema = load_schema("fvBD", schemas_dir)
        assert schema != {}

    def test_required_fields_present(self, schemas_dir):
        schema = load_schema("fvBD", schemas_dir)
        for field in ("identifiedBy", "rnFormat", "containedBy"):
            assert field in schema, f"Missing field: {field}"

    def test_properties_is_sorted_list(self, schemas_dir):
        schema = load_schema("fvBD", schemas_dir)
        props = schema.get("properties", [])
        assert isinstance(props, list)
        assert props == sorted(props)

    def test_relation_to_simplified(self, schemas_dir):
        schema = load_schema("fvBD", schemas_dir)
        for rel_data in schema.get("relationTo", {}).values():
            assert "targetClass" in rel_data
            assert "cardinality" in rel_data

    def test_relation_from_simplified(self, schemas_dir):
        schema = load_schema("fvBD", schemas_dir)
        for rel_data in schema.get("relationFrom", {}).values():
            assert "sourceClass" in rel_data

    def test_abstract_class(self, schemas_dir):
        # fvBD is not abstract
        schema = load_schema("fvBD", schemas_dir)
        assert schema.get("isAbstract") is False

    def test_fault_inst_has_no_label(self, schemas_dir):
        # faultInst has no label in the schema
        schema = load_schema("faultInst", schemas_dir)
        assert schema != {}
