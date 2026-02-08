"""UserRelation operations with time horizon tracking (RFC-002).

IMPORT CONVENTION:
- Core accesses these through core.relation property
- Receives Core reference for coordinating with other operations

TIME HORIZON MECHANISM (RFC-002):
- User relations track engagement signals with time_horizon field
- On access: time_horizon += delta * SAFETY_COEFFICIENT
- Relation is alive when: time_horizon >= current_day()
- Fact significance = max(inbound_user_relations.time_horizon)

SCHEMA (v20260130):
- Uses user_relation table in Core DB
- time_horizon and last_access_at stored as days since epoch (2020-01-01)
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..utils import uid
from ..utils.time import current_day

if TYPE_CHECKING:
    from . import Core

# Configuration (RFC-002)
SAFETY_COEFFICIENT = 1.2  # Margin for irregular access patterns

# User relation kinds (RFC-002 v5)
USER_RELATION_KINDS = {
    'explicit_link',  # Operator-created connection
}


@dataclass
class UserRelation:
    """User relation (engagement signal with time horizon).

    Per RFC-002 v5, user relations encode engagement/attention signals
    that naturally decay over time based on access patterns.
    """
    uuid: str  # core_ prefix (becomes soil_ on fossilization)
    kind: str  # One of USER_RELATION_KINDS
    source: str  # UUID of source
    source_type: str  # 'item' | 'entity' | 'artifact'
    target: str  # UUID of target
    target_type: str  # 'item' | 'entity' | 'artifact' | 'fragment'
    time_horizon: int  # Future timestamp (days since epoch)
    last_access_at: int  # Timestamp of most recent access (days since epoch)
    created_at: int  # Days since epoch
    evidence: dict | None = None
    metadata: dict | None = None


class RelationOperations:
    """User relation operations with time horizon tracking.

    Per RFC-002 v5, user relations track engagement signals through
    time_horizon computation. Access patterns determine relation
    longevity through the safety coefficient mechanism.
    """

    def __init__(self, conn: sqlite3.Connection, core: "Core | None" = None):
        """Initialize relation operations.

        Args:
            conn: SQLite connection with row_factory set to sqlite3.Row
            core: Core reference for future coordination (unused currently)
        """
        self._conn = conn
        self._core = core

    def create(
        self,
        kind: str,
        source: str,
        source_type: str,
        target: str,
        target_type: str,
        initial_horizon_days: int = 7,
        evidence: dict | None = None,
        metadata: dict | None = None,
    ) -> str:
        """Create a user relation with initial time horizon.

        Args:
            kind: Relation kind (one of USER_RELATION_KINDS)
            source: UUID of source (prefixed or non-prefixed)
            source_type: Type of source ('item' | 'entity' | 'artifact')
            target: UUID of target (prefixed or non-prefixed)
            target_type: Type of target ('item' | 'entity' | 'artifact' | 'fragment')
            initial_horizon_days: Initial time horizon in days (default: 7)
            evidence: Optional evidence for the relation
            metadata: Optional metadata for the relation

        Returns:
            UUID of the created relation (with core_ prefix)

        Raises:
            ValueError: If kind is not in USER_RELATION_KINDS
        """
        if kind not in USER_RELATION_KINDS:
            raise ValueError(f"Invalid relation kind: {kind}. Must be one of {USER_RELATION_KINDS}")

        # Strip prefixes for storage
        source = uid.strip_prefix(source)
        target = uid.strip_prefix(target)

        # Generate UUID (without prefix for storage) and compute timestamps
        plain_uuid = uid.generate_uuid()
        today = current_day()
        time_horizon = today + initial_horizon_days

        # Create return value with prefix (for API response)
        relation_uuid = uid.add_core_prefix(plain_uuid)

        # Serialize evidence and metadata to JSON for storage
        evidence_json = json.dumps(evidence) if evidence else None
        metadata_json = json.dumps(metadata) if metadata else None

        self._conn.execute(
            """INSERT INTO user_relation (
                uuid, kind, source, source_type, target, target_type,
                time_horizon, last_access_at, created_at, evidence, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                plain_uuid,
                kind,
                source,
                source_type,
                target,
                target_type,
                time_horizon,
                today,
                today,
                evidence_json,
                metadata_json,
            ),
        )
        self._conn.commit()

        return relation_uuid

    def get_by_id(self, relation_id: str) -> sqlite3.Row:
        """Get user relation by ID.

        Args:
            relation_id: The UUID of the relation (plain or with core_ prefix)

        Returns:
            sqlite3.Row with relation data

        Raises:
            ResourceNotFound: If relation_id doesn't exist
        """
        from ..exceptions import ResourceNotFound

        # Strip prefix if provided
        relation_id = uid.strip_prefix(relation_id)

        row = self._conn.execute(
            """SELECT * FROM user_relation WHERE uuid = ?""",
            (relation_id,)
        ).fetchone()

        if not row:
            raise ResourceNotFound(f"User relation not found: {relation_id}")

        return row

    def update_time_horizon(self, relation_id: str) -> None:
        """Update time horizon on relation access (RFC-002).

        Applies the SAFETY_COEFFICIENT mechanism:
            time_horizon += delta * SAFETY_COEFFICIENT
            last_access_at = current_day()

        Args:
            relation_id: The UUID of the relation (plain or with core_ prefix)

        Raises:
            ResourceNotFound: If relation_id doesn't exist
        """
        from ..exceptions import ResourceNotFound

        # Strip prefix if provided
        relation_id = uid.strip_prefix(relation_id)

        # Get current state
        row = self.get_by_id(relation_id)
        last_access = row["last_access_at"]
        today = current_day()

        # Compute delta and apply safety coefficient
        delta = today - last_access
        horizon_increase = int(delta * SAFETY_COEFFICIENT)
        new_horizon = row["time_horizon"] + horizon_increase

        # Update relation
        cursor = self._conn.execute(
            """UPDATE user_relation
               SET time_horizon = ?, last_access_at = ?
               WHERE uuid = ?""",
            (new_horizon, today, relation_id)
        )
        self._conn.commit()

        if cursor.rowcount == 0:
            raise ResourceNotFound(f"User relation not found: {relation_id}")

    def list_inbound(
        self,
        target: str,
        alive_only: bool = True,
    ) -> list[sqlite3.Row]:
        """List inbound user relations for a target.

        Args:
            target: UUID of target (prefixed or non-prefixed)
            alive_only: If True, only return relations where time_horizon >= current_day()

        Returns:
            List of sqlite3.Row with relation data

        Examples:
            # Get all active relations pointing to this entity
            relations = core.relation.list_inbound(entity_id)

            # Get all relations including expired ones
            all_relations = core.relation.list_inbound(entity_id, alive_only=False)
        """
        # Strip prefix if provided
        target = uid.strip_prefix(target)

        if alive_only:
            today = current_day()
            rows = self._conn.execute(
                """SELECT * FROM user_relation
                   WHERE target = ? AND time_horizon >= ?
                   ORDER BY time_horizon DESC""",
                (target, today)
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM user_relation
                   WHERE target = ?
                   ORDER BY time_horizon DESC""",
                (target,)
            ).fetchall()

        return list(rows)

    def list_outbound(
        self,
        source: str,
        alive_only: bool = True,
    ) -> list[sqlite3.Row]:
        """List outbound user relations from a source.

        Args:
            source: UUID of source (prefixed or non-prefixed)
            alive_only: If True, only return relations where time_horizon >= current_day()

        Returns:
            List of sqlite3.Row with relation data

        Examples:
            # Get all active relations from this entity
            relations = core.relation.list_outbound(entity_id)
        """
        # Strip prefix if provided
        source = uid.strip_prefix(source)

        if alive_only:
            today = current_day()
            rows = self._conn.execute(
                """SELECT * FROM user_relation
                   WHERE source = ? AND time_horizon >= ?
                   ORDER BY time_horizon DESC""",
                (source, today)
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM user_relation
                   WHERE source = ?
                   ORDER BY time_horizon DESC""",
                (source,)
            ).fetchall()

        return list(rows)

    def expire(self, relation_id: str) -> None:
        """Mark relation for fossilization by setting time_horizon to past.

        This is typically called during fossilization sweeps to mark
        relations that should be moved from Core to Soil.

        Args:
            relation_id: The UUID of the relation (plain or with core_ prefix)

        Note:
            This doesn't delete the relation immediately. The fossilization
            sweep will move expired relations to Soil.
        """
        from ..exceptions import ResourceNotFound

        # Strip prefix if provided
        relation_id = uid.strip_prefix(relation_id)

        # Set time_horizon to yesterday (ensures it's expired)
        yesterday = current_day() - 1

        cursor = self._conn.execute(
            """UPDATE user_relation
               SET time_horizon = ?
               WHERE uuid = ?""",
            (yesterday, relation_id)
        )
        self._conn.commit()

        if cursor.rowcount == 0:
            raise ResourceNotFound(f"User relation not found: {relation_id}")

    def fact_time_horizon(self, fact_uuid: str) -> int | None:
        """Compute fact significance from inbound user relations.

        Per RFC-002, fact significance = max(inbound_user_relations.time_horizon).
        Returns None if no user relations (orphaned fact).

        Args:
            fact_uuid: UUID of the fact/entity (prefixed or non-prefixed)

        Returns:
            Maximum time_horizon from inbound user relations, or None if orphaned

        Examples:
            # Check if fact is still relevant
            horizon = core.relation.fact_time_horizon(item_id)
            if horizon is None:
                print("Orphaned fact - no user relations")
            elif horizon < current_day():
                print("Fact should fossilize")
            else:
                print(f"Fact alive until day {horizon}")
        """
        # Strip prefix if provided
        fact_uuid = uid.strip_prefix(fact_uuid)

        row = self._conn.execute(
            """SELECT MAX(time_horizon) as max_horizon
               FROM user_relation
               WHERE target = ?""",
            (fact_uuid,)
        ).fetchone()

        if row and row["max_horizon"] is not None:
            return row["max_horizon"]
        return None

    def is_alive(self, relation_id: str) -> bool:
        """Check if relation is still alive.

        Args:
            relation_id: The UUID of the relation (plain or with core_ prefix)

        Returns:
            True if time_horizon >= current_day(), False otherwise
        """
        row = self.get_by_id(relation_id)
        return row["time_horizon"] >= current_day()
