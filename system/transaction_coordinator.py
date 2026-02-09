"""Cross-database transaction coordinator (RFC-008 v1.2).

This module provides coordination for operations that span both Soil and Core
databases, ensuring best-effort atomicity and proper failure handling.

ARCHITECTURE:
- Uses EXCLUSIVE locks on both databases for cross-DB operations
- Commits Soil first (source of truth), then Core
- Detects inconsistency when Soil commits but Core fails
- Startup checks identify orphaned EntityDeltas and broken hash chains

TRANSACTION SEMANTICS (RFC-008):
- Single-DB operations: Standard SQLite ACID
- Cross-DB operations: Best-effort atomicity with app-level coordination
- Split operations: Item commits independently, relation retries on failure

SYSTEM STATUS MODES:
- NORMAL: No issues detected
- INCONSISTENT: Orphaned deltas detected (Soil committed, Core did not)
- READ_ONLY: Database opened read-only for maintenance
- SAFE_MODE: Database corruption detected

USAGE:
    # Cross-database operation with automatic coordination
    coordinator = TransactionCoordinator()
    with coordinator.cross_database_transaction() as (soil, core):
        # Perform operations on both databases
        soil.create_item(item)
        core.entity.create("transactions", ...)
        # Commits Soil first, then Core on successful exit

    # Check system status on startup
    coordinator = TransactionCoordinator()
    status = coordinator.check_consistency()
    if status != SystemStatus.NORMAL:
        # Handle inconsistency
        pass
"""

from __future__ import annotations

import json
import sqlite3
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from .exceptions import ConsistencyError

if TYPE_CHECKING:
    from .core import Core
    from .soil.database import Soil


class SystemStatus(Enum):
    """System status modes (RFC-008)."""

    NORMAL = "normal"
    INCONSISTENT = "inconsistent"
    READ_ONLY = "read_only"
    SAFE_MODE = "safe_mode"


class TransactionCoordinator:
    """
    Coordinates transactions across Soil and Core databases (RFC-008).

    Provides best-effort atomicity for cross-database operations:
    - EXCLUSIVE locking on both databases
    - Commit ordering: Soil first, then Core
    - Inconsistency detection and recovery tools

    Attributes:
        soil_db_path: Path to Soil database file
        core_db_path: Path to Core database file
    """

    def __init__(self, soil_db_path: Path | str | None = None, core_db_path: Path | str | None = None):
        """Initialize transaction coordinator.

        Args:
            soil_db_path: Path to Soil database. If None, uses get_db_path('soil')
            core_db_path: Path to Core database. If None, uses get_db_path('core')
        """
        from system.host.environment import get_db_path

        self.soil_db_path = Path(soil_db_path) if soil_db_path else get_db_path('soil')
        self.core_db_path = Path(core_db_path) if core_db_path else get_db_path('core')

    def check_consistency(self) -> SystemStatus:
        """
        Check database consistency on startup (RFC-008 INV-TX-018 to INV-TX-020).

        Checks for:
        - Orphaned EntityDeltas (Soil committed, Core did not)
        - Broken hash chains (previous_hash doesn't match)

        Returns:
            SystemStatus indicating the health of the system

        Note:
            System starts regardless of state (always-available startup)
        """
        issues = []

        # Check for orphaned EntityDeltas
        orphans = self._find_orphaned_deltas()
        if orphans:
            issues.append(f"Found {len(orphans)} orphaned EntityDeltas")

        # Check for broken hash chains
        broken_chains = self._find_broken_hash_chains()
        if broken_chains:
            issues.append(f"Found {len(broken_chains)} broken hash chains")

        if issues:
            # Log issues but still return the status
            # (RFC-008 INV-TX-020: System starts regardless of state)
            for issue in issues:
                print(f"[TransactionCoordinator] WARNING: {issue}")

            if broken_chains:
                # Database corruption detected
                return SystemStatus.SAFE_MODE
            return SystemStatus.INCONSISTENT

        return SystemStatus.NORMAL

    def _find_orphaned_deltas(self) -> list[dict]:
        """
        Find EntityDelta items with no matching entity in Core.

        Orphaned EntityDeltas occur when:
        - Soil commits (Item created)
        - Core fails to commit (Entity not updated)
        - Process killed between commits

        Returns:
            List of orphaned delta dictionaries with uuid, realized_at, entity_id
        """
        from .soil.database import get_soil

        orphans = []

        with get_soil(self.soil_db_path) as soil:
            # Find all EntityDelta items
            # NOTE: Direct _get_connection() access needed for consistency checking
            # This is infrastructure code that operates at the connection level
            # TODO: Consider adding public Soil.get_items_by_type() method to avoid private access
            conn = soil._get_connection()
            cursor = conn.execute(
                """SELECT uuid, realized_at, data
                   FROM item
                   WHERE _type = 'EntityDelta'
                   AND superseded_by IS NULL"""
            )

            for row in cursor.fetchall():
                data = json.loads(row["data"])
                entity_id = data.get("entity_id")

                if entity_id:
                    # Check if entity exists in Core
                    if not self._entity_exists_in_core(entity_id):
                        orphans.append({
                            "uuid": row["uuid"],
                            "realized_at": row["realized_at"],
                            "entity_id": entity_id,
                        })

        return orphans

    def _entity_exists_in_core(self, entity_id: str) -> bool:
        """Check if entity exists in Core database.

        Args:
            entity_id: Entity UUID (with or without core_ prefix)

        Returns:
            True if entity exists, False otherwise
        """
        # Strip prefix (entities stored without prefix)
        from .utils import uid
        entity_id = uid.strip_prefix(entity_id)

        with sqlite3.connect(str(self.core_db_path)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT 1 FROM entity WHERE uuid = ?",
                (entity_id,)
            )
            return cursor.fetchone() is not None

    def _find_broken_hash_chains(self) -> list[dict]:
        """
        Find entities with broken hash chains.

        A broken chain occurs when entity.previous_hash doesn't match
        the hash of the entity it references.

        Returns:
            List of broken chain dictionaries with uuid, previous_hash, expected_hash
        """
        broken = []

        with sqlite3.connect(str(self.core_db_path)) as conn:
            conn.row_factory = sqlite3.Row

            # Find entities with non-NULL previous_hash
            cursor = conn.execute(
                """SELECT uuid, previous_hash, hash, type, data, created_at
                   FROM entity
                   WHERE previous_hash IS NOT NULL"""
            )

            for row in cursor.fetchall():
                # Find the entity that should have this hash
                prev_cursor = conn.execute(
                    "SELECT uuid, hash FROM entity WHERE hash = ?",
                    (row["previous_hash"],)
                )
                prev_entity = prev_cursor.fetchone()

                if prev_entity is None:
                    # Previous entity not found - broken chain
                    broken.append({
                        "uuid": row["uuid"],
                        "previous_hash": row["previous_hash"],
                        "issue": "previous_hash not found in any entity",
                    })

        return broken

    # ==========================================================================
    # CROSS-DATABASE TRANSACTION CONTEXT
    # ==========================================================================

    class CrossDatabaseTransaction:
        """
        Context manager for coordinated cross-database transactions.

        Ensures best-effort atomicity across Soil and Core:
        - EXCLUSIVE locks on both databases
        - Commit Soil first, then Core
        - Rollback both on exception
        - Detect inconsistency if Soil commits but Core fails
        """

        def __init__(self, coordinator: "TransactionCoordinator"):
            """Initialize cross-database transaction.

            Args:
                coordinator: Parent TransactionCoordinator instance
            """
            self._coordinator = coordinator
            self._soil: Soil | None = None
            self._core: Core | None = None
            self._soil_conn: sqlite3.Connection | None = None
            self._core_conn: sqlite3.Connection | None = None
            self._soil_committed = False

        def __enter__(self) -> tuple["Soil", "Core"]:
            """Begin cross-database transaction with EXCLUSIVE locks.

            Returns:
                Tuple of (soil, core) instances

            Raises:
                RuntimeError: If database initialization fails
            """
            from .core import get_core
            from .soil.database import get_soil

            # Create Soil instance
            self._soil = get_soil(self._coordinator.soil_db_path)
            self._soil.__enter__()

            # Create Core instance
            self._core = get_core()
            self._core.__enter__()

            # Get raw connections for transaction coordination
            # NOTE: Direct _get_connection()/_get_conn() access is intentional here
            # This is coordination-layer infrastructure that needs EXCLUSIVE lock control
            # There is no public API for "begin EXCLUSIVE transaction and get connection"
            # This is analogous to Core's internal operations that access private connections
            self._soil_conn = self._soil._get_connection()
            self._core_conn = self._core._get_conn()

            # Begin EXCLUSIVE transactions on both databases
            # (RFC-008 INV-TX-004: SERIALIZABLE via BEGIN EXCLUSIVE)
            self._soil_conn.execute("BEGIN EXCLUSIVE")
            self._core_conn.execute("BEGIN EXCLUSIVE")

            return self._soil, self._core

        def __exit__(self, exc_type, exc_val, exc_tb):
            """Commit or rollback coordinated transaction.

            Commit ordering: Soil first (source of truth), then Core (RFC-008 INV-TX-007).

            Args:
                exc_type: Exception type if exception occurred, else None
                exc_val: Exception value if exception occurred, else None
                exc_tb: Exception traceback if exception occurred, else None

            Returns:
                False to propagate exceptions
            """
            try:
                if exc_type is None:
                    # No exception - attempt coordinated commit
                    self._commit_coordinated()
                else:
                    # Exception occurred - rollback both
                    self._rollback_both()
            finally:
                # Always close connections
                self._soil.__exit__(None, None, None)
                self._core.__exit__(None, None, None)

            return False  # Propagate exceptions

        def _commit_coordinated(self):
            """
            Commit transaction with Soil-first ordering.

            Commit sequence (RFC-008 INV-TX-007):
            1. Commit Soil (source of truth for Items and EntityDeltas)
            2. Commit Core (entity registry)

            If Core fails after Soil commits → INCONSISTENT state
            """
            # Commit Soil first
            if self._soil_conn:
                try:
                    self._soil_conn.commit()
                    self._soil_committed = True
                except Exception as e:
                    # Soil commit failed - rollback both
                    self._rollback_both()
                    raise RuntimeError(f"Soil commit failed: {e}") from e

            # Commit Core second
            if self._core_conn:
                try:
                    self._core_conn.commit()
                except Exception as e:
                    # Core commit failed after Soil committed → INCONSISTENT
                    # (RFC-008 INV-TX-008)
                    print(f"[TransactionCoordinator] CRITICAL: Soil committed but Core failed: {e}")
                    print("[TransactionCoordinator] System is now INCONSISTENT")
                    # Attempt to rollback Core (no-op if already failed)
                    try:
                        self._core_conn.rollback()
                    except Exception:
                        pass
                    raise ConsistencyError(
                        "Soil committed but Core failed - system INCONSISTENT",
                        details={"soil_committed": True, "core_error": str(e)}
                    ) from e

        def _rollback_both(self):
            """
            Rollback both databases (best-effort).

            If one database already committed, rollback is no-op on that DB.
            (RFC-008 INV-TX-010: Best-effort rollback)
            """
            if self._soil_conn:
                try:
                    self._soil_conn.rollback()
                except Exception as e:
                    print(f"[TransactionCoordinator] Warning: Soil rollback failed: {e}")

            if self._core_conn:
                try:
                    self._core_conn.rollback()
                except Exception as e:
                    print(f"[TransactionCoordinator] Warning: Core rollback failed: {e}")

    def cross_database_transaction(self) -> "CrossDatabaseTransaction":
        """
        Create a cross-database transaction context manager.

        Usage:
            coordinator = TransactionCoordinator()
            with coordinator.cross_database_transaction() as (soil, core):
                # Perform operations on both databases
                soil.create_item(item)
                core.entity.create("transactions", ...)
                # Commits Soil first, then Core on successful exit

        Returns:
            CrossDatabaseTransaction context manager

        Raises:
            ConsistencyError: If Soil commits but Core fails
        """
        return self.CrossDatabaseTransaction(self)


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_transaction_coordinator() -> TransactionCoordinator:
    """
    Get a transaction coordinator instance.

    Database paths are resolved via RFC-004 environment variables.

    Returns:
        TransactionCoordinator instance
    """
    return TransactionCoordinator()
