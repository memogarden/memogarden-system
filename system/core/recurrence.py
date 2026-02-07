"""Recurrence-specific operations.

IMPORT CONVENTION:
- Core accesses these through core.recurrence property
- Receives Core reference for coordinating entity registry operations

ID GENERATION POLICY:
All recurrence IDs are auto-generated via entity.create(). This design:
1. Prevents users from accidentally passing invalid or duplicate IDs
2. Ensures entity registry entry is always created with recurrence
3. Encapsulates ID generation logic within the database layer
4. Simplifies the API - users call core.recurrence.create() without managing IDs

SCHEMA (v20260130):
- Uses generic entity table with type='Recurrence'
- Recurrence data stored in data JSON field
"""

import json
import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..exceptions import ResourceNotFound
from ..utils import isodatetime, uid
from . import query

if TYPE_CHECKING:
    from . import Core


class RecurrenceOperations:
    """Recurrence operations.

    Coordinates with EntityOperations through Core reference.
    Automatically creates entity registry entries via core.entity.create().

    Uses the new schema (v20260130) where recurrence data is stored
    in the entity.data JSON field.
    """

    def __init__(self, conn: sqlite3.Connection, core: "Core | None" = None):
        """Initialize recurrence operations.

        Args:
            conn: SQLite connection with row_factory set to sqlite3.Row
            core: Core reference for coordinating entity registry operations.
                  Required for create() to auto-generate entity IDs.
        """
        self._conn = conn
        self._core = core

    def get_by_id(self, recurrence_id: str) -> sqlite3.Row:
        """Get recurrence by ID.

        Args:
            recurrence_id: The UUID of the recurrence (plain or with core_ prefix)

        Returns:
            sqlite3.Row with recurrence data from entity table

        Raises:
            ResourceNotFound: If recurrence_id doesn't exist
        """
        # Strip prefix if provided
        recurrence_id = uid.strip_prefix(recurrence_id)

        row = self._conn.execute(
            """SELECT
                e.uuid,
                e.type,
                e.hash,
                e.previous_hash,
                e.version,
                e.created_at,
                e.updated_at,
                e.superseded_by,
                e.superseded_at,
                e.group_id,
                e.derived_from,
                json_extract(e.data, '$.rrule') as rrule,
                json_extract(e.data, '$.entities') as entities,
                json_extract(e.data, '$.valid_from') as valid_from,
                json_extract(e.data, '$.valid_until') as valid_until
               FROM entity e
               WHERE e.type = 'Recurrence' AND e.uuid = ?""",
            (recurrence_id,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(
                f"Recurrence '{recurrence_id}' not found",
                {"recurrence_id": recurrence_id}
            )

        return row

    def create(
        self,
        rrule: str,
        entities: str,
        valid_from: datetime,
        valid_until: datetime | None = None,
    ) -> str:
        """Create a recurrence with automatic entity registry creation.

        Creates an entity entry with type='Recurrence' and stores recurrence
        data in the data JSON field. The recurrence ID is auto-generated via
        core.entity.create().

        Args:
            rrule: iCal RRULE string (e.g., "FREQ=MONTHLY;BYDAY=2FR")
            entities: JSON string with transaction template(s)
            valid_from: Start of recurrence window (datetime)
            valid_until: Optional end of recurrence window (datetime, null = forever)

        Returns:
            The auto-generated recurrence ID (UUID v4 string, no prefix)

        Raises:
            ValueError: If RecurrenceOperations initialized without Core reference
            sqlite3.IntegrityError: If generated UUID already exists (extremely rare)
        """
        if self._core is None:
            raise ValueError(
                "RecurrenceOperations requires Core reference for create(). "
                "Use core.recurrence.create() instead of standalone RecurrenceOperations."
            )

        # Build recurrence data as JSON
        recurrence_data = {
            "rrule": rrule,
            "entities": entities,
            "valid_from": isodatetime.to_timestamp(valid_from),
            "valid_until": isodatetime.to_timestamp(valid_until) if valid_until else None
        }

        # Create entity registry entry with recurrence data
        recurrence_id = self._core.entity.create(
            entity_type="Recurrence",
            group_id=None,
            derived_from=None
        )

        # Update entity with recurrence data
        self._conn.execute(
            """UPDATE entity
               SET data = ?
               WHERE uuid = ?""",
            (json.dumps(recurrence_data), recurrence_id)
        )

        return recurrence_id

    def list(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
        offset: int = 0
    ) -> list[sqlite3.Row]:
        """List recurrences with optional filtering.

        Args:
            filters: Dictionary of filter conditions (empty for all)
                - valid_from: ISO 8601 datetime string, filter recurrences starting after this
                - valid_until: ISO 8601 datetime string, filter recurrences ending before this
                - include_superseded: If False (default), excludes superseded recurrences
            limit: Maximum number of results to return (default: 100)
            offset: Number of results to skip (default: 0)

        Returns:
            List of sqlite3.Row objects with recurrence data
        """
        filters = filters or {}

        param_map = {
            "valid_from": "json_extract(e.data, '$.valid_from') >= ?",
            "valid_until": "json_extract(e.data, '$.valid_until') <= ?",
        }

        # Filter out None values and exclude non-column flags like include_superseded
        conditions = {
            k: v for k, v in filters.items()
            if v is not None and k != "include_superseded"
        }

        where_clause, params = query.build_where_clause(conditions, param_map)

        # Always filter by type='Recurrence'
        if where_clause == "1=1":
            where_clause = "e.type = 'Recurrence'"
        else:
            where_clause = "e.type = 'Recurrence' AND " + where_clause

        # Handle superseded filter as special case (exclude if not explicitly included)
        if not filters.get("include_superseded"):
            where_clause += " AND e.superseded_by IS NULL"
        params.extend([limit, offset])

        query_sql = f"""
            SELECT
                e.uuid,
                e.type,
                e.hash,
                e.previous_hash,
                e.version,
                e.created_at,
                e.updated_at,
                e.superseded_by,
                e.superseded_at,
                e.group_id,
                e.derived_from,
                json_extract(e.data, '$.rrule') as rrule,
                json_extract(e.data, '$.entities') as entities,
                json_extract(e.data, '$.valid_from') as valid_from,
                json_extract(e.data, '$.valid_until') as valid_until
            FROM entity e
            WHERE {where_clause}
            ORDER BY e.created_at DESC
            LIMIT ? OFFSET ?
        """

        return self._conn.execute(query_sql, params).fetchall()

    def update(self, recurrence_id: str, data: dict[str, Any]) -> None:
        """Update recurrence with partial data.

        Args:
            recurrence_id: The UUID of the recurrence to update

        Note:
            - Only non-None fields in data are updated
            - entity.updated_at is also updated
        """
        # Strip prefix if provided
        recurrence_id = uid.strip_prefix(recurrence_id)

        # Convert datetime fields to timestamps if present
        if "valid_from" in data and data["valid_from"] is not None:
            data["valid_from"] = isodatetime.to_timestamp(data["valid_from"])
        if "valid_until" in data and data["valid_until"] is not None:
            data["valid_until"] = isodatetime.to_timestamp(data["valid_until"])

        if data:
            # Build JSON updates using json_set for each field
            json_updates = []
            params = []

            for key, value in data.items():
                json_updates.append("json_set(entity.data, ?, ?)")
                params.extend([f"$.{key}", value])

            params.append(recurrence_id)

            # Build nested json_set calls to update multiple fields
            update_sql = "UPDATE entity SET data = "
            for i, update in enumerate(json_updates):
                if i == 0:
                    update_sql += update
                else:
                    update_sql = f"json_set({update_sql}, {update})"

            update_sql += " WHERE uuid = ?"

            self._conn.execute(update_sql, params)

            # Update entity hash chain
            if self._core is not None:
                self._core.entity.update_hash(recurrence_id)
            else:
                # Fallback for standalone usage
                now = isodatetime.now()
                self._conn.execute(
                    "UPDATE entity SET updated_at = ? WHERE uuid = ?",
                    (now, recurrence_id)
                )
