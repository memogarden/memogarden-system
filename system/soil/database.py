"""Soil database operations for MemoGarden Soil."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC
from pathlib import Path

from .item import SOIL_UUID_PREFIX, Evidence, Item, current_day, generate_soil_uuid
from .relation import SystemRelation


class Soil:
    """Soil database for immutable Items and System Relations.

    CONNECTION LIFECYCLE (Session 6.5 Refactor):
    - Soil MUST be used as context manager (enforced at runtime)
    - __enter__: Marks Soil as active, creates connection, returns self
    - __exit__: Commits on success, rollbacks on exception, always closes
    - Operations call _get_connection() which raises RuntimeError if not in context
    """

    def __init__(self, db_path: str | Path = "soil.db"):
        """Initialize Soil database.

        Args:
            db_path: Path to SQLite database file

        Note:
            Soil must be used as context manager. Operations will raise
            RuntimeError if called outside of 'with' statement.
        """
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._in_context = False  # Track if we're inside a context manager

    def _get_connection(self) -> sqlite3.Connection:
        """Get connection, enforcing context manager usage.

        Returns:
            SQLite connection

        Raises:
            RuntimeError: If Soil is not being used as context manager
        """
        if not self._in_context:
            raise RuntimeError(
                "Soil must be used as context manager. "
                "Use: with get_soil() as soil: ..."
            )
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
        return self._conn

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Soil:
        """Enter context manager for transaction.

        Returns:
            self for use in with-statement
        """
        self._in_context = True
        self._get_connection()  # Ensure connection is created
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager, committing or rolling back transaction.

        Args:
            exc_type: Exception type if exception occurred, else None
            exc_val: Exception value if exception occurred, else None
            exc_tb: Exception traceback if exception occurred, else None
        """
        self._in_context = False
        try:
            if self._conn is not None:  # Only commit/rollback if connection exists
                if exc_type is None:
                    # No exception - commit the transaction
                    self._conn.commit()
                else:
                    # Exception occurred - rollback the transaction
                    self._conn.rollback()
        finally:
            # Always close connection
            self.close()

    # ==========================================================================
    # INITIALIZATION
    # ==========================================================================

    def init_schema(self):
        """Initialize database schema from bundled schema.sql."""
        from pathlib import Path
        schema_path = Path(__file__).parent.parent / "schemas" / "sql" / "soil.sql"
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        with open(schema_path) as f:
            schema_sql = f.read()

        conn = self._get_connection()
        conn.executescript(schema_sql)
        conn.commit()

    def get_schema_version(self) -> str | None:
        """Get current schema version."""
        cursor = self._get_connection().execute(
            "SELECT value FROM _schema_metadata WHERE key = 'version'"
        )
        row = cursor.fetchone()
        return row[0] if row else None

    # ==========================================================================
    # ITEM OPERATIONS
    # ==========================================================================

    def create_item(self, item: Item) -> str:
        """Create a new Item in Soil.

        Args:
            item: Item to create

        Returns:
            UUID of created Item
        """
        # Compute hash if not provided
        if item.integrity_hash is None:
            item.integrity_hash = item.compute_hash()

        conn = self._get_connection()
        conn.execute(
            """INSERT INTO item (uuid, _type, realized_at, canonical_at, integrity_hash,
                              fidelity, superseded_by, superseded_at, data, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                item.uuid,
                item._type,
                item.realized_at,
                item.canonical_at,
                item.integrity_hash,
                item.fidelity,
                item.superseded_by,
                item.superseded_at,
                json.dumps(item.data),
                json.dumps(item.metadata) if item.metadata else None,
            )
        )
        return item.uuid

    def get_item(self, uuid: str) -> Item | None:
        """Get Item by UUID.

        Args:
            uuid: Item UUID (with or without "soil_" prefix)

        Returns:
            Item if found, None otherwise
        """
        # Ensure prefix
        if not uuid.startswith(SOIL_UUID_PREFIX):
            uuid = f"{SOIL_UUID_PREFIX}{uuid}"

        cursor = self._get_connection().execute(
            "SELECT * FROM item WHERE uuid = ?", (uuid,)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return Item(
            uuid=row["uuid"],
            _type=row["_type"],
            realized_at=row["realized_at"],
            canonical_at=row["canonical_at"],
            integrity_hash=row["integrity_hash"],
            fidelity=row["fidelity"],
            superseded_by=row["superseded_by"],
            superseded_at=row["superseded_at"],
            data=json.loads(row["data"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def mark_superseded(self, original_uuid: str, superseded_by_uuid: str, superseded_at: str) -> bool:
        """Mark an Item as superseded by another Item.

        Updates the original Item's superseded_by and superseded_at fields
        to indicate it has been replaced by a new version.

        Args:
            original_uuid: UUID of the Item being superseded
            superseded_by_uuid: UUID of the new Item that supersedes it
            superseded_at: ISO 8601 timestamp when supersession occurred

        Returns:
            True if Item was found and updated, False if not found
        """
        # Ensure UUIDs have prefix
        if not original_uuid.startswith(SOIL_UUID_PREFIX):
            original_uuid = f"{SOIL_UUID_PREFIX}{original_uuid}"
        if not superseded_by_uuid.startswith(SOIL_UUID_PREFIX):
            superseded_by_uuid = f"{SOIL_UUID_PREFIX}{superseded_by_uuid}"

        conn = self._get_connection()
        cursor = conn.execute(
            """UPDATE item
               SET superseded_by = ?, superseded_at = ?
               WHERE uuid = ?""",
            (superseded_by_uuid, superseded_at, original_uuid)
        )
        return cursor.rowcount > 0

    def find_item_by_rfc_message_id(self, message_id: str) -> Item | None:
        """Find Email item by RFC Message-ID.

        Args:
            message_id: RFC 822 Message-ID header value

        Returns:
            Email Item if found, None otherwise
        """
        cursor = self._get_connection().execute(
            """SELECT * FROM item
               WHERE _type = 'Email'
               AND json_extract(data, '$.rfc_message_id') = ?""",
            (message_id,)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return Item(
            uuid=row["uuid"],
            _type=row["_type"],
            realized_at=row["realized_at"],
            canonical_at=row["canonical_at"],
            integrity_hash=row["integrity_hash"],
            fidelity=row["fidelity"],
            superseded_by=row["superseded_by"],
            superseded_at=row["superseded_at"],
            data=json.loads(row["data"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def list_items(self, _type: str | None = None, limit: int = 100) -> list[Item]:
        """List Items, optionally filtered by type.

        Args:
            _type: Filter by Item type (e.g., 'Email', 'Note')
            limit: Maximum number of Items to return

        Returns:
            List of Items
        """
        if _type:
            cursor = self._get_connection().execute(
                """SELECT * FROM item WHERE _type = ? ORDER BY realized_at DESC LIMIT ?""",
                (_type, limit)
            )
        else:
            cursor = self._get_connection().execute(
                """SELECT * FROM item ORDER BY realized_at DESC LIMIT ?""",
                (limit,)
            )

        items = []
        for row in cursor.fetchall():
            items.append(Item(
                uuid=row["uuid"],
                _type=row["_type"],
                realized_at=row["realized_at"],
                canonical_at=row["canonical_at"],
                integrity_hash=row["integrity_hash"],
                fidelity=row["fidelity"],
                superseded_by=row["superseded_by"],
                superseded_at=row["superseded_at"],
                data=json.loads(row["data"]),
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            ))
        return items

    # ==========================================================================
    # SYSTEM RELATION OPERATIONS
    # ==========================================================================

    def create_relation(self, relation: SystemRelation) -> str:
        """Create a System Relation.

        Args:
            relation: SystemRelation to create

        Returns:
            UUID of created relation
        """
        # Convert Evidence to dict if needed
        evidence_data = None
        if relation.evidence:
            if isinstance(relation.evidence, Evidence):
                evidence_data = relation.evidence.to_dict()
            else:
                evidence_data = relation.evidence

        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO system_relation (uuid, kind, source, source_type, target, target_type,
                                              created_at, evidence, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    relation.uuid,
                    relation.kind,
                    relation.source,
                    relation.source_type,
                    relation.target,
                    relation.target_type,
                    relation.created_at,
                    json.dumps(evidence_data) if evidence_data else None,
                    json.dumps(relation.metadata) if relation.metadata else None,
                )
            )
        except sqlite3.IntegrityError:
            # Relation already exists (unique constraint on kind, source, target)
            # Fetch existing relation's UUID
            cursor = conn.execute(
                """SELECT uuid FROM system_relation
                   WHERE kind = ? AND source = ? AND target = ?""",
                (relation.kind, relation.source, relation.target)
            )
            row = cursor.fetchone()
            return row[0] if row else relation.uuid

        return relation.uuid

    def create_replies_to_relation(
        self,
        reply_uuid: str,
        parent_uuid: str,
        evidence: Evidence | dict | None = None,
    ) -> str | None:
        """Create a 'replies_to' system relation for email threading.

        Args:
            reply_uuid: UUID of reply Item
            parent_uuid: UUID of parent Item being replied to
            evidence: Optional provenance information

        Returns:
            UUID of created relation, or None if parent not found
        """
        parent = self.get_item(parent_uuid)
        if parent is None:
            return None

        relation = SystemRelation(
            uuid=generate_soil_uuid(),
            kind="replies_to",
            source=reply_uuid,
            source_type="item",
            target=parent_uuid,
            target_type="item",
            created_at=current_day(),
            evidence=evidence,
        )
        return self.create_relation(relation)

    def get_relations(self, source: str | None = None, kind: str | None = None) -> list[SystemRelation]:
        """Get System Relations.

        Args:
            source: Filter by source UUID
            kind: Filter by relation kind

        Returns:
            List of SystemRelations
        """
        query = "SELECT * FROM system_relation WHERE 1=1"
        params = []

        if source:
            query += " AND source = ?"
            params.append(source)
        if kind:
            query += " AND kind = ?"
            params.append(kind)

        cursor = self._get_connection().execute(f"{query} ORDER BY created_at DESC", params)

        relations = []
        for row in cursor.fetchall():
            relations.append(SystemRelation(
                uuid=row["uuid"],
                kind=row["kind"],
                source=row["source"],
                source_type=row["source_type"],
                target=row["target"],
                target_type=row["target_type"],
                created_at=row["created_at"],
                evidence=json.loads(row["evidence"]) if row["evidence"] else None,
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            ))
        return relations

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================

    def count_items(self, _type: str | None = None) -> int:
        """Count Items.

        Args:
            _type: Filter by Item type

        Returns:
            Count of Items
        """
        if _type:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM item WHERE _type = ?", (_type,)
            )
        else:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM item"
            )
        return cursor.fetchone()[0]

    def count_relations(self, kind: str | None = None) -> int:
        """Count System Relations.

        Args:
            kind: Filter by relation kind

        Returns:
            Count of relations
        """
        if kind:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM system_relation WHERE kind = ?", (kind,)
            )
        else:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM system_relation"
            )
        return cursor.fetchone()[0]


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_soil(db_path: str | Path = "soil.db", init: bool = False) -> Soil:
    """Get or create Soil database.

    Args:
        db_path: Path to SQLite database file
        init: If True, initialize schema if not exists

    Returns:
        Soil instance
    """
    soil = Soil(db_path)

    if init:
        # Check if already initialized
        if soil.get_schema_version() is None:
            soil.init_schema()

    return soil


def create_email_item(**kwargs) -> Item:
    """Create an Email Item from keyword arguments.

    Accepts structured dict with _type, data, metadata.
    Example:
        create_email_item(
            _type="Email",
            realized_at="2026-01-30T12:35:00Z",
            canonical_at="2026-01-30T12:34:56Z",
            fidelity="full",
            data={...},
            metadata={...},
        )

    Args:
        **kwargs: Email item fields

    Returns:
        Email Item
    """
    from datetime import datetime

    # Extract data fields
    data = kwargs.get("data", {})

    return Item(
        uuid=generate_soil_uuid(),
        _type=kwargs.get("_type", "Email"),
        realized_at=kwargs.get("realized_at", datetime.now(UTC).isoformat()),
        canonical_at=kwargs.get("canonical_at", data.get("sent_at")),
        fidelity=kwargs.get("fidelity", "full"),
        data=data,
        metadata=kwargs.get("metadata", {}),
    )
