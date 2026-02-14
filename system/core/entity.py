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

CONNECTION LIFECYCLE (Session 6.5 Refactor):
- Operations receive Core instance, not direct Connection
- All operations use self._conn property which calls core._get_conn()
- This enforces context manager usage at runtime
"""

from typing import TYPE_CHECKING

from ..exceptions import ResourceNotFound
from utils import hash_chain, uid
import utils.datetime as isodatetime

if TYPE_CHECKING:
    from . import Core

import sqlite3


class EntityOperations:
    """Entity registry operations with hash-based change tracking.

    Provides methods for creating, retrieving, and managing entities
    in the global entity registry with PRD v6 compliant hash chains.
    """

    def __init__(self, core: "Core"):
        """Initialize entity operations with a Core instance.

        Args:
            core: Core instance for database access

        Note:
            Uses self._conn property to get connection via core._get_conn(),
            which enforces context manager usage.
        """
        self._core = core

    @property
    def _conn(self) -> sqlite3.Connection:
        """Get database connection, enforcing context manager usage.

        Returns:
            SQLite connection

        Raises:
            RuntimeError: If Core is not being used as context manager
        """
        return self._core._get_conn()

    def create(
        self,
        entity_type: str,
        group_id: str | None = None,
        derived_from: str | None = None,
        data: dict | str | None = None
    ) -> str:
        """Create entity in global registry with auto-generated UUID and hash.

        Args:
            entity_type: The type of entity (e.g., 'Transaction', 'Recurrence')
            group_id: Optional group ID for clustering related entities
            derived_from: Optional ID of source entity for provenance tracking
            data: Optional JSON data for type-specific fields (dict or JSON string,
                   defaults to empty JSON object). Converted to JSON string for storage.

        Returns:
            The auto-generated entity UUID (plain UUID, no prefix)

        Raises:
            sqlite3.IntegrityError: If generated UUID already exists (extremely rare)
        """
        import json

        # Generate UUID with collision retry
        max_retries = 3
        for attempt in range(max_retries):
            entity_uuid = uid.generate_uuid()
            now = isodatetime.now()

            # Convert data to JSON string for storage
            if data is None:
                data_json = json.dumps({})
            elif isinstance(data, dict):
                data_json = json.dumps(data)
            else:
                data_json = data

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
                    """INSERT INTO entity (uuid, type, hash, previous_hash, version, group_id, derived_from, created_at, updated_at, data)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (entity_uuid, entity_type, initial_hash, None, 1, group_id, derived_from, now, now, data_json)
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
    ) -> dict:
        """Get entity by UUID, raise ResourceNotFound if not found.

        Args:
            entity_id: The UUID of the entity (plain or with prefix)
            table_or_view: Table or view name to query (default: 'entity')
            entity_type: Human-readable type name for error messages

        Returns:
            dict with entity data (JSON fields parsed to Python native types)

        Raises:
            ResourceNotFound: If entity_id doesn't exist
        """
        import json

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

        # Convert sqlite3.Row to dict and parse JSON fields
        entity = dict(row)

        # Parse JSON data to Python dict
        if entity.get('data'):
            entity['data'] = json.loads(entity['data'])

        return entity

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

    def update_data(self, entity_id: str, data: dict) -> None:
        """Update entity.data JSON field and refresh hash.

        This is a convenience method for Semantic API edit operations that
        modify the entity.data JSON field. The hash is automatically updated
        to reflect the data change.

        Args:
            entity_id: The UUID of the entity to update
            data: New data dictionary (will be JSON-serialized)

        Raises:
            ResourceNotFound: If entity_id doesn't exist
        """
        import json

        entity_id = uid.strip_prefix(entity_id)
        new_data = json.dumps(data)

        # Update entity.data and timestamp
        self._conn.execute(
            "UPDATE entity SET data = ?, updated_at = ? WHERE uuid = ?",
            (new_data, isodatetime.now(), entity_id)
        )

        # Refresh hash to maintain hash chain integrity
        self.update_hash(entity_id)

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

    def query_with_filters(
        self,
        entity_type: str | None = None,
        include_superseded: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Query entities with filters.

        Args:
            entity_type: Filter by entity type (None = all types)
            include_superseded: If True, include superseded entities (default: False)
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            Tuple of (list of entity dicts with parsed JSON, total count)
        """
        import json

        # Build WHERE clause
        where_parts = []
        params = []

        # Filter by type
        if entity_type:
            where_parts.append("type = ?")
            params.append(entity_type)

        # Filter by superseded status (default: exclude superseded)
        if not include_superseded:
            where_parts.append("superseded_by IS NULL")

        # Build full query
        where_clause = " AND ".join(where_parts) if where_parts else "1=1"
        query = f"""
            SELECT * FROM entity
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """

        params.extend([limit, offset])

        # Execute query
        rows = self._conn.execute(query, params).fetchall()

        # Convert rows to dicts and parse JSON data
        entities = []
        for row in rows:
            entity = dict(row)
            if entity.get('data'):
                entity['data'] = json.loads(entity['data'])
            entities.append(entity)

        # Get total count
        count_query = f"SELECT COUNT(*) as total FROM entity WHERE {where_clause}"
        total_row = self._conn.execute(count_query, params[:-2]).fetchone()
        total = total_row["total"]

        return entities, total

    def search(
        self,
        query: str,
        coverage: str = "content",
        limit: int = 20
    ) -> list[dict]:
        """Search entities by fuzzy text matching.

        Session 9: Public API for entity search.
        Uses SQLite LIKE with wildcards for fuzzy matching.

        Args:
            query: Search string (will be wrapped with % wildcards)
            coverage: Coverage level - "names" (type only), "content" (type+data), "full" (all fields)
            limit: Maximum results to return

        Returns:
            List of matching entity dicts with parsed JSON data

        Note:
            Only returns active entities (superseded_by IS NULL)
            Results ordered by updated_at DESC (most recent first)
        """
        import json

        # Build search pattern with wildcards
        search_pattern = f"%{query}%"

        # Build WHERE clause based on coverage level
        where_conditions = ["superseded_by IS NULL"]  # Only active entities
        params = []

        if coverage == "names":
            # Search in entity type only
            where_conditions.append("type LIKE ?")
            params.append(search_pattern)
        elif coverage == "content":
            # Search in type and JSON data
            where_conditions.append("(type LIKE ? OR data LIKE ?)")
            params.extend([search_pattern, search_pattern])
        else:  # full
            # Search all searchable fields (entity table has no metadata column)
            where_conditions.append("(type LIKE ? OR data LIKE ?)")
            params.extend([search_pattern, search_pattern])

        # Build query
        sql = f"""
            SELECT uuid, type, data, hash, previous_hash, version,
                   created_at, updated_at, superseded_by, superseded_at,
                   group_id, derived_from
            FROM entity
            WHERE {' AND '.join(where_conditions)}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        # Execute query and convert to dicts with parsed JSON
        rows = self._conn.execute(sql, params).fetchall()
        entities = []
        for row in rows:
            entity = dict(row)
            if entity.get('data'):
                entity['data'] = json.loads(entity['data'])
            entities.append(entity)

        return entities

    def exists(self, entity_id: str) -> bool:
        """Check if an entity exists.

        Args:
            entity_id: The UUID of the entity (plain or with prefix)

        Returns:
            True if entity exists, False otherwise
        """
        # Strip prefix if provided
        entity_id = uid.strip_prefix(entity_id)

        row = self._conn.execute(
            "SELECT 1 FROM entity WHERE uuid = ?",
            (entity_id,)
        ).fetchone()

        return row is not None
