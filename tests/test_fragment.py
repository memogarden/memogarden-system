"""Tests for Fragment system.

Session 16: ConversationLog Entity and Fragment System
Tests fragment ID generation, reference parsing, and resolution.
"""

import pytest
import sqlite3

from system.fragment import (
    generate_fragment_id,
    parse_references,
    ReferenceType,
    Reference,
    Fragment,
    NotImplementedError,
)
from system.utils import uid


class TestFragmentGeneration:
    """Tests for fragment ID generation."""

    def test_generate_fragment_id_simple(self):
        """Generate fragment ID from simple text."""
        frag_id = generate_fragment_id("hello world")

        assert frag_id.startswith("^")
        assert len(frag_id) == 4  # ^ + exactly 3 chars

    def test_generate_fragment_id_sentence(self):
        """Generate fragment ID from sentence."""
        frag_id = generate_fragment_id("Approve: Use RFC-009")

        assert frag_id.startswith("^")
        assert len(frag_id) == 4  # ^ + exactly 3 chars

    def test_generate_fragment_id_full_hash(self):
        """Generate fragment ID from longer content."""
        frag_id = generate_fragment_id(
            "The fragment system provides semantic reference tracking for Messages."
        )

        assert frag_id.startswith("^")
        assert len(frag_id) == 4  # ^ + exactly 3 chars

    def test_generate_fragment_id_deterministic(self):
        """Same content produces same fragment ID."""
        content = "Test fragment"

        frag_id1 = generate_fragment_id(content)
        frag_id2 = generate_fragment_id(content)

        assert frag_id1 == frag_id2  # Deterministic

    def test_generate_fragment_id_cap_at_three_chars(self):
        """Fragment ID is always exactly 4 characters total (^ + 3 chars)."""
        # Very long content still produces 3-char fragment after ^
        frag_id = generate_fragment_id("x" * 100)

        assert frag_id.startswith("^")
        # Spec requires exactly 3 chars after ^, so 4 total
        assert len(frag_id) == 4  # ^ + exactly 3 chars


class TestReferenceParsing:
    """Tests for reference parsing from Message content."""

    def test_parse_fragment_reference(self):
        """Parse fragment reference ^abc."""
        content = "See ^abc for details"

        refs = parse_references(content)

        assert len(refs) == 1
        assert refs[0].type == ReferenceType.FRAGMENT
        assert refs[0].target == "^abc"
        assert refs[0].span == (4, 8)

    def test_parse_artifact_line_reference(self):
        """Parse artifact line reference README:15."""
        content = "See line 15 in README:15"

        refs = parse_references(content)

        assert len(refs) == 1
        assert refs[0].type == ReferenceType.ARTIFACT_LINE
        assert refs[0].target == "README:15"

    def test_parse_artifact_line_at_commit(self):
        """Parse artifact line at commit README:15@abc123."""
        content = "Changes at README:15@abc123 are significant."

        refs = parse_references(content)

        assert len(refs) == 1
        assert refs[0].type == ReferenceType.ARTIFACT_LINE_AT_COMMIT
        assert refs[0].target == "README:15@abc123"

    def test_parse_item_reference(self):
        """Parse Item reference @uuid."""
        content = "See @soil_abc123def for context."

        refs = parse_references(content)

        assert len(refs) == 1
        assert refs[0].type == ReferenceType.ITEM
        assert refs[0].target == "soil_abc123def"

    def test_parse_log_reference(self):
        """Parse log reference [text](uuid)."""
        content = "See previous discussion at [text](soil_xyz123)."

        refs = parse_references(content)

        assert len(refs) == 1
        assert refs[0].type == ReferenceType.LOG
        assert refs[0].target == "soil_xyz123"

    def test_parse_multiple_references(self):
        """Parse multiple references in one message."""
        content = "Review ^abc, README:15, and @soil_abc123def"

        refs = parse_references(content)

        assert len(refs) == 3

        # Check all types (no LOG reference in this content)
        types = {ref.type for ref in refs}
        assert ReferenceType.FRAGMENT in types
        assert ReferenceType.ARTIFACT_LINE in types
        assert ReferenceType.ITEM in types

    def test_references_with_overlapping_positions(self):
        """Parse references with different positions."""
        content = "See ^abc and ^def"

        refs = parse_references(content)

        assert len(refs) == 2
        # ^abc starts at position 4, ends at 8
        assert refs[0].span == (4, 8)
        # ^def starts at position 13, ends at 17
        assert refs[1].span == (13, 17)

    def test_parse_with_invalid_reference(self):
        """Ignore invalid reference patterns."""
        content = "The value is $100"

        refs = parse_references(content)

        # Should not parse as a reference (no ^ pattern)
        assert len(refs) == 0


class TestFragmentResolution:
    """Tests for fragment resolution within scopes.

    NOTE: Fragment resolution requires cross-database queries (Core entity + Soil items)
    which is not yet implemented for MVP. Raises NotImplementedError.

    These tests will be re-enabled in Session 17 when ArtifactDelta operations are added.
    """

    def test_resolve_fragment_not_implemented(self, db_core):
        """Resolving fragment raises NotImplementedError."""
        # Import here to access the function
        from system.fragment import resolve_fragment

        # Create test connection for fragment resolution test
        import sqlite3
        from system.host.environment import get_db_path
        test_conn = sqlite3.connect(str(get_db_path('core')))
        test_conn.row_factory = sqlite3.Row

        with pytest.raises(NotImplementedError, match=r"Session 17"):
            resolve_fragment(
                conn=test_conn,
                scope_uuid="test_scope",
                fragment_id="^abc"
            )

    def test_resolve_artifact_line_not_implemented(self, db_core):
        """Resolving artifact line raises NotImplementedError."""
        from system.fragment import resolve_artifact_line

        # Use same test connection
        import sqlite3
        from system.host.environment import get_db_path
        test_conn = sqlite3.connect(str(get_db_path('core')))
        test_conn.row_factory = sqlite3.Row

        with pytest.raises(NotImplementedError, match=r"Session 17"):
            resolve_artifact_line(
                conn=test_conn,
                artifact_uuid="test_artifact",
                line_number=42
            )
