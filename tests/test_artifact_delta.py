"""Tests for Artifact delta operations (Session 17).

Tests delta operations for Project Studio:
- Delta op parsing (+, -, ~, >)
- Content application with line operations
- Hash computation and comparison
- Diff between commits
- ArtifactDelta commit operations
"""

import pytest

from system.core import get_core
from system.core.artifact import (
    ConflictError,
    DeltaOp,
    DiffResult,
    apply_delta_ops,
    compute_content_hash,
    diff_commits,
    parse_delta_ops,
)


# ============================================================================
# Delta Parsing Tests
# ============================================================================

class TestDeltaParsing:
    """Test delta operation parsing from string format."""

    def test_parse_add_operation(self):
        """Parsing add operation: +15:^abc"""
        ops = parse_delta_ops("+15:^abc")
        assert len(ops) == 1
        assert ops[0].op_type == "add"
        assert ops[0].line == 15
        assert ops[0].fragment == "^abc"

    def test_parse_remove_operation(self):
        """Parsing remove operation: -23"""
        ops = parse_delta_ops("-23")
        assert len(ops) == 1
        assert ops[0].op_type == "remove"
        assert ops[0].line == 23

    def test_parse_replace_operation(self):
        """Parsing replace operation: ~18:^b2e→^c3d"""
        ops = parse_delta_ops("~18:^b2e→^c3d")
        assert len(ops) == 1
        assert ops[0].op_type == "replace"
        assert ops[0].line == 18
        assert ops[0].fragment == "^b2e"
        assert ops[0].replacement == "^c3d"

    def test_parse_move_operation(self):
        """Parsing move operation: >12@30"""
        ops = parse_delta_ops(">12@30")
        assert len(ops) == 1
        assert ops[0].op_type == "move"
        assert ops[0].line == 12
        assert ops[0].target_line == 30

    def test_parse_multiple_operations(self):
        """Parsing multiple operations (multi-line string)."""
        ops_string = "+15:^abc\n-23\n~18:^b2e→^c3d\n>12@30"
        ops = parse_delta_ops(ops_string)
        assert len(ops) == 4
        assert ops[0].op_type == "add"
        assert ops[1].op_type == "remove"
        assert ops[2].op_type == "replace"
        assert ops[3].op_type == "move"

    def test_parse_invalid_operation_raises_error(self):
        """Invalid operation syntax raises ValueError."""
        with pytest.raises(ValueError, match="Invalid delta operation"):
            parse_delta_ops("invalid:operation")

    def test_parse_empty_lines_skipped(self):
        """Empty lines in ops string are skipped."""
        ops = parse_delta_ops("+15:^abc\n\n-23\n  ")
        assert len(ops) == 2
        assert ops[0].op_type == "add"
        assert ops[1].op_type == "remove"


# ============================================================================
# Delta Application Tests
# ============================================================================

class TestDeltaApplication:
    """Test applying delta operations to content."""

    def test_apply_add_operation(self):
        """Add operation inserts content at line."""
        content = "line1\nline2\nline3"
        ops = [DeltaOp(op_type='add', line=2, fragment='^xyz')]
        result = apply_delta_ops(content, ops)
        assert result == "line1\n[^xyz]\nline2\nline3"

    def test_apply_remove_operation(self):
        """Remove operation deletes line."""
        content = "line1\nline2\nline3"
        ops = [DeltaOp(op_type='remove', line=2)]
        result = apply_delta_ops(content, ops)
        assert result == "line1\nline3"

    def test_apply_replace_operation(self):
        """Replace operation changes line content."""
        content = "line1\nline2\nline3"
        ops = [DeltaOp(op_type='replace', line=2, fragment='^old', replacement='^new')]
        result = apply_delta_ops(content, ops)
        assert result == "line1\n[^new]\nline3"

    def test_apply_move_operation(self):
        """Move operation reorders lines."""
        content = "line1\nline2\nline3\nline4"
        ops = [DeltaOp(op_type='move', line=1, target_line=3)]
        result = apply_delta_ops(content, ops)
        assert result == "line2\nline3\nline1\nline4"

    def test_apply_multiple_operations(self):
        """Multiple operations applied in sequence."""
        content = "line1\nline2\nline3"
        ops = [
            DeltaOp(op_type='add', line=2, fragment='^x'),
            DeltaOp(op_type='remove', line=3),
        ]
        result = apply_delta_ops(content, ops)
        assert result == "line1\n[^x]\nline2"

    def test_invalid_line_number_raises_error(self):
        """Invalid line number raises ValueError."""
        content = "line1\nline2\nline3"
        ops = [DeltaOp(op_type='add', line=10, fragment='^x')]
        with pytest.raises(ValueError, match="Invalid line number"):
            apply_delta_ops(content, ops)


# ============================================================================
# Hash Computation Tests
# ============================================================================

class TestHashComputation:
    """Test content hash computation for optimistic locking."""

    def test_hash_is_consistent(self):
        """Same content produces same hash."""
        content = "test content"
        hash1 = compute_content_hash(content)
        hash2 = compute_content_hash(content)
        assert hash1 == hash2

    def test_different_content_different_hash(self):
        """Different content produces different hash."""
        hash1 = compute_content_hash("content a")
        hash2 = compute_content_hash("content b")
        assert hash1 != hash2

    def test_hash_is_8_characters(self):
        """Hash is truncated to 8 characters."""
        content = "any content"
        hash_value = compute_content_hash(content)
        assert len(hash_value) == 8
        assert all(c in '0123456789abcdef' for c in hash_value)


# ============================================================================
# Diff Tests
# ============================================================================

class TestDiffCommits:
    """Test diff computation between content versions."""

    def test_diff_identical_content(self):
        """Identical content shows all unchanged."""
        old = "line1\nline2"
        new = "line1\nline2"
        results = diff_commits(old, new)
        assert len(results) == 2
        assert results[0].change_type == "unchanged"
        assert results[1].change_type == "unchanged"

    def test_diff_added_lines(self):
        """New lines show as added."""
        old = "line1"
        new = "line1\nline2"
        results = diff_commits(old, new)
        assert len(results) == 2
        assert results[0].change_type == "unchanged"
        assert results[1].change_type == "added"
        assert results[1].old_content is None

    def test_diff_removed_lines(self):
        """Removed lines show as removed."""
        old = "line1\nline2"
        new = "line1"
        results = diff_commits(old, new)
        assert len(results) == 2
        assert results[0].change_type == "unchanged"
        assert results[1].change_type == "removed"
        assert results[1].new_content is None

    def test_diff_modified_lines(self):
        """Changed lines show as modified."""
        old = "line1\nline2"
        new = "line1\nchanged"
        results = diff_commits(old, new)
        assert len(results) == 2
        assert results[0].change_type == "unchanged"
        assert results[1].change_type == "modified"
        assert results[1].old_content == "line2"
        assert results[1].new_content == "changed"


# ============================================================================
# Integration Tests
# ============================================================================

class TestArtifactCommitIntegration:
    """Integration tests for artifact commit operations."""

    def test_commit_creates_delta_item(self):
        """Commit creates ArtifactDelta Item in Soil."""
        with get_core() as core:
            # First create an artifact
            artifact_uuid = core.entity.create(
                entity_type="Artifact",
                data={
                    "label": "Test Artifact",
                    "content": "Initial content\nLine 2\nLine 3",
                    "content_type": "text/plain",
                }
            )

            # Get current hash
            current_hash = core.entity.get_current_hash(artifact_uuid)

            # Commit a delta
            # Operation order: removes/moves first (reverse line order), then adds/replaces
            # Original: "Initial content\nLine 2\nLine 3" (3 lines)
            # Remove line 3: "Initial content\nLine 2" (2 lines)
            # Add at line 2: "Initial content\n[^new]\nLine 2" (3 lines)
            ops_string = "+2:^n3w\n-3"  # 3-char fragment ID per spec
            result = core.artifact.commit_delta(
                artifact_uuid=artifact_uuid,
                ops_string=ops_string,
                references=["^n3w"],
                based_on_hash=current_hash,
            )

            # Verify delta was created
            assert "delta_uuid" in result
            assert result["previous_hash"] == current_hash
            assert result["new_hash"] != current_hash
            assert result["line_count"] == 3  # After removing line 3 and adding at line 2

    def test_commit_conflicting_hash_raises_error(self):
        """Commit with wrong hash raises ConflictError."""
        with get_core() as core:
            artifact_uuid = core.entity.create(
                entity_type="Artifact",
                data={
                    "label": "Test Artifact",
                    "content": "Initial content",
                    "content_type": "text/plain",
                }
            )

            # Try to commit with wrong hash
            wrong_hash = "00000000"
            with pytest.raises(ConflictError, match="modified since last read"):
                core.artifact.commit_delta(
                    artifact_uuid=artifact_uuid,
                    ops_string="+1:^xyz",  # 3-char fragment ID per spec
                    references=["^xyz"],
                    based_on_hash=wrong_hash,
                )

    def test_get_at_commit_current_state(self):
        """Get at commit for current hash returns current content."""
        with get_core() as core:
            artifact_uuid = core.entity.create(
                entity_type="Artifact",
                data={
                    "label": "Test Artifact",
                    "content": "Current content",
                    "content_type": "text/plain",
                }
            )

            current_hash = compute_content_hash("Current content")
            result = core.artifact.get_at_commit(artifact_uuid, current_hash)

            assert result["at_commit"] == current_hash
            assert result["content"] == "Current content"

    def test_list_deltas_returns_commit_history(self):
        """List deltas returns commit history for artifact."""
        with get_core() as core:
            artifact_uuid = core.entity.create(
                entity_type="Artifact",
                data={
                    "label": "Test Artifact",
                    "content": "Content",
                    "content_type": "text/plain",
                }
            )

            # Make multiple commits (using 3-char fragment IDs per spec)
            ops_string = "+1:^abc"
            first_hash = core.entity.get_current_hash(artifact_uuid)
            core.artifact.commit_delta(
                artifact_uuid=artifact_uuid,
                ops_string=ops_string,
                references=["^abc"],
                based_on_hash=first_hash,
            )

            second_hash = core.entity.get_current_hash(artifact_uuid)
            core.artifact.commit_delta(
                artifact_uuid=artifact_uuid,
                ops_string="+1:^def",
                references=["^def"],
                based_on_hash=second_hash,
            )

            # List deltas
            deltas = core.artifact.list_deltas(artifact_uuid)
            assert len(deltas) == 2
