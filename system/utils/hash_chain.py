"""Hash computation for entity change tracking.

Per PRD v6, all Entities maintain a cryptographic hash chain:
- Entity.hash = SHA256(content + previous_hash)
- Enables conflict detection and audit trails
"""

import hashlib
import sqlite3
from typing import Any


def compute_entity_hash(
    entity_type: str,
    created_at: str,
    updated_at: str,
    group_id: str | None = None,
    derived_from: str | None = None,
    superseded_by: str | None = None,
    superseded_at: str | None = None,
    previous_hash: str | None = None,
) -> str:
    """Compute the hash for an entity based on its state.

    The hash includes all entity metadata fields. Domain-specific fields
    (transaction amount, username, etc.) are NOT included in the entity hash
    because they're stored in separate tables.

    For complete change tracking, domain tables compute their own hashes
    and the entity hash represents the "entity registry" state.

    Args:
        entity_type: The type of entity (e.g., 'transactions', 'users')
        created_at: ISO 8601 timestamp
        updated_at: ISO 8601 timestamp
        group_id: Optional group ID
        derived_from: Optional derived_from reference
        superseded_by: Optional superseded_by reference
        superseded_at: Optional superseded_at timestamp
        previous_hash: Previous state hash (for chain continuity)

    Returns:
        Hex-encoded SHA256 hash string
    """
    # Build hash input from all fields in a consistent order
    hash_input = (
        f"type:{entity_type}|"
        f"created_at:{created_at}|"
        f"updated_at:{updated_at}|"
        f"group_id:{group_id or ''}|"
        f"derived_from:{derived_from or ''}|"
        f"superseded_by:{superseded_by or ''}|"
        f"superseded_at:{superseded_at or ''}|"
        f"previous_hash:{previous_hash or ''}"
    )

    return hashlib.sha256(hash_input.encode()).hexdigest()


def compute_entity_hash_from_row(row: sqlite3.Row) -> str:
    """Compute entity hash from a database row.

    Convenience function for computing hashes from query results.

    Args:
        row: SQLite Row object with entity columns

    Returns:
        Hex-encoded SHA256 hash string
    """
    return compute_entity_hash(
        entity_type=row["type"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        group_id=row.get("group_id"),
        derived_from=row.get("derived_from"),
        superseded_by=row.get("superseded_by"),
        superseded_at=row.get("superseded_at"),
        previous_hash=row.get("previous_hash"),
    )


def compute_next_hash(
    entity_type: str,
    created_at: str,
    updated_at: str,
    current_hash: str,
    group_id: str | None = None,
    derived_from: str | None = None,
    superseded_by: str | None = None,
    superseded_at: str | None = None,
) -> str:
    """Compute the next hash in the chain after an update.

    Args:
        entity_type: The type of entity
        created_at: ISO 8601 timestamp (unchanged from original)
        updated_at: NEW ISO 8601 timestamp
        current_hash: The current entity hash (becomes previous_hash)
        group_id: Optional group ID
        derived_from: Optional derived_from reference
        superseded_by: Optional superseded_by reference
        superseded_at: Optional superseded_at timestamp

    Returns:
        Hex-encoded SHA256 hash string for the new state
    """
    return compute_entity_hash(
        entity_type=entity_type,
        created_at=created_at,
        updated_at=updated_at,
        group_id=group_id,
        derived_from=derived_from,
        superseded_by=superseded_by,
        superseded_at=superseded_at,
        previous_hash=current_hash,  # Current hash becomes previous
    )
