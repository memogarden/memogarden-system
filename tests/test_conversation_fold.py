"""Tests for Conversation fold operation (Session 18).

Tests fold operation for Project Studio:
- Fold operation with summary
- ConversationLog collapse behavior
- Summary object creation
- Fragment ID handling
"""

import pytest

from system.core import get_core
from system.core.entity import EntityOperations
from utils import uid


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def entity_ops(db_core):
    """Provide EntityOperations for creating test entities."""
    return db_core.entity


# ============================================================================
# Fold Operation Tests
# ============================================================================

class TestFoldOperation:
    """Test conversation fold operation."""

    def test_fold_creates_summary(self, db_core, entity_ops):
        """Fold operation creates summary object on ConversationLog."""
        # Create a ConversationLog entity
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={
                "parent_uuid": None,
                "items": ["soil_msg1", "soil_msg2"],
            }
        )

        # Fold the conversation (entity.create returns plain UUID, fold returns prefixed)
        result = db_core.conversation.fold(
            log_uuid=log_uuid,
            summary_content="Decision made to use approach A",
            author="operator",
        )

        # Verify result (fold returns prefixed UUID)
        assert result.log_uuid == uid.add_core_prefix(log_uuid)
        assert result.collapsed is True
        assert result.summary["content"] == "Decision made to use approach A"
        assert result.summary["author"] == "operator"
        assert "timestamp" in result.summary

    def test_fold_with_fragment_ids(self, db_core, entity_ops):
        """Fold operation includes fragment IDs when provided."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={
                "parent_uuid": None,
                "items": [],
            }
        )

        result = db_core.conversation.fold(
            log_uuid=log_uuid,
            summary_content="Summary with fragments ^abc and ^def",
            author="agent",
            fragment_ids=["^abc", "^def"],
        )

        assert result.summary["fragment_ids"] == ["^abc", "^def"]

    def test_fold_empty_summary_raises_error(self, db_core, entity_ops):
        """Fold operation with empty summary raises ValueError."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        with pytest.raises(ValueError, match="Summary content cannot be empty"):
            db_core.conversation.fold(
                log_uuid=log_uuid,
                summary_content="",
                author="operator",
            )

    def test_fold_whitespace_only_summary_raises_error(self, db_core, entity_ops):
        """Fold operation with whitespace-only summary raises ValueError."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        with pytest.raises(ValueError, match="Summary content cannot be empty"):
            db_core.conversation.fold(
                log_uuid=log_uuid,
                summary_content="   \n  ",
                author="operator",
            )

    def test_fold_nonexistent_log_raises_error(self, db_core):
        """Fold operation on non-existent log raises ResourceNotFound."""
        from system.exceptions import ResourceNotFound

        with pytest.raises(ResourceNotFound, match="ConversationLog not found"):
            db_core.conversation.fold(
                log_uuid="core_nonexistent",
                summary_content="Summary",
                author="operator",
            )


class TestConversationLogGet:
    """Test getting ConversationLog entities."""

    def test_get_conversation_log(self, db_core, entity_ops):
        """Get operation returns ConversationLog with proper data."""
        # Create with initial data
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={
                "parent_uuid": None,
                "items": ["soil_msg1"],
            }
        )

        # Get the log
        result = db_core.conversation.get(log_uuid=log_uuid)

        assert result["uuid"] == uid.add_core_prefix(log_uuid)
        assert result["_type"] == "ConversationLog"
        assert result["data"]["parent_uuid"] is None
        assert result["data"]["items"] == ["soil_msg1"]
        assert "created_at" in result
        assert "updated_at" in result

    def test_get_folded_conversation_log(self, db_core, entity_ops):
        """Get operation returns summary for folded logs."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        # Fold the conversation
        db_core.conversation.fold(
            log_uuid=log_uuid,
            summary_content="Folded summary",
            author="system",
        )

        # Get and verify summary is present
        result = db_core.conversation.get(log_uuid=log_uuid)

        assert result["data"]["collapsed"] is True
        assert result["data"]["summary"]["content"] == "Folded summary"
        assert result["data"]["summary"]["author"] == "system"

    def test_get_nonexistent_conversation_log_raises_error(self, db_core):
        """Get operation on non-existent log raises ResourceNotFound."""
        from system.exceptions import ResourceNotFound

        with pytest.raises(ResourceNotFound, match="ConversationLog not found"):
            db_core.conversation.get(log_uuid="core_nonexistent")


class TestFoldWithCorePrefix:
    """Test fold operation with core_ prefix handling."""

    def test_fold_with_core_prefix(self, db_core, entity_ops):
        """Fold operation works with core_ prefixed UUID."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        # Use core_ prefix (as API would provide)
        result = db_core.conversation.fold(
            log_uuid=uid.add_core_prefix(log_uuid),  # Add core_ prefix
            summary_content="Summary",
            author="operator",
        )

        assert result.collapsed is True

    def test_get_with_core_prefix(self, db_core, entity_ops):
        """Get operation works with core_ prefixed UUID."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        # Use core_ prefix
        result = db_core.conversation.get(log_uuid=uid.add_core_prefix(log_uuid))

        assert result["uuid"] == uid.add_core_prefix(log_uuid)


class TestFoldAuthorTypes:
    """Test different author types for fold operation."""

    def test_fold_by_operator(self, db_core, entity_ops):
        """Fold operation with operator author."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        result = db_core.conversation.fold(
            log_uuid=log_uuid,
            summary_content="Operator summary",
            author="operator",
        )

        assert result.summary["author"] == "operator"

    def test_fold_by_agent(self, db_core, entity_ops):
        """Fold operation with agent author."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        result = db_core.conversation.fold(
            log_uuid=log_uuid,
            summary_content="Agent summary",
            author="agent",
        )

        assert result.summary["author"] == "agent"

    def test_fold_by_system(self, db_core, entity_ops):
        """Fold operation with system author."""
        log_uuid = entity_ops.create(
            entity_type="ConversationLog",
            data={"parent_uuid": None, "items": []}
        )

        result = db_core.conversation.fold(
            log_uuid=log_uuid,
            summary_content="System summary",
            author="system",
        )

        assert result.summary["author"] == "system"


class TestFoldPersistence:
    """Test that fold changes persist across Core instances."""

    def test_fold_persists_across_transactions(self):
        """Fold operation persists when Core context exits."""
        # Create and fold in first transaction
        with get_core() as core1:
            log_uuid = core1.entity.create(
                entity_type="ConversationLog",
                data={"parent_uuid": None, "items": []}
            )
            core1.conversation.fold(
                log_uuid=log_uuid,
                summary_content="Persistent summary",
                author="operator",
            )

        # Verify in second transaction
        with get_core() as core2:
            result = core2.conversation.get(log_uuid=log_uuid)
            assert result["data"]["collapsed"] is True
            assert result["data"]["summary"]["content"] == "Persistent summary"
