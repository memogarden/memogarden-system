"""Conversation operations for Project Studio.

Session 18: Fold Verb
Implements conversation fold operation per RFC-005 single-word verb convention:
- fold: Collapse conversation branch with summary
- Adds summary object to ConversationLog
- Sets collapsed=true on the log
- Branch remains visible and can continue (messages can be appended after fold)

Per RFC-005: fold is a single-word verb applicable to any entity/fact.
Per Project Studio spec: fold creates a summary with content, author, timestamp, and optional fragment_ids.

BRANCHING NOTES:
- Branch creation happens implicitly via RFC-003 ContextFrame inheritance
- When a subagent is created with its own ContextFrame that inherits from a parent,
  this implicitly creates a conversation branch
- No explicit 'branch' verb is needed
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..exceptions import ResourceNotFound
import utils.datetime as isodatetime
from utils import uid

if TYPE_CHECKING:
    from . import Core


@dataclass
class FoldResult:
    """Result of folding a conversation branch."""

    log_uuid: str  # UUID of folded ConversationLog
    summary: dict  # Summary object that was created
    collapsed: bool  # True if branch is now collapsed


class ConversationOperations:
    """Operations for ConversationLog entity.

    Provides fold operation for collapsing conversation branches
    with summaries while preserving full history.
    """

    def __init__(self, core: "Core"):
        """Initialize ConversationOperations with Core reference.

        Args:
            core: Core instance for database access
        """
        self._core = core

    @property
    def _conn(self) -> sqlite3.Connection:
        """Get database connection, enforcing context manager usage."""
        return self._core._get_conn()

    def fold(
        self,
        log_uuid: str,
        summary_content: str,
        author: Literal["operator", "agent", "system"],
        fragment_ids: list[str] | None = None,
    ) -> FoldResult:
        """Fold a conversation branch by adding a summary.

        Per Project Studio spec (§6.3):
        - Creates summary object attached to ConversationLog
        - Marks branch as folded (collapsed=true)
        - Branch remains visible and can continue (messages can be appended after fold)

        Args:
            log_uuid: UUID of ConversationLog to fold (with or without core_ prefix)
            summary_content: Summary text for collapsed branch
            author: Who created the summary (operator/agent/system)
            fragment_ids: Optional fragment IDs referenced in the summary

        Returns:
            FoldResult with log_uuid, summary object, and collapsed status

        Raises:
            ResourceNotFound: If ConversationLog doesn't exist
            ValueError: If summary_content is empty
        """
        if not summary_content or not summary_content.strip():
            raise ValueError("Summary content cannot be empty")

        # Strip core_ prefix if present
        log_uuid = uid.strip_prefix(log_uuid)

        # Verify ConversationLog exists
        cursor = self._conn.execute(
            "SELECT uuid, data, updated_at FROM entity WHERE uuid = ?",
            (log_uuid,)
        )
        row = cursor.fetchone()

        if row is None:
            raise ResourceNotFound(f"ConversationLog not found: {log_uuid}")

        # Parse existing data
        data = json.loads(row["data"]) if row["data"] else {}

        # Create summary object
        summary = {
            "timestamp": isodatetime.now(),
            "author": author,
            "content": summary_content,
        }

        # Add fragment_ids if provided
        if fragment_ids:
            summary["fragment_ids"] = fragment_ids

        # Update data with summary and collapsed flag
        data["summary"] = summary
        data["collapsed"] = True

        # Serialize updated data
        updated_data_json = json.dumps(data)

        # Update the entity
        now = isodatetime.now()
        self._conn.execute(
            """UPDATE entity
               SET data = ?, updated_at = ?
               WHERE uuid = ?""",
            (updated_data_json, now, log_uuid)
        )

        return FoldResult(
            log_uuid=uid.add_core_prefix(log_uuid),
            summary=summary,
            collapsed=True
        )

    def get(
        self,
        log_uuid: str,
    ) -> dict:
        """Get a ConversationLog by UUID.

        Args:
            log_uuid: UUID of ConversationLog (with or without core_ prefix)

        Returns:
            dict with uuid, _type, data, created_at, updated_at

        Raises:
            ResourceNotFound: If ConversationLog doesn't exist
        """
        # Strip core_ prefix if present
        log_uuid = uid.strip_prefix(log_uuid)

        cursor = self._conn.execute(
            """SELECT uuid, type, data, created_at, updated_at
               FROM entity
               WHERE uuid = ?""",
            (log_uuid,)
        )
        row = cursor.fetchone()

        if row is None:
            raise ResourceNotFound(f"ConversationLog not found: {log_uuid}")

        return {
            "uuid": uid.add_core_prefix(row["uuid"]),
            "_type": row["type"],
            "data": json.loads(row["data"]) if row["data"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
