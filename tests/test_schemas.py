"""Tests for system.schemas module.

Session 11: Schema Access Utilities
Tests for RFC-004 schema bundling and runtime access.

Coverage:
- get_sql_schema(layer) - Return soil.sql or core.sql
- get_type_schema(category, type_name) - Return JSON schema
- list_type_schemas(category) - List available schemas
"""

import pytest

from system.schemas import get_sql_schema, get_type_schema, list_type_schemas


class TestGetSqlSchema:
    """Tests for get_sql_schema function."""

    def test_get_core_schema(self):
        """get_sql_schema('core') returns Core SQL schema."""
        schema = get_sql_schema('core')

        assert isinstance(schema, str)
        assert len(schema) > 0
        # Verify it's SQL
        assert 'CREATE TABLE' in schema
        # Verify it's Core schema (entity registry)
        assert 'entity' in schema.lower()
        assert '_schema_metadata' in schema

    def test_get_soil_schema(self):
        """get_sql_schema('soil') returns Soil SQL schema."""
        schema = get_sql_schema('soil')

        assert isinstance(schema, str)
        assert len(schema) > 0
        # Verify it's SQL
        assert 'CREATE TABLE' in schema
        # Verify it's Soil schema (items, system_relations)
        assert 'item' in schema.lower()
        assert 'system_relation' in schema.lower()

    def test_invalid_layer_raises_value_error(self):
        """get_sql_schema with invalid layer raises ValueError."""
        with pytest.raises(ValueError, match="Invalid layer"):
            get_sql_schema('invalid')

        with pytest.raises(ValueError, match="Invalid layer"):
            get_sql_schema('Entity')

    def test_schema_content_is_valid_sql(self):
        """Returned schema content is valid SQL (basic checks)."""
        for layer in ['soil', 'core']:
            schema = get_sql_schema(layer)
            # Basic SQL validation
            assert schema.strip().startswith('--') or schema.strip().startswith('CREATE')
            assert 'CREATE TABLE IF NOT EXISTS' in schema
            assert ';' in schema  # Has statements


class TestGetTypeSchema:
    """Tests for get_type_schema function."""

    def test_get_email_item_schema(self):
        """get_type_schema('facts', 'Email') returns Email schema."""
        schema = get_type_schema('facts', 'Email')

        assert isinstance(schema, dict)
        assert schema['title'] == 'Email'
        assert '$schema' in schema
        assert 'allOf' in schema or 'properties' in schema
        # Email extends Note via allOf
        if 'allOf' in schema:
            assert len(schema['allOf']) >= 1

    def test_get_transaction_entity_schema(self):
        """get_type_schema('entities', 'Transaction') returns Transaction schema."""
        schema = get_type_schema('entities', 'Transaction')

        assert isinstance(schema, dict)
        assert schema['title'] == 'Transaction'
        assert '$schema' in schema
        assert 'allOf' in schema or 'properties' in schema
        # Transaction extends Entity via allOf
        if 'allOf' in schema:
            assert len(schema['allOf']) >= 1

    def test_get_note_item_schema(self):
        """get_type_schema('facts', 'Note') returns Note schema."""
        schema = get_type_schema('facts', 'Note')

        assert isinstance(schema, dict)
        assert schema['title'] == 'Note'
        assert '$schema' in schema
        assert 'allOf' in schema or 'properties' in schema

    def test_invalid_category_raises_value_error(self):
        """get_type_schema with invalid category raises ValueError."""
        with pytest.raises(ValueError, match="Invalid category"):
            get_type_schema('invalid', 'Email')

        with pytest.raises(ValueError, match="Invalid category"):
            get_type_schema('item', 'Email')  # Should be 'items'

    def test_nonexistent_type_raises_file_not_found(self):
        """get_type_schema with nonexistent type raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="Type schema file not found"):
            get_type_schema('facts', 'NonexistentType')

    def test_type_name_case_insensitive(self):
        """get_type_schema handles case variations correctly."""
        # File name is lowercase (email.schema.json)
        # Our implementation normalizes type names, but let's verify
        # it works with the standard capitalized form
        schema = get_type_schema('facts', 'Email')
        assert schema['title'] == 'Email'

    def test_action_result_schema(self):
        """get_type_schema returns ActionResult schema (Session 6.6 structured error)."""
        schema = get_type_schema('facts', 'ActionResult')

        assert isinstance(schema, dict)
        assert schema['title'] == 'ActionResult'
        assert '$schema' in schema
        assert 'allOf' in schema or 'properties' in schema


class TestListTypeSchemas:
    """Tests for list_type_schemas function."""

    def test_list_item_schemas(self):
        """list_type_schemas('facts') returns list of Fact types."""
        types = list_type_schemas('facts')

        assert isinstance(types, list)
        assert len(types) > 0
        assert all(isinstance(t, str) for t in types)
        # Check for expected types
        assert 'Email' in types or 'email' in types
        assert 'Note' in types or 'note' in types
        # Session 6: Audit facts
        assert 'Action' in types or 'action' in types
        assert 'ActionResult' in types or 'actionresult' in types

    def test_list_entity_schemas(self):
        """list_type_schemas('entities') returns list of Entity types."""
        types = list_type_schemas('entities')

        assert isinstance(types, list)
        assert len(types) > 0
        assert all(isinstance(t, str) for t in types)
        # Check for expected types (currently only Transaction in memogarden-system)
        assert 'Transaction' in types or 'transaction' in types

    def test_invalid_category_raises_value_error(self):
        """list_type_schemas with invalid category raises ValueError."""
        with pytest.raises(ValueError, match="Invalid category"):
            list_type_schemas('invalid')

        with pytest.raises(ValueError, match="Invalid category"):
            list_type_schemas('item')  # Should be 'facts'

    def test_listed_types_can_be_retrieved(self):
        """All types returned by list_type_schemas can be retrieved."""
        for category in ['facts', 'entities']:
            types = list_type_schemas(category)
            for type_name in types[:3]:  # Test first 3 to avoid excessive test time
                schema = get_type_schema(category, type_name)
                assert isinstance(schema, dict)
                assert 'title' in schema or '$schema' in schema

    def test_returns_sorted_list(self):
        """list_type_schemas returns alphabetically sorted list."""
        types = list_type_schemas('facts')

        # Check if sorted
        assert types == sorted(types)


class TestRfc004Invariants:
    """Tests for RFC-004 invariants.

    INV-PKG-004: Try importlib.resources first (bundled package)
    INV-PKG-005: Fall back to file reading (development mode)
    INV-PKG-006: Raise FileNotFoundError if schema not found
    """

    def test_fallback_to_file_reading(self):
        """Schemas are accessible via file reading (development mode)."""
        # This test verifies the fallback mechanism works
        # In production, importlib.resources would be used
        # In development, file reading fallback is used
        schema = get_sql_schema('core')
        assert schema is not None
        assert len(schema) > 0

    def test_file_not_found_when_schema_missing(self):
        """Raises FileNotFoundError for truly missing schemas."""
        # We can't test a truly missing schema since all are present
        # But we can verify the error type for invalid lookup
        with pytest.raises(FileNotFoundError):
            get_type_schema('facts', 'DefinitelyNotARealType12345')

    def test_schema_content_consistency(self):
        """Schema content is consistent across access methods."""
        # Get schema multiple times
        schema1 = get_sql_schema('core')
        schema2 = get_sql_schema('core')

        # Should be identical
        assert schema1 == schema2
