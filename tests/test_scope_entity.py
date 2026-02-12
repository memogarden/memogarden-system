"""Tests for Scope entity operations.

Session 15: Scope Entity and Schema
Tests for Scope entity CRUD operations through the generic EntityOperations API.
"""

import pytest

from system.exceptions import ResourceNotFound
from system.schemas import get_type_schema, list_type_schemas
from system.utils import uid


class TestScopeEntityOperations:
    """Tests for Scope entity CRUD operations."""

    def test_scope_schema_exists(self, db_core):
        """Scope schema is available and valid."""
        schema = get_type_schema('entities', 'Scope')

        assert isinstance(schema, dict)
        assert schema['title'] == 'Scope'
        assert '$schema' in schema
        assert 'allOf' in schema or 'properties' in schema

        # Verify Scope extends Entity
        if 'allOf' in schema:
            assert any(ref.get('$ref') == 'entity.schema.json' for ref in schema['allOf'])

    def test_create_scope_minimal(self, db_core):
        """Create Scope with minimal required fields."""
        # Create scope with only label
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Test Scope'}
        )

        # Verify entity was created
        entity = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity is not None
        assert entity['type'] == 'Scope'
        assert entity['data']['label'] == 'Test Scope'

    def test_create_scope_with_participants(self, db_core):
        """Create Scope with active participants."""
        participant_id = uid.generate_uuid()
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={
                'label': 'Team Project',
                'active_participants': [participant_id]
            }
        )

        entity = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity['data']['active_participants'] == [participant_id]

    def test_create_scope_with_artifacts(self, db_core):
        """Create Scope with artifacts."""
        artifact_id = uid.generate_uuid()
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={
                'label': 'Artifact Collection',
                'artifact_uuids': [artifact_id]
            }
        )

        entity = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity['data']['artifact_uuids'] == [artifact_id]

    def test_create_scope_full(self, db_core):
        """Create Scope with all fields."""
        participant_id = uid.generate_uuid()
        artifact_id = uid.generate_uuid()
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={
                'label': 'Complete Scope',
                'active_participants': [participant_id],
                'artifact_uuids': [artifact_id]
            }
        )

        entity = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity['data']['label'] == 'Complete Scope'
        assert entity['data']['active_participants'] == [participant_id]
        assert entity['data']['artifact_uuids'] == [artifact_id]

    def test_get_scope_by_id(self, db_core):
        """Retrieve Scope by UUID."""
        # Create a scope
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Get Test'}
        )

        # Get by plain UUID
        entity = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity is not None
        assert entity['uuid'] == scope_uuid
        assert entity['type'] == 'Scope'

    def test_get_scope_by_id_with_prefix(self, db_core):
        """Retrieve Scope by UUID with core_ prefix."""
        # Create a scope
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Prefix Test'}
        )

        # Get with prefix
        entity = db_core.entity.get_by_id(scope_uuid, entity_type='Scope')
        assert entity is not None
        assert entity['uuid'] == scope_uuid

    def test_update_scope_data(self, db_core):
        """Update Scope data fields."""
        # Create scope
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Original Label'}
        )

        # Update data
        db_core.entity.update_data(scope_uuid, data={
            'label': 'Updated Label',
            'active_participants': [uid.generate_uuid()]
        })

        # Verify update
        entity = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity['data']['label'] == 'Updated Label'
        assert 'active_participants' in entity['data']

    def test_query_scopes_by_type(self, db_core):
        """Query Scopes filtering by type."""
        # Create multiple scopes
        db_core.entity.create(entity_type='Scope', data={'label': 'Scope 1'})
        db_core.entity.create(entity_type='Scope', data={'label': 'Scope 2'})
        db_core.entity.create(entity_type='Artifact', data={'label': 'Not a Scope'})

        # Query only Scopes
        results, _ = db_core.entity.query_with_filters(
            entity_type='Scope',
            limit=10
        )

        # Should return 2 scopes
        assert len(results) == 2
        assert all(r['type'] == 'Scope' for r in results)

    def test_query_scopes_with_limit(self, db_core):
        """Query Scopes with limit."""
        # Create 5 scopes
        for i in range(5):
            db_core.entity.create(entity_type='Scope', data={'label': f'scope {i}'})

        # Query with limit 3
        results, _ = db_core.entity.query_with_filters(
            entity_type='Scope',
            limit=3
        )

        assert len(results) == 3

    def test_query_scopes_with_offset(self, db_core):
        """Query Scopes with offset."""
        # Create 5 scopes
        for i in range(5):
            db_core.entity.create(entity_type='Scope', data={'label': f'scope {i}'})

        # Query with offset 2, limit 2
        results, _ = db_core.entity.query_with_filters(
            entity_type='Scope',
            limit=2,
            offset=2
        )

        # Query uses ORDER BY created_at DESC (newest first)
        # Created order: 0, 1, 2, 3, 4
        # DESC order: 4, 3, 2, 1, 0
        # offset=2 skips 4, 3 and returns: 2, 1
        assert len(results) == 2
        assert 'scope 2' in results[0]['data']['label']
        assert 'scope 1' in results[1]['data']['label']

    def test_supersede_scope(self, db_core):
        """Supersede old Scope with new Scope."""
        # Create original scope
        old_scope = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Old Scope'}
        )

        # Create new scope
        new_scope = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'New Scope'}
        )

        # Supersede
        db_core.entity.supersede(old_scope, new_scope)

        # Verify old scope is superseded
        old_entity = db_core.entity.get_by_id(uid.strip_prefix(old_scope), entity_type='Scope')
        assert old_entity['superseded_by'] == new_scope
        assert old_entity['superseded_at'] is not None

    def test_nonexistent_scope_raises_not_found(self, db_core):
        """Getting nonexistent Scope raises ResourceNotFound."""
        fake_scope = uid.generate_uuid()

        with pytest.raises(ResourceNotFound, match='Scope.*not found'):
            db_core.entity.get_by_id(fake_scope, entity_type='Scope')

    def test_scope_hash_chain(self, db_core):
        """Scope maintains hash chain through updates."""
        # Create scope
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Hash Chain Test'}
        )

        # Get initial hash
        entity1 = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        hash1 = entity1['hash']

        # Update data
        db_core.entity.update_data(scope_uuid, data={'label': 'Updated'})

        # Get new hash
        entity2 = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        hash2 = entity2['hash']

        # Hashes should be different
        assert hash1 != hash2

    def test_scope_version_increment(self, db_core):
        """Scope version increments on updates."""
        # Create scope
        scope_uuid = db_core.entity.create(
            entity_type='Scope',
            data={'label': 'Version Test'}
        )

        # Initial version is 1
        entity1 = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity1['version'] == 1

        # Update data
        db_core.entity.update_data(scope_uuid, data={'label': 'Updated'})

        # Version should increment to 2
        entity2 = db_core.entity.get_by_id(uid.strip_prefix(scope_uuid), entity_type='Scope')
        assert entity2['version'] == 2


class TestScopeInListTypeSchemas:
    """Tests for Scope in list_type_schemas output."""

    def test_scope_in_entity_schemas(self):
        """Scope appears in entity types list."""
        entity_types = list_type_schemas('entities')

        assert isinstance(entity_types, list)
        assert 'Scope' in entity_types or 'scope' in entity_types
