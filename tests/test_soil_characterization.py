"""Characterization tests for Soil package.

These tests capture current behavior to ensure refactoring doesn't break anything.
Run these before and after each refactoring step.

If these tests fail after a refactoring, the refactoring changed behavior.
"""

import pytest
import tempfile
from pathlib import Path
from system.soil import Soil, Fact, SystemRelation, Evidence, generate_soil_uuid, SOIL_UUID_PREFIX


class TestSoilUUIDs:
    """Characterize Soil UUID generation behavior."""

    def test_uuid_has_prefix(self):
        """All Soil UUIDs should start with 'soil_' prefix."""
        uuid = generate_soil_uuid()
        assert uuid.startswith(SOIL_UUID_PREFIX), f"UUID {uuid} missing prefix {SOIL_UUID_PREFIX}"

    def test_uuid_format(self):
        """Soil UUIDs should be: 'soil_' + standard UUID4."""
        uuid = generate_soil_uuid()
        assert len(uuid) == len(SOIL_UUID_PREFIX) + 36  # 4 + 36 for UUID4
        # UUID4 format: 8-4-4-4-12 hex digits
        uuid_part = uuid[len(SOIL_UUID_PREFIX):]
        assert len(uuid_part) == 36

    def test_uuid_is_unique(self):
        """Each call should generate a unique UUID."""
        uuids = [generate_soil_uuid() for _ in range(100)]
        assert len(set(uuids)) == 100, "Generated duplicate UUIDs"


class TestItemCreation:
    """Characterize Item creation behavior."""

    def test_item_can_be_created(self):
        """Items can be created with minimum required fields."""
        item = Fact(
            uuid=generate_soil_uuid(),
            _type="Note",
            realized_at="2026-01-30T12:00:00Z",
            canonical_at="2026-01-30T12:00:00Z",
            data={"description": "Test note"}
        )
        assert item._type == "Note"
        assert item.data["description"] == "Test note"

    def test_item_stored_in_database(self):
        """Items can be stored and retrieved from database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                item = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Test note", "count": 42}
                )
                uuid = soil.create_fact(item)

                # Verify item was created
                retrieved = soil.get_fact(uuid)
                assert retrieved is not None
                assert retrieved._type == "Note"
                assert retrieved.data["description"] == "Test note"
                assert retrieved.data["count"] == 42

    def test_item_data_preserved_as_json(self):
        """Item.data should be stored and retrieved as JSON dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                item = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={
                        "description": "Test",
                        "count": 42,
                        "nested": {"key": "value"}
                    }
                )
                soil.create_fact(item)

                # Verify nested data is preserved
                retrieved = soil.get_fact(item.uuid)
                assert retrieved.data["description"] == "Test"
                assert retrieved.data["count"] == 42
                assert retrieved.data["nested"]["key"] == "value"

    def test_item_metadata_is_optional(self):
        """Items can be created without metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                item = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Test"}
                )
                soil.create_fact(item)

                retrieved = soil.get_fact(item.uuid)
                assert retrieved.metadata is None or retrieved.metadata == {}

    def test_item_metadata_is_stored(self):
        """Items can have metadata stored."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                item = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Email",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"rfc_message_id": "<test@example.com>"},
                    metadata={"provider": "test", "labels": ["INBOX"]}
                )
                soil.create_fact(item)

                retrieved = soil.get_fact(item.uuid)
                assert retrieved.metadata is not None
                assert retrieved.metadata["provider"] == "test"
                assert retrieved.metadata["labels"] == ["INBOX"]

    def test_item_uuid_without_prefix_is_accepted(self):
        """get_fact() should accept UUIDs without 'soil_' prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                item = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Test"}
                )
                soil.create_fact(item)

                # Get UUID without prefix
                uuid_without_prefix = item.uuid[len(SOIL_UUID_PREFIX):]
                retrieved = soil.get_fact(uuid_without_prefix)
                assert retrieved is not None
                assert retrieved.uuid == item.uuid


class TestEmailDeduplication:
    """Characterize email deduplication behavior."""

    def test_email_found_by_rfc_message_id(self):
        """Emails can be found by rfc_message_id."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                message_id = "<test@example.com>"
                email = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Email",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={
                        "rfc_message_id": message_id,
                        "from_address": "sender@example.com",
                        "to_addresses": ["recipient@example.com"]
                    }
                )
                soil.create_fact(email)

                # Should find by rfc_message_id
                found = soil.find_item_by_rfc_message_id(message_id)
                assert found is not None
                assert found.uuid == email.uuid
                assert found.data["from_address"] == "sender@example.com"

    def test_email_not_found_with_different_message_id(self):
        """find_item_by_rfc_message_id returns None for non-existent ID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                found = soil.find_item_by_rfc_message_id("<nonexistent@example.com>")
                assert found is None


class TestSystemRelations:
    """Characterize SystemRelation behavior."""

    def test_relation_can_be_created(self):
        """System relations can be created."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create two items
                item1 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Item 1"}
                )
                item2 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"description": "Item 2"}
                )
                soil.create_fact(item1)
                soil.create_fact(item2)

                # Create relation
                relation = SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="cites",
                    source=item2.uuid,
                    source_type="item",
                    target=item1.uuid,
                    target_type="item",
                    created_at=2230
                )
                uuid = soil.create_relation(relation)

                assert uuid is not None

    def test_duplicate_relation_returns_same_uuid(self):
        """Creating duplicate relation should return existing UUID (idempotent)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create two items
                item1 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Item 1"}
                )
                item2 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"description": "Item 2"}
                )
                soil.create_fact(item1)
                soil.create_fact(item2)

                # Create relation
                relation = SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="cites",
                    source=item2.uuid,
                    source_type="item",
                    target=item1.uuid,
                    target_type="item",
                    created_at=2230
                )
                uuid1 = soil.create_relation(relation)

                # Try to create duplicate - should return existing UUID
                relation.uuid = generate_soil_uuid()  # New UUID
                uuid2 = soil.create_relation(relation)

                # Should return same UUID (relation already exists)
                assert uuid1 == uuid2

    def test_relations_can_be_filtered_by_source(self):
        """Relations can be queried by source UUID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create three items
                item1 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Item 1"}
                )
                item2 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"description": "Item 2"}
                )
                item3 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:02:00Z",
                    canonical_at="2026-01-30T12:02:00Z",
                    data={"description": "Item 3"}
                )
                soil.create_fact(item1)
                soil.create_fact(item2)
                soil.create_fact(item3)

                # Create relations: item2 cites item1, item3 cites item1
                soil.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="cites",
                    source=item2.uuid,
                    source_type="item",
                    target=item1.uuid,
                    target_type="item",
                    created_at=2230
                ))
                soil.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="cites",
                    source=item3.uuid,
                    source_type="item",
                    target=item1.uuid,
                    target_type="item",
                    created_at=2230
                ))

                # Query relations from item2
                relations = soil.get_relations(source=item2.uuid)
                assert len(relations) == 1
                assert relations[0].kind == "cites"
                assert relations[0].source == item2.uuid

    def test_relations_can_be_filtered_by_kind(self):
        """Relations can be queried by kind."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create two items
                item1 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Item 1"}
                )
                item2 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"description": "Item 2"}
                )
                soil.create_fact(item1)
                soil.create_fact(item2)

                # Create relations of different kinds
                soil.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="cites",
                    source=item2.uuid,
                    source_type="item",
                    target=item1.uuid,
                    target_type="item",
                    created_at=2230
                ))
                soil.create_relation(SystemRelation(
                    uuid=generate_soil_uuid(),
                    kind="replies_to",
                    source=item2.uuid,
                    source_type="item",
                    target=item1.uuid,
                    target_type="item",
                    created_at=2230
                ))

                # Query by kind
                cites_relations = soil.get_relations(source=item2.uuid, kind="cites")
                replies_relations = soil.get_relations(source=item2.uuid, kind="replies_to")

                assert len(cites_relations) == 1
                assert len(replies_relations) == 1
                assert cites_relations[0].kind == "cites"
                assert replies_relations[0].kind == "replies_to"

    def test_replies_to_relation_helper(self):
        """create_replies_to_relation() creates replies_to relation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create parent and reply items
                parent = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Email",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"rfc_message_id": "<parent@example.com>"}
                )
                reply = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Email",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"rfc_message_id": "<reply@example.com>"}
                )
                soil.create_fact(parent)
                soil.create_fact(reply)

                # Create replies_to relation
                relation_uuid = soil.create_replies_to_relation(
                    reply_uuid=reply.uuid,
                    parent_uuid=parent.uuid
                )

                assert relation_uuid is not None

                # Verify relation was created
                relations = soil.get_relations(source=reply.uuid, kind="replies_to")
                assert len(relations) == 1
                assert relations[0].target == parent.uuid

    def test_replies_to_relation_returns_none_if_parent_not_found(self):
        """create_replies_to_relation() returns None if parent doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create only reply item
                reply = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Email",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"rfc_message_id": "<reply@example.com>"}
                )
                soil.create_fact(reply)

                # Try to create relation to non-existent parent
                relation_uuid = soil.create_replies_to_relation(
                    reply_uuid=reply.uuid,
                    parent_uuid="soil_nonexistent_uuid"
                )

                assert relation_uuid is None


class TestDatabaseInitialization:
    """Characterize database initialization behavior."""

    def test_schema_version_is_set(self):
        """After init_schema(), schema version should be set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                version = soil.get_schema_version()
                assert version is not None
                assert version == "20260130"

    def test_reinit_is_idempotent(self):
        """Calling init_schema() twice should not fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()
                soil.init_schema()  # Should not fail

                version = soil.get_schema_version()
                assert version == "20260130"

    def test_database_file_is_created(self):
        """Database file should be created after init_schema()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

            assert db_path.exists()

    def test_context_manager_works(self):
        """Soil can be used as context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            item_uuid = None
            with Soil(db_path) as soil:
                soil.init_schema()
                item = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Test"}
                )
                item_uuid = item.uuid
                soil.create_fact(item)

            # Connection should be closed after context
            # But data should be committed
            with Soil(db_path) as soil2:
                retrieved = soil2.get_fact(item_uuid)
                assert retrieved is not None


class TestItemListOperations:
    """Characterize Item list/query operations."""

    def test_items_can_be_listed(self):
        """Items can be listed without filters."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create multiple items
                for i in range(3):
                    item = Fact(
                        uuid=generate_soil_uuid(),
                        _type="Note",
                        realized_at="2026-01-30T12:00:00Z",
                        canonical_at="2026-01-30T12:00:00Z",
                        data={"description": f"Note {i}"}
                    )
                    soil.create_fact(item)

                items = soil.list_items(limit=10)
                assert len(items) == 3

    def test_items_can_be_filtered_by_type(self):
        """Items can be filtered by _type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create different item types
                note = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "A note"}
                )
                email = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Email",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"rfc_message_id": "<test@example.com>"}
                )
                soil.create_fact(note)
                soil.create_fact(email)

                # Filter by type
                notes = soil.list_items(_type="Note", limit=10)
                emails = soil.list_items(_type="Email", limit=10)

                assert len(notes) == 1
                assert len(emails) == 1
                assert notes[0]._type == "Note"
                assert emails[0]._type == "Email"

    def test_items_list_respects_limit(self):
        """list_items() should respect the limit parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create more items than limit
                for i in range(10):
                    item = Fact(
                        uuid=generate_soil_uuid(),
                        _type="Note",
                        realized_at=f"2026-01-30T12:{i:02d}:00Z",
                        canonical_at=f"2026-01-30T12:{i:02d}:00Z",
                        data={"description": f"Note {i}"}
                    )
                    soil.create_fact(item)

                items = soil.list_items(limit=5)
                assert len(items) == 5


class TestCountOperations:
    """Characterize count operations."""

    def test_items_can_be_counted(self):
        """All items can be counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create multiple items
                for i in range(5):
                    item = Fact(
                        uuid=generate_soil_uuid(),
                        _type="Note",
                        realized_at="2026-01-30T12:00:00Z",
                        canonical_at="2026-01-30T12:00:00Z",
                        data={"description": f"Note {i}"}
                    )
                    soil.create_fact(item)

                count = soil.count_items()
                assert count == 5

    def test_items_can_be_counted_by_type(self):
        """Items can be counted by _type."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create different item types
                for i in range(3):
                    note = Fact(
                        uuid=generate_soil_uuid(),
                        _type="Note",
                        realized_at="2026-01-30T12:00:00Z",
                        canonical_at="2026-01-30T12:00:00Z",
                        data={"description": f"Note {i}"}
                    )
                    soil.create_fact(note)

                    email = Fact(
                        uuid=generate_soil_uuid(),
                        _type="Email",
                        realized_at="2026-01-30T12:01:00Z",
                        canonical_at="2026-01-30T12:01:00Z",
                        data={"rfc_message_id": f"<test{i}@example.com>"}
                    )
                    soil.create_fact(email)

                note_count = soil.count_items(_type="Note")
                email_count = soil.count_items(_type="Email")

                assert note_count == 3
                assert email_count == 3

    def test_relations_can_be_counted(self):
        """All relations can be counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create items and relations
                item1 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Item 1"}
                )
                item2 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"description": "Item 2"}
                )
                soil.create_fact(item1)
                soil.create_fact(item2)

                # Create multiple relations with same kind, source, target
                # Due to UNIQUE(kind, source, target) constraint, only 1 is stored
                for i in range(3):
                    soil.create_relation(SystemRelation(
                        uuid=generate_soil_uuid(),
                        kind="cites",
                        source=item2.uuid,
                        source_type="item",
                        target=item1.uuid,
                        target_type="item",
                        created_at=2230 + i
                    ))

                count = soil.count_relations()
                assert count == 1  # Deduplication: only 1 unique (kind, source, target) tuple

    def test_relations_can_be_counted_by_kind(self):
        """Relations can be counted by kind."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            with Soil(db_path) as soil:
                soil.init_schema()

                # Create items
                item1 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:00:00Z",
                    canonical_at="2026-01-30T12:00:00Z",
                    data={"description": "Item 1"}
                )
                item2 = Fact(
                    uuid=generate_soil_uuid(),
                    _type="Note",
                    realized_at="2026-01-30T12:01:00Z",
                    canonical_at="2026-01-30T12:01:00Z",
                    data={"description": "Item 2"}
                )
                soil.create_fact(item1)
                soil.create_fact(item2)

                # Create relations of different kinds
                # Due to UNIQUE(kind, source, target) constraint, each kind gets 1 relation
                for i in range(2):
                    soil.create_relation(SystemRelation(
                        uuid=generate_soil_uuid(),
                        kind="cites",
                        source=item2.uuid,
                        source_type="item",
                        target=item1.uuid,
                        target_type="item",
                        created_at=2230 + i
                    ))

                for i in range(3):
                    soil.create_relation(SystemRelation(
                        uuid=generate_soil_uuid(),
                        kind="replies_to",
                        source=item2.uuid,
                        source_type="item",
                        target=item1.uuid,
                        target_type="item",
                        created_at=2230 + i
                    ))

                cites_count = soil.count_relations(kind="cites")
                replies_count = soil.count_relations(kind="replies_to")

                assert cites_count == 1  # Deduplication: only 1 unique (kind, source, target) tuple
                assert replies_count == 1  # Deduplication: only 1 unique (kind, source, target) tuple


class TestEvidence:
    """Characterize Evidence behavior."""

    def test_evidence_to_dict(self):
        """Evidence can be converted to dict."""
        evidence = Evidence(
            source="system_inferred",
            confidence=0.9,
            method="rfc_5322_in_reply_to"
        )

        evidence_dict = evidence.to_dict()
        assert evidence_dict["source"] == "system_inferred"
        assert evidence_dict["confidence"] == 0.9
        assert evidence_dict["method"] == "rfc_5322_in_reply_to"

    def test_evidence_none_values_excluded(self):
        """Evidence.to_dict() excludes None values."""
        evidence = Evidence(
            source="user_stated",
            confidence=None,
            basis=None,
            method=None
        )

        evidence_dict = evidence.to_dict()
        assert "source" in evidence_dict
        assert "confidence" not in evidence_dict
        assert "basis" not in evidence_dict
        assert "method" not in evidence_dict
