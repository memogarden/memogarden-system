"""Transaction-specific operations with hash-based change tracking.

IMPORT CONVENTION:
- Core accesses these through core.transaction property
- Receives Core reference for coordinating entity registry operations

ID GENERATION POLICY:
All transaction IDs are auto-generated via entity.create(). This design:
1. Prevents users from accidentally passing invalid or duplicate IDs
2. Ensures entity registry entry is always created with transaction
3. Encapsulates ID generation logic within the database layer
4. Simplifies the API - users call core.transaction.create() without managing IDs

HASH CHAIN (PRD v6):
- Transaction updates trigger entity hash chain updates
- Enables optimistic locking and conflict detection

SCHEMA (v20260130):
- Uses generic entity table with type='Transaction'
- Transaction data stored in data JSON field
"""

import json
import sqlite3
from datetime import date
from typing import TYPE_CHECKING, Any

from ..exceptions import ResourceNotFound
from ..utils import isodatetime, uid
from . import query

if TYPE_CHECKING:
    from . import Core


class TransactionOperations:
    """Transaction operations with hash-based change tracking.

    Coordinates with EntityOperations through Core reference.
    Automatically creates entity registry entries via core.entity.create().
    Maintains hash chain on updates via core.entity.update_hash().

    Uses the new schema (v20260130) where transaction data is stored
    in the entity.data JSON field.
    """

    def __init__(self, conn: sqlite3.Connection, core: "Core | None" = None):
        """Initialize transaction operations.

        Args:
            conn: SQLite connection with row_factory set to sqlite3.Row
            core: Core reference for coordinating entity registry operations.
                  Required for create() to auto-generate entity IDs.
        """
        self._conn = conn
        self._core = core

    def get_by_id(self, transaction_id: str) -> sqlite3.Row:
        """Get transaction by ID.

        Args:
            transaction_id: The UUID of the transaction (plain or with core_ prefix)

        Returns:
            sqlite3.Row with transaction data from entity table

        Raises:
            ResourceNotFound: If transaction_id doesn't exist
        """
        # Strip prefix if provided
        transaction_id = uid.strip_prefix(transaction_id)

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
                json_extract(e.data, '$.amount') as amount,
                json_extract(e.data, '$.currency') as currency,
                json_extract(e.data, '$.transaction_date') as transaction_date,
                json_extract(e.data, '$.description') as description,
                json_extract(e.data, '$.account') as account,
                json_extract(e.data, '$.category') as category,
                json_extract(e.data, '$.author') as author,
                json_extract(e.data, '$.recurrence_id') as recurrence_id,
                json_extract(e.data, '$.notes') as notes
               FROM entity e
               WHERE e.type = 'Transaction' AND e.uuid = ?""",
            (transaction_id,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(
                f"Transaction '{transaction_id}' not found",
                {"transaction_id": transaction_id}
            )

        return row

    def create(
        self,
        amount: float,
        transaction_date: date,
        description: str,
        account: str,
        category: str | None = None,
        notes: str | None = None,
        author: str = "system"
    ) -> str:
        """Create a transaction with automatic entity registry creation.

        Creates an entity entry with type='Transaction' and stores transaction
        data in the data JSON field. The transaction ID is auto-generated via
        core.entity.create().

        Args:
            amount: Transaction amount
            transaction_date: Date of the transaction
            description: Short description/title
            account: Account label
            category: Optional category label
            notes: Optional detailed notes
            author: Creator identifier (default: "system")

        Returns:
            The auto-generated transaction ID (plain UUID v4 string, no prefix)

        Raises:
            ValueError: If TransactionOperations initialized without Core reference
            sqlite3.IntegrityError: If generated UUID already exists (extremely rare)
        """
        if self._core is None:
            raise ValueError(
                "TransactionOperations requires Core reference for create(). "
                "Use core.transaction.create() instead of standalone TransactionOperations."
            )

        # Build transaction data as JSON
        transaction_data = {
            "amount": amount,
            "currency": "SGD",
            "transaction_date": isodatetime.to_datestring(transaction_date),
            "description": description,
            "account": account,
            "category": category,
            "notes": notes,
            "author": author,
            "recurrence_id": None
        }

        # Create entity registry entry with transaction data
        transaction_id = self._core.entity.create(
            entity_type="Transaction",
            group_id=None,
            derived_from=None
        )

        # Update entity with transaction data
        self._conn.execute(
            """UPDATE entity
               SET data = ?
               WHERE uuid = ?""",
            (json.dumps(transaction_data), transaction_id)
        )

        return transaction_id

    def list_transactions(
        self,
        filters: dict[str, Any],
        limit: int = 100,
        offset: int = 0
    ) -> list[sqlite3.Row]:
        """List transactions with filtering.

        Args:
            filters: Dictionary of filter conditions:
                - start_date: ISO 8601 date string (inclusive)
                - end_date: ISO 8601 date string (inclusive)
                - account: Account label
                - category: Category label
                - include_superseded: If False (default), excludes superseded transactions
            limit: Maximum number of results to return (default: 100)
            offset: Number of results to skip (default: 0)

        Returns:
            List of sqlite3.Row objects with transaction data (including hash/previous_hash/version)
        """
        param_map = {
            "start_date": "json_extract(e.data, '$.transaction_date') >= ?",
            "end_date": "json_extract(e.data, '$.transaction_date') <= ?",
            "account": "json_extract(e.data, '$.account') = ?",
            "category": "json_extract(e.data, '$.category') = ?",
        }

        # Filter out None values and exclude non-column flags like include_superseded
        conditions = {
            k: v for k, v in filters.items()
            if v is not None and k != "include_superseded"
        }

        where_clause, params = query.build_where_clause(conditions, param_map)

        # Always filter by type='Transaction'
        if where_clause == "1=1":
            where_clause = "e.type = 'Transaction'"
        else:
            where_clause = "e.type = 'Transaction' AND " + where_clause

        # Handle superseded filter as special case (exclude if not explicitly included)
        # This is added separately because "IS NULL" doesn't use a placeholder
        if not filters.get("include_superseded"):
            where_clause += " AND e.superseded_by IS NULL"
        params.extend([limit, offset])

        # Query for new schema with json_extract
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
                json_extract(e.data, '$.amount') as amount,
                json_extract(e.data, '$.currency') as currency,
                json_extract(e.data, '$.transaction_date') as transaction_date,
                json_extract(e.data, '$.description') as description,
                json_extract(e.data, '$.account') as account,
                json_extract(e.data, '$.category') as category,
                json_extract(e.data, '$.author') as author,
                json_extract(e.data, '$.recurrence_id') as recurrence_id,
                json_extract(e.data, '$.notes') as notes
            FROM entity e
            WHERE {where_clause}
            ORDER BY json_extract(e.data, '$.transaction_date') DESC, e.created_at DESC
            LIMIT ? OFFSET ?
        """

        return self._conn.execute(query_sql, params).fetchall()

    def update(self, transaction_id: str, data: dict[str, Any]) -> str:
        """Update transaction with partial data and update hash chain.

        Args:
            transaction_id: The UUID of the transaction to update
            data: Dictionary of field names to values to update

        Returns:
            The new entity hash

        Note:
            - Only non-None fields in data are updated
            - entity.hash, entity.previous_hash, and entity.version are updated

        Raises:
            ResourceNotFound: If transaction_id doesn't exist
        """
        # Strip prefix if provided
        transaction_id = uid.strip_prefix(transaction_id)

        # Convert date to string if present
        if "transaction_date" in data and data["transaction_date"] is not None:
            data["transaction_date"] = isodatetime.to_datestring(data["transaction_date"])

        if data:
            # Build JSON updates using nested json_set for each field
            # json_set(data, '$.field', value) updates individual fields
            params = []

            # Build the nested json_set calls
            # Build parameter list for JSON fields
            for key, value in data.items():
                params.extend([f"$.{key}", value])

            # Build the nested json_set expression
            # Example: json_set(json_set(entity.data, '$.field1', ?), '$.field2', ?)
            json_expression = "entity.data"
            for i in range(len(data)):
                json_expression = f"json_set({json_expression}, ?, ?)"

            params.append(transaction_id)

            # Update transaction data in entity table
            update_sql = f"UPDATE entity SET data = {json_expression} WHERE uuid = ?"

            self._conn.execute(update_sql, params)

            # Update entity hash chain
            if self._core is not None:
                return self._core.entity.update_hash(transaction_id)
            else:
                # Fallback for standalone usage (shouldn't happen in practice)
                now = isodatetime.now()
                self._conn.execute(
                    "UPDATE entity SET updated_at = ? WHERE uuid = ?",
                    (now, transaction_id)
                )
                return ""

        return ""

    def get_accounts(self) -> list[str]:
        """Get distinct account labels.

        Returns:
            List of unique account strings
        """
        rows = self._conn.execute(
            """SELECT DISTINCT json_extract(data, '$.account') as account
               FROM entity
               WHERE type = 'Transaction' AND json_extract(data, '$.account') IS NOT NULL
               ORDER BY account"""
        ).fetchall()
        return [row["account"] for row in rows]

    def get_categories(self) -> list[str]:
        """Get distinct category labels.

        Returns:
            List of unique category strings
        """
        rows = self._conn.execute(
            """SELECT DISTINCT json_extract(data, '$.category') as category
               FROM entity
               WHERE type = 'Transaction' AND json_extract(data, '$.category') IS NOT NULL
               ORDER BY category"""
        ).fetchall()
        return [row["category"] for row in rows]
