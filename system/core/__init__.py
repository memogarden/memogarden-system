"""Database module for MemoGarden Core.

This module provides the Core API for database operations.
Core encapsulates connection management and provides access to entity operations.

ARCHITECTURE:
- Core owns its connection (no Flask g.db dependency)
- Connection closes on context exit (atomic=True) or after operation (atomic=False)
- Each entity type gets an encapsulated class with related operations

COORDINATION PATTERN:
Operations classes that depend on entity registry receive a Core reference
to enable automatic coordination. For example, TransactionOperations receives
Core so it can automatically create entity registry entries:

    # High-level API (Core usage):
    core.transaction.create(amount=100, ...)  # Entity created automatically

    # Low-level API (standalone usage):
    entity_id = core.entity.create("transactions")
    ops = TransactionOperations(conn)
    ops.create(entity_id, amount=100, ...)  # Requires explicit entity_id

This design preserves composition (no inheritance) while providing a clean
high-level API that encapsulates implementation details.

ID GENERATION POLICY:
All entity IDs are auto-generated UUIDs. This design choice:
1. Prevents users from accidentally passing invalid or duplicate IDs
2. Encapsulates ID generation logic within the database layer
3. Ensures UUID v4 format compliance with collision retry
4. Simplifies the API - users don't need to manage ID creation
5. Both entity.create() and transaction.create() always generate new IDs
"""

import sqlite3
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING

from system.config import default_settings as settings

# Thread-local storage for atomic Core
_core_context: ContextVar["Core"] = ContextVar("_core_context", default=None)

if TYPE_CHECKING:
    from .entity import EntityOperations
    from .recurrence import RecurrenceOperations
    from .transaction import TransactionOperations


class Core:
    """
    Database Core with entity operations.

    Maintains its own connection and transaction state.
    Provides access to entity operations through properties.

    Connection Lifecycle:
    - atomic=True: Connection closes on __exit__ from context manager
    - atomic=False: Connection closes after each operation (autocommit)
    """

    def __init__(self, connection: sqlite3.Connection, atomic: bool = False):
        """Initialize Core with a database connection.

        Args:
            connection: SQLite connection with row_factory set to sqlite3.Row
            atomic: If True, Core MUST be used as context manager.
                    If False, Core has autocommit semantics.
        """
        self._conn = connection
        self._atomic = atomic
        self._entity_ops = None
        self._transaction_ops = None
        self._recurrence_ops = None

    @property
    def entity(self) -> "EntityOperations":
        """Entity registry operations.

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.
        """
        if self._entity_ops is None:
            from .entity import EntityOperations
            self._entity_ops = EntityOperations(self._conn)
        return self._entity_ops

    @property
    def transaction(self) -> "TransactionOperations":
        """Transaction operations.

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.

        Note: Passes self (Core) to TransactionOperations so it can
        coordinate entity registry operations automatically.
        """
        if self._transaction_ops is None:
            from .transaction import TransactionOperations
            # Pass self (Core) for entity coordination
            self._transaction_ops = TransactionOperations(self._conn, core=self)
        return self._transaction_ops

    @property
    def recurrence(self) -> "RecurrenceOperations":
        """Recurrence operations.

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.

        Note: Passes self (Core) to RecurrenceOperations so it can
        coordinate entity registry operations automatically.
        """
        if self._recurrence_ops is None:
            from .recurrence import RecurrenceOperations
            # Pass self (Core) for entity coordination
            self._recurrence_ops = RecurrenceOperations(self._conn, core=self)
        return self._recurrence_ops

    def __enter__(self) -> "Core":
        """Enter context manager for atomic transaction.

        Raises:
            RuntimeError: If Core was not created with atomic=True

        Returns:
            self for use in with-statement
        """
        if not self._atomic:
            raise RuntimeError(
                "Core must be created with atomic=True for context manager use. "
                "Use: with db.get_core(atomic=True) as core:"
            )
        _core_context.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager, committing or rolling back transaction.

        Args:
            exc_type: Exception type if exception occurred, else None
            exc_val: Exception value if exception occurred, else None
            exc_tb: Exception traceback if exception occurred, else None
        """
        try:
            if exc_type is None:
                # No exception - commit the transaction
                self._conn.commit()
            else:
                # Exception occurred - rollback the transaction
                self._conn.rollback()
        finally:
            # Always clear context and close connection
            _core_context.set(None)
            self._conn.close()

    def __del__(self):
        """Cleanup connection if not already closed.

        Called during garbage collection. Ignores errors since connection
        may already be closed or in an invalid state.
        """
        if hasattr(self, "_conn") and self._conn:
            try:
                self._conn.close()
            except Exception:
                # Ignore errors during garbage collection
                # Connection may already be closed or invalid
                pass


def _create_connection() -> sqlite3.Connection:
    """Create a fresh database connection.

    Returns:
        SQLite connection with row_factory set to sqlite3.Row,
        foreign keys enabled, and WAL mode for concurrent access.

    Note:
        WAL (Write-Ahead Logging) mode allows better concurrent access
        by enabling readers to proceed without blocking writers.
    """
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_core(atomic: bool = False) -> Core:
    """
    Get a database Core instance.

    Args:
        atomic: If True, returns a Core that MUST be used as context manager.
                Use for multi-operation transactions that need to commit together.
                If False (default), returns a Core with autocommit semantics.
                Each operation commits independently, connection closes after operation.

    Returns:
        Core instance with entity/transaction operations

    Examples:
        Autocommit mode (single operation):
        >>> core = get_core()
        >>> transaction = core.transaction.get_by_id(uuid)
        >>> # Connection closes automatically

        Atomic mode (multi-operation transaction):
        >>> with get_core(atomic=True) as core:
        ...     uuid = core.entity.create("transactions")
        ...     core.transaction.create(uuid, amount=100, ...)
        ...     core.entity.supersede(old_id, uuid)
        ...     # All operations commit together on exit
    """
    conn = _create_connection()
    return Core(conn, atomic=atomic)


# ============================================================================
# DATABASE INITIALIZATION
# ============================================================================

EXPECTED_SCHEMA_VERSION = "20260130"


def _get_current_schema_version(db: sqlite3.Connection) -> str | None:
    """Get the current schema version from the database.

    Args:
        db: Database connection

    Returns:
        Current schema version string (e.g., "20251229") or None if not set
    """
    cursor = db.execute(
        "SELECT value FROM _schema_metadata WHERE key = 'version'"
    )
    row = cursor.fetchone()
    return row[0] if row else None


def _apply_migration(db: sqlite3.Connection, from_version: str, to_version: str) -> None:
    """Apply a migration from one schema version to another.

    Args:
        db: Database connection
        from_version: Current schema version
        to_version: Target schema version

    Raises:
        FileNotFoundError: If migration file doesn't exist
        RuntimeError: If migration fails
    """
    migration_file = (
        Path(__file__).parent.parent / "schemas" / "sql" / "migrations" /
        f"migrate_{from_version}_to_{to_version}.sql"
    )

    if not migration_file.exists():
        raise RuntimeError(
            f"Migration from {from_version} to {to_version} required but file not found: {migration_file}"
        )

    with open(migration_file, "r") as f:
        migration_sql = f.read()

    try:
        db.executescript(migration_sql)
        db.commit()
    except Exception as e:
        db.rollback()
        raise RuntimeError(f"Migration from {from_version} to {to_version} failed: {e}") from e


def _run_migrations(db: sqlite3.Connection) -> None:
    """Check schema version and apply migrations if needed.

    Args:
        db: Database connection (should be in a transaction)

    Note:
        This is a simple migration runner for development. For production,
        consider implementing the full migration mechanism described in
        plan/future/migration-mechanism.md
    """
    current_version = _get_current_schema_version(db)

    if current_version is None:
        # No version set - this should not happen in a properly initialized DB
        raise RuntimeError("Database has _schema_metadata table but no version set")

    if current_version == EXPECTED_SCHEMA_VERSION:
        # Already at expected version, no migration needed
        return

    # Direct migration paths
    if current_version == "20251223" and EXPECTED_SCHEMA_VERSION == "20251229":
        _apply_migration(db, current_version, EXPECTED_SCHEMA_VERSION)
    elif current_version == "20251229" and EXPECTED_SCHEMA_VERSION == "20251230":
        _apply_migration(db, current_version, EXPECTED_SCHEMA_VERSION)
    elif current_version == "20251230" and EXPECTED_SCHEMA_VERSION == "20260129":
        _apply_migration(db, current_version, EXPECTED_SCHEMA_VERSION)
    elif current_version == "20260129" and EXPECTED_SCHEMA_VERSION == "20260130":
        _apply_migration(db, current_version, EXPECTED_SCHEMA_VERSION)
    elif current_version < EXPECTED_SCHEMA_VERSION:
        # Skip migrations for older versions (would need multi-step migration in production)
        # This allows development to continue without full migration support
        pass
    elif current_version > EXPECTED_SCHEMA_VERSION:
        # Database is newer than code - this is OK (forward compatible)
        pass


def init_db():
    """Initialize database by running schema.sql if not already initialized.

    Also checks schema version and applies migrations if the database exists
    but is at an older schema version.
    """
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(db_path)) as db:
        # Check if database is already initialized
        cursor = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_metadata'"
        )
        if cursor.fetchone():
            # Database exists - check if migration is needed
            _run_migrations(db)
            return

        # Fresh database - apply current schema
        schema_path = Path(__file__).parent.parent / "schemas" / "sql" / "core.sql"
        if schema_path.exists():
            with open(schema_path, "r") as f:
                schema_sql = f.read()
            db.executescript(schema_sql)
            db.commit()
        else:
            raise RuntimeError(
                f"Schema file not found: {schema_path}\n"
                "Ensure schemas are bundled in system/schemas/sql/"
            )
