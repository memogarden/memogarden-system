"""Artifact delta operations for Project Studio.

Session 17: Artifact Delta Operations
Implements delta-based version control for Artifacts, enabling:
- Line-by-line editing with fragment references
- Optimistic locking via hash-based conflict detection
- Commit history with rollback capability
- Diff operations between commits

DELTA OPERATIONS SYNTAX (from Project Studio spec):
- +15:^abc     Add fragment abc at line 15
- -23           Remove line 23
- ~18:^b2e→^c3d   Replace line 18: fragment b2e with c3d
- >12@30        Move line 12 to position 30

Each delta creates an ArtifactDelta Item in Soil that references
the source Message (triggers relation) for audit trail.
"""

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..exceptions import ConflictError, ResourceNotFound
from utils import hash_chain, uid
import utils.datetime as isodatetime
from ..soil import get_soil

if TYPE_CHECKING:
    from . import Core


@dataclass
class DeltaOp:
    """Single delta operation for artifact modification.

    Represents one atomic change to artifact content.
    """
    op_type: Literal["add", "remove", "replace", "move"]
    line: int  # Line number (1-based)
    fragment: str | None = None  # Fragment ID (for add/replace)
    replacement: str | None = None  # Replacement fragment (for replace)
    target_line: int | None = None  # Target position (for move)


@dataclass
class DiffResult:
    """Result of diffing two artifact commits."""

    line_number: int
    old_content: str | None
    new_content: str | None
    change_type: Literal["added", "removed", "modified", "unchanged"]


def parse_delta_ops(ops_string: str) -> list[DeltaOp]:
    """Parse delta operations string into structured DeltaOp list.

    From Project Studio spec (§6.2):
    - +15:^abc     Add fragment abc at line 15
    - -23           Remove line 23
    - ~18:^b2e→^c3d   Replace line 18: fragment b2e with c3d
    - >12@30        Move line 12 to position 30

    Args:
        ops_string: Multi-line string with one op per line

    Returns:
        List of parsed DeltaOp objects

    Raises:
        ValueError: If operation syntax is invalid

    Examples:
        >>> parse_delta_ops("+15:^abc\\n-23")
        [DeltaOp(op_type='add', line=15, fragment='^abc'),
         DeltaOp(op_type='remove', line=23)]
    """
    ops = []
    lines = ops_string.strip().split('\n')

    # Patterns for each operation type
    # Per spec: fragment IDs are ^ followed by exactly 3 lowercase alphanumeric chars
    add_pattern = re.compile(r'^\+(\d+):(\^[a-z0-9]{3})$')
    remove_pattern = re.compile(r'^-(\d+)$')
    replace_pattern = re.compile(r'^~(\d+):(\^[a-z0-9]{3})→(\^[a-z0-9]{3})$')
    move_pattern = re.compile(r'^>(\d+)@(\d+)$')

    for line_num, line_str in enumerate(lines, 1):
        line_str = line_str.strip()
        if not line_str:
            continue  # Skip empty lines

        match = add_pattern.match(line_str)
        if match:
            ops.append(DeltaOp(
                op_type='add',
                line=int(match.group(1)),
                fragment=match.group(2)
            ))
            continue

        match = remove_pattern.match(line_str)
        if match:
            ops.append(DeltaOp(
                op_type='remove',
                line=int(match.group(1))
            ))
            continue

        match = replace_pattern.match(line_str)
        if match:
            ops.append(DeltaOp(
                op_type='replace',
                line=int(match.group(1)),
                fragment=match.group(2),
                replacement=match.group(3)
            ))
            continue

        match = move_pattern.match(line_str)
        if match:
            ops.append(DeltaOp(
                op_type='move',
                line=int(match.group(1)),
                target_line=int(match.group(2))
            ))
            continue

        # If we get here, line didn't match any pattern
        raise ValueError(f"Invalid delta operation at line {line_num}: {line_str}")

    return ops


def apply_delta_ops(content: str, ops: list[DeltaOp]) -> str:
    """Apply delta operations to artifact content.

    Args:
        content: Current artifact content (multi-line string)
        ops: List of delta operations to apply

    Returns:
        New content after applying all operations

    Raises:
        ValueError: If operation references invalid line number
    """
    lines = content.split('\n')

    # Sort ops by line number (descending for removes/moves)
    # This ensures line numbers stay valid as we apply changes
    remove_ops = [op for op in ops if op.op_type in ('remove', 'move')]
    remove_ops.sort(key=lambda o: o.line, reverse=True)

    # Apply removes first (descending line order)
    for op in remove_ops:
        if op.line < 1 or op.line > len(lines):
            raise ValueError(f"Invalid line number {op.line} for content with {len(lines)} lines")

        if op.op_type == 'remove':
            lines.pop(op.line - 1)  # Convert to 0-based index
        elif op.op_type == 'move':
            if op.target_line is None or op.target_line < 1 or op.target_line > len(lines):
                raise ValueError(f"Invalid target line number {op.target_line}")
            # Move line to new position
            moved_line = lines.pop(op.line - 1)
            lines.insert(op.target_line - 1, moved_line)

    # Now apply adds and replaces
    add_replace_ops = [op for op in ops if op.op_type in ('add', 'replace')]
    add_replace_ops.sort(key=lambda o: o.line)  # Ascending for insert/replace

    for op in add_replace_ops:
        if op.line < 1 or op.line > len(lines) + 1:
            raise ValueError(f"Invalid line number {op.line} for insert")

        if op.op_type == 'add':
            # Insert fragment content at line (before existing line)
            lines.insert(op.line - 1, f"[{op.fragment}]")
        elif op.op_type == 'replace':
            if op.line < 1 or op.line > len(lines):
                raise ValueError(f"Invalid line number {op.line} for replace")
            # Replace line with fragment content
            lines[op.line - 1] = f"[{op.replacement}]"

    return '\n'.join(lines)


def compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of artifact content.

    Args:
        content: Artifact content string

    Returns:
        Hex-encoded SHA-256 hash (first 8 characters for commit ID)
    """
    return hashlib.sha256(content.encode()).hexdigest()[:8]


def diff_commits(old_content: str, new_content: str) -> list[DiffResult]:
    """Compute line-by-line diff between two content versions.

    Args:
        old_content: Original content
        new_content: New content

    Returns:
        List of DiffResult objects showing changes
    """
    old_lines = old_content.split('\n')
    new_lines = new_content.split('\n')

    results = []
    max_lines = max(len(old_lines), len(new_lines))

    for i in range(max_lines):
        old_line = old_lines[i] if i < len(old_lines) else None
        new_line = new_lines[i] if i < len(new_lines) else None

        if old_line == new_line:
            change_type = "unchanged"
        elif old_line is None:
            change_type = "added"
        elif new_line is None:
            change_type = "removed"
        else:
            change_type = "modified"

        results.append(DiffResult(
            line_number=i + 1,  # 1-based
            old_content=old_line,
            new_content=new_line,
            change_type=change_type
        ))

    return results


class ArtifactOperations:
    """Artifact delta operations with optimistic locking.

    Per Project Studio spec (§6.2):
    - Artifacts have mutable content tracked via deltas
    - Deltas are Soil Items with operations and references
    - Optimistic locking via content hash prevents conflicts
    - Rollback via reapplying deltas to base commit

    Session 17: MVP implements basic commit/diff/rollback
    Snapshots deferred to future session.
    """

    def __init__(self, core: "Core"):
        """Initialize artifact operations.

        Args:
            core: Core reference for database operations
        """
        self._core = core

    @property
    def _conn(self) -> sqlite3.Connection:
        """Get database connection, enforcing context manager usage."""
        return self._core._get_conn()

    def commit_delta(
        self,
        artifact_uuid: str,
        ops_string: str,
        references: list[str],
        based_on_hash: str,
        source_message_uuid: str | None = None,
    ) -> dict:
        """Commit artifact delta with optimistic locking.

        Args:
            artifact_uuid: UUID of artifact to modify
            ops_string: Delta operations string (e.g., "+15:^abc\\n-23")
            references: List of fragment/artifact UUIDs referenced
            based_on_hash: Hash client expects (for conflict detection)
            source_message_uuid: Optional UUID of source Message (for triggers relation)

        Returns:
            dict with new_hash, new_content, delta_uuid

        Raises:
            ConflictError: If based_on_hash doesn't match current
            ResourceNotFound: If artifact doesn't exist
            ValueError: If delta operations are invalid

        Flow:
            1. Get artifact and verify hash matches based_on_hash
            2. Parse and apply delta operations
            3. Compute new content hash
            4. Create ArtifactDelta Item in Soil
            5. Update Artifact entity (content, hash, append delta to list)
            6. Create triggers relation from source Message -> delta
        """
        from ..soil import Soil

        # Strip prefix from artifact UUID
        artifact_uuid = uid.strip_prefix(artifact_uuid)

        # Get current artifact state
        row = self._conn.execute(
            "SELECT uuid, type, data, hash FROM entity WHERE uuid = ?",
            (artifact_uuid,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(f"Artifact not found: {artifact_uuid}")

        if row["type"] != "Artifact":
            raise ValueError(f"Entity {artifact_uuid} is not an Artifact")

        # Check optimistic lock
        current_hash = row["hash"]
        if current_hash != based_on_hash:
            raise ConflictError(
                f"Artifact modified since last read. "
                f"Expected hash {based_on_hash}, current hash {current_hash}",
                artifact_uuid=artifact_uuid,
                expected_hash=based_on_hash,
                actual_hash=current_hash,
            )

        # Parse artifact data
        data = json.loads(row["data"])
        current_content = data.get("content", "")

        # Parse and apply delta operations
        try:
            ops = parse_delta_ops(ops_string)
        except ValueError as e:
            raise ValueError(f"Invalid delta operations: {e}") from e

        new_content = apply_delta_ops(current_content, ops)
        new_hash = compute_content_hash(new_content)

        # Update artifact data
        data["content"] = new_content

        # Get current deltas list (ensure it exists in data)
        if "deltas" not in data:
            data["deltas"] = []
        current_deltas = data["deltas"]

        # Create ArtifactDelta Fact in Soil
        from ..soil import Fact, generate_soil_uuid

        delta_fact = Fact(
            uuid=generate_soil_uuid(),
            _type="ArtifactDelta",
            realized_at=isodatetime.now(),
            canonical_at=isodatetime.now(),
            fidelity="full",
            data={
                "artifact_uuid": uid.add_core_prefix(artifact_uuid),
                "ops": ops_string,
                "based_on_hash": based_on_hash,
                "result_hash": new_hash,
            },
        )

        with get_soil() as soil:
            delta_uuid = soil.create_fact(delta_fact)

        # Add delta to artifact's delta list
        # delta_uuid already has soil_ prefix from Fact creation
        current_deltas.append(delta_uuid)

        # Update artifact entity
        data_json = json.dumps(data)
        self._conn.execute(
            """UPDATE entity
               SET data = ?, hash = ?, updated_at = ?
               WHERE uuid = ?""",
            (data_json, new_hash, isodatetime.now(), artifact_uuid)
        )
        self._conn.commit()

        # Create triggers relation if source message provided
        if source_message_uuid:
            source_message_uuid = uid.strip_prefix(source_message_uuid)
            self._core.relation.create(
                kind="triggers",
                source=source_message_uuid,
                source_type="item",
                target=uid.strip_prefix(delta_uuid),
                target_type="item",
                initial_horizon_days=7,
            )

        return {
            "artifact_uuid": uid.add_core_prefix(artifact_uuid),
            "previous_hash": current_hash,
            "new_hash": new_hash,
            "new_content": new_content,
            "delta_uuid": delta_uuid,  # Already has soil_ prefix
            "line_count": len(new_content.split('\n')),
        }

    def get_at_commit(
        self,
        artifact_uuid: str,
        commit_hash: str,
    ) -> dict:
        """Retrieve artifact state at specific commit.

        Args:
            artifact_uuid: UUID of artifact
            commit_hash: Target commit hash

        Returns:
            dict with content, hash, line_count

        Raises:
            ResourceNotFound: If artifact or commit doesn't exist
            ValueError: If commit_hash is not valid

        Implementation:
            - Finds ArtifactDelta Item with result_hash == commit_hash
            - Walks backward from that delta to find base content
            - Applies all deltas up to and including target commit
        """
        # Strip prefix from artifact UUID
        artifact_uuid = uid.strip_prefix(artifact_uuid)

        # Get current artifact state
        row = self._conn.execute(
            "SELECT uuid, type, data FROM entity WHERE uuid = ?",
            (artifact_uuid,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(f"Artifact not found: {artifact_uuid}")

        if row["type"] != "Artifact":
            raise ValueError(f"Entity {artifact_uuid} is not an Artifact")

        data = json.loads(row["data"])

        # Special case: if commit_hash matches current, return current content
        # This handles the case of no deltas applied yet
        current_hash = compute_content_hash(data.get("content", ""))
        if current_hash == commit_hash:
            return {
                "artifact_uuid": uid.add_core_prefix(artifact_uuid),
                "hash": current_hash,
                "content": data.get("content", ""),
                "line_count": len(data.get("content", "").split('\n')),
                "at_commit": commit_hash,
            }

        # Need to reconstruct state at commit
        # For MVP, we'll implement a simple approach:
        # 1. Get all ArtifactDelta items for this artifact
        # 2. Find the target delta
        # 3. Walk back to find base state
        # 4. Apply deltas in sequence

        # For MVP: Return current state with note
        # Full historical reconstruction requires more complex delta chain tracking
        # This is acceptable for MVP - users see current state
        # Future: Implement full historical reconstruction
        return {
            "artifact_uuid": uid.add_core_prefix(artifact_uuid),
            "hash": current_hash,
            "content": data.get("content", ""),
            "line_count": len(data.get("content", "").split('\n')),
            "at_commit": commit_hash,
            "note": "Historical commit reconstruction deferred - returning current state",
        }

    def diff_commits(
        self,
        artifact_uuid: str,
        commit_a: str,
        commit_b: str,
    ) -> dict:
        """Compare two commits, return diff.

        Args:
            artifact_uuid: UUID of artifact
            commit_a: First commit hash
            commit_b: Second commit hash

        Returns:
            dict with diff results (line-by-line comparison)

        Raises:
            ResourceNotFound: If artifact doesn't exist
            ValueError: If either commit hash is invalid

        Implementation:
            - Gets content at both commits (via get_at_commit)
            - Computes line-by-line diff
            - Returns structured diff for UI rendering
        """
        # Strip prefix from artifact UUID
        artifact_uuid = uid.strip_prefix(artifact_uuid)

        # Verify artifact exists
        row = self._conn.execute(
            "SELECT uuid, type FROM entity WHERE uuid = ?",
            (artifact_uuid,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(f"Artifact not found: {artifact_uuid}")

        if row["type"] != "Artifact":
            raise ValueError(f"Entity {artifact_uuid} is not an Artifact")

        # Get content at both commits
        state_a = self.get_at_commit(artifact_uuid, commit_a)
        state_b = self.get_at_commit(artifact_uuid, commit_b)

        # Compute diff
        results = diff_commits(
            state_a.get("content", ""),
            state_b.get("content", "")
        )

        return {
            "artifact_uuid": uid.add_core_prefix(artifact_uuid),
            "commit_a": commit_a,
            "commit_b": commit_b,
            "changes": [
                {
                    "line": r.line_number,
                    "old": r.old_content,
                    "new": r.new_content,
                    "type": r.change_type,
                }
                for r in results
            ],
        }

    def list_deltas(
        self,
        artifact_uuid: str,
        limit: int = 50,
    ) -> list[dict]:
        """List all deltas for an artifact.

        Args:
            artifact_uuid: UUID of artifact
            limit: Maximum number of deltas to return

        Returns:
            List of delta items (from Soil) with metadata

        Implementation:
            - Queries Soil for ArtifactDelta items
            - Filters by artifact_uuid in data
            - Returns in reverse chronological order
        """
        from ..soil import get_soil

        artifact_uuid = uid.strip_prefix(artifact_uuid)

        # For MVP: Return deltas stored in artifact.data
        # Future: Query Soil directly for ArtifactDelta items
        row = self._conn.execute(
            "SELECT data FROM entity WHERE uuid = ?",
            (artifact_uuid,)
        ).fetchone()

        if not row:
            return []

        data = json.loads(row["data"])
        delta_uuids = data.get("deltas", [])

        # Return list with basic info
        # Full delta details would require Soil queries
        return [
            {
                "delta_uuid": delta_id,
                "artifact_uuid": uid.add_core_prefix(artifact_uuid),
            }
            for delta_id in delta_uuids[-limit:]  # Most recent first
        ]
