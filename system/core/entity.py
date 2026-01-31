"""Entity registry operations with hash-based change tracking.

The entity registry provides a global table tracking all entities
in the system with PRD v6 compliant hash chains.

IMPORT CONVENTION:
- Core accesses these through core.entity property
- NO direct import needed when using Core API

ID GENERATION POLICY:
All entity IDs are auto-generated UUIDs. This design choice:
1. Prevents users from accidentally passing invalid or duplicate IDs
2. Encapsulates ID generation logic within the database layer
3. Ensures UUID v4 format compliance
4. Simplifies the API - users don't need to manage ID creation

HASH CHAIN (PRD v6):
- Each entity has a hash representing its current state
- hash = SHA256(metadata + previous_hash)
- Enables optimistic locking and conflict detection
"""

import sqlite3

from ..exceptions import ResourceNotFound
from ..utils import hash_chain, isodatetime, uid


class EntityOperations:
    """Entity registry operations with hash-based change tracking.

    Provides methods for creating, retrieving, and managing entities
    in the global entity registry with PRD v6 compliant hash chains.
    """

    def __init__(self, conn: sqlite3.Connection):
        """Initialize entity operations with a database connection.

        Args:
            conn: SQLite connection with row_factory set to sqlite3.Row
        """
        self._conn = conn

    def create(
        self,
        entity_type: str,
        group_id: str | None = None,
        derived_from: str | None = None
    ) -> str:
        """Create entity in global registry with auto-generated UUID and hash.

        Args:
            entity_type: The type of entity (e.g., 'transactions', 'recurrences')
            group_id: Optional group ID for clustering related entities
            derived_from: Optional ID of source entity for provenance tracking

        Returns:
            The auto-generated entity UUID (plain UUID, no prefix)

        Raises:
            sqlite3.IntegrityError: If generated UUID already exists (extremely rare)
        """
        # Generate UUID with collision retry
        max_retries = 3
        for attempt in range(max_retries):
            entity_uuid = uid.generate_uuid()
            now = isodatetime.now()

            # Compute initial hash (previous_hash is NULL for initial entities)
            initial_hash = hash_chain.compute_entity_hash(
                entity_type=entity_type,
                created_at=now,
                updated_at=now,
                group_id=group_id,
                derived_from=derived_from,
                previous_hash=None,  # Initial entity has no previous hash
            )

            try:
                self._conn.execute(
                    """INSERT INTO entity (uuid, type, hash, previous_hash, version, group_id, derived_from, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (entity_uuid, entity_type, initial_hash, None, 1, group_id, derived_from, now, now)
                )
                return entity_uuid
            except sqlite3.IntegrityError:
                # UUID collision - retry with new UUID
                if attempt == max_retries - 1:
                    raise

        # Should never reach here
        raise RuntimeError("Failed to generate unique UUID after retries")

    def get_by_id(
        self,
        entity_id: str,
        table_or_view: str = "entity",
        entity_type: str = "Entity"
    ) -> sqlite3.Row:
        """Get entity by UUID, raise ResourceNotFound if not found.

        Args:
            entity_id: The UUID of the entity (plain or with prefix)
            table_or_view: Table or view name to query (default: 'entity')
            entity_type: Human-readable type name for error messages

        Returns:
            sqlite3.Row with entity data

        Raises:
            ResourceNotFound: If entity_id doesn't exist
        """
        # Strip prefix if provided
        entity_id = uid.strip_prefix(entity_id)

        # Use uuid column for new schema
        column = "uuid" if table_or_view == "entity" else "uuid"

        row = self._conn.execute(
            f"SELECT * FROM {table_or_view} WHERE {column} = ?",
            (entity_id,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(
                f"{entity_type} '{entity_id}' not found",
                {"entity_id": entity_id}
            )

        return row

    def supersede(self, old_id: str, new_id: str) -> None:
        """Mark entity as superseded by another entity.

        Updates the old entity's hash and version to reflect the supersession.

        Args:
            old_id: The UUID of the entity being superseded
            new_id: The UUID of the superseding entity

        Note:
            Both entities should exist before calling this method.
            The old entity will have superseded_by and superseded_at set.
        """
        # Strip prefixes if provided
        old_id = uid.strip_prefix(old_id)
        new_id = uid.strip_prefix(new_id)

        now = isodatetime.now()

        # Get current state for hash computation
        current = self._conn.execute(
            "SELECT type, hash, version, created_at, group_id, derived_from FROM entity WHERE uuid = ?",
            (old_id,)
        ).fetchone()

        if not current:
            raise ResourceNotFound(f"Entity '{old_id}' not found", {"entity_id": old_id})

        # Compute new hash with superseded_by set
        new_hash = hash_chain.compute_entity_hash(
            entity_type=current["type"],
            created_at=current["created_at"],
            updated_at=now,
            group_id=current["group_id"],
            derived_from=current["derived_from"],
            superseded_by=new_id,
            superseded_at=now,
            previous_hash=current["hash"],
        )

        self._conn.execute(
            """UPDATE entity
               SET superseded_by = ?, superseded_at = ?, updated_at = ?, hash = ?, version = version + 1
               WHERE uuid = ?""",
            (new_id, now, now, new_hash, old_id)
        )

    def update_hash(self, entity_id: str) -> str:
        """Update hash and version for an entity after domain data changes.

        This should be called after updating domain-specific tables (transactions,
        users, etc.) to maintain the hash chain.

        Args:
            entity_id: The UUID of the entity

        Returns:
            The new hash value

        Raises:
            ResourceNotFound: If entity_id doesn't exist
        """
        # Strip prefix if provided
        entity_id = uid.strip_prefix(entity_id)

        now = isodatetime.now()

        # Get current state
        current = self._conn.execute(
            "SELECT type, hash, version, created_at, group_id, derived_from, superseded_by, superseded_at FROM entity WHERE uuid = ?",
            (entity_id,)
        ).fetchone()

        if not current:
            raise ResourceNotFound(f"Entity '{entity_id}' not found", {"entity_id": entity_id})

        # Compute new hash
        new_hash = hash_chain.compute_next_hash(
            entity_type=current["type"],
            created_at=current["created_at"],
            updated_at=now,
            current_hash=current["hash"],
            group_id=current["group_id"],
            derived_from=current["derived_from"],
            superseded_by=current["superseded_by"],
            superseded_at=current["superseded_at"],
        )

        # Update hash, version, and timestamp
        self._conn.execute(
            "UPDATE entity SET hash = ?, previous_hash = ?, version = version + 1, updated_at = ? WHERE uuid = ?",
            (new_hash, current["hash"], now, entity_id)
        )

        return new_hash

    def update_timestamp(self, entity_id: str) -> None:
        """Update the updated_at timestamp (deprecated: use update_hash).

        This method is kept for backward compatibility but should be replaced
        with update_hash() to maintain the hash chain.

        Args:
            entity_id: The UUID of the entity to update
        """
        self.update_hash(entity_id)

    def get_current_hash(self, entity_id: str) -> str:
        """Get the current hash for an entity.

        Args:
            entity_id: The UUID of the entity

        Returns:
            The current hash value

        Raises:
            ResourceNotFound: If entity_id doesn't exist
        """
        entity_id = uid.strip_prefix(entity_id)

        row = self._conn.execute(
            "SELECT hash FROM entity WHERE uuid = ?",
            (entity_id,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(f"Entity '{entity_id}' not found", {"entity_id": entity_id})

        return row["hash"]

    def check_conflict(self, entity_id: str, based_on_hash: str) -> bool:
        """Check if there's a conflict based on the provided hash.

        Args:
            entity_id: The UUID of the entity
            based_on_hash: The hash the client expects

        Returns:
            True if there's a conflict (hashes don't match), False otherwise

        Raises:
            ResourceNotFound: If entity_id doesn't exist
        """
        current_hash = self.get_current_hash(entity_id)
        return current_hash != based_on_hash
