"""Database module for MemoGarden Core.

This module provides the Core API for database operations.
Core encapsulates connection management and provides access to entity operations.

ARCHITECTURE:
- Core owns its connection (no Flask g.db dependency)
- Core MUST be used as context manager (enforced at runtime)
- Connection closes on context exit (commit or rollback based on exception)
- Each entity type gets an encapsulated class with related operations

COORDINATION PATTERN:
Operations classes that depend on entity registry receive a Core reference
to enable automatic coordination. For example, TransactionOperations receives
Core so it can automatically create entity registry entries:

    # High-level API (Core usage):
    with get_core() as core:
        core.transaction.create(amount=100, ...)  # Entity created automatically
        # All operations commit together on exit

    # Low-level API (standalone usage):
    with get_core() as core:
        entity_id = core.entity.create("transactions")
        # Operations commit together on exit

This design preserves composition (no inheritance) while providing a clean
high-level API that encapsulates implementation details.

ID GENERATION POLICY:
All entity IDs are auto-generated UUIDs. This design choice:
1. Prevents users from accidentally passing invalid or duplicate IDs
2. Encapsulates ID generation logic within the database layer
3. Ensures UUID v4 format compliance with collision retry
4. Simplifies the API - users don't need to manage ID creation
5. Both entity.create() and transaction.create() always generate new IDs

CONNECTION LIFECYCLE (Session 6.5 Refactor):
- Core enforces context manager usage via _in_context flag
- Operations call core._get_conn() which raises RuntimeError if not in context
- __enter__ sets _in_context=True, __exit__ sets _in_context=False
- __exit__ commits on success, rollbacks on exception, always closes connection
"""

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from system.config import default_settings as settings

if TYPE_CHECKING:
    from .entity import EntityOperations
    from .recurrence import RecurrenceOperations
    from .relation import RelationOperations
    from .transaction import TransactionOperations
    from .context import ContextOperations
    from .artifact import ArtifactOperations
    from .conversation import ConversationOperations


class Core:
    """
    Database Core with entity operations.

    Maintains its own connection and transaction state.
    Provides access to entity operations through properties.

    Connection Lifecycle:
    - Core MUST be used as context manager (enforced at runtime)
    - __enter__: Marks Core as active, returns self
    - __exit__: Commits on success, rollbacks on exception, always closes
    """

    def __init__(self, connection: sqlite3.Connection):
        """Initialize Core with a database connection.

        Args:
            connection: SQLite connection with row_factory set to sqlite3.Row

        Note:
            Core must be used as context manager. Operations will raise
            RuntimeError if called outside of 'with' statement.
        """
        self._conn = connection
        self._in_context = False  # Track if we're inside a context manager
        self._entity_ops = None
        self._transaction_ops = None
        self._recurrence_ops = None
        self._relation_ops = None
        self._context_ops = None
        self._artifact_ops = None
        self._conversation_ops = None

    @property
    def entity(self) -> "EntityOperations":
        """Entity registry operations.

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.
        """
        if self._entity_ops is None:
            from .entity import EntityOperations
            self._entity_ops = EntityOperations(self)
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
            self._transaction_ops = TransactionOperations(self)
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
            self._recurrence_ops = RecurrenceOperations(self)
        return self._recurrence_ops

    @property
    def relation(self) -> "RelationOperations":
        """User relation operations with time horizon tracking (RFC-002).

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.
        """
        if self._relation_ops is None:
            from .relation import RelationOperations
            self._relation_ops = RelationOperations(self)
        return self._relation_ops

    @property
    def context(self) -> "ContextOperations":
        """Context frame and view stream operations (RFC-003).

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.

        Note: Passes self (Core) to ContextOperations so it can
        coordinate entity registry operations automatically.
        """
        if self._context_ops is None:
            from .context import ContextOperations
            self._context_ops = ContextOperations(self)
        return self._context_ops

    @property
    def artifact(self) -> "ArtifactOperations":
        """Artifact delta operations for Project Studio.

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.

        Note: Passes self (Core) to ArtifactOperations so it can
        coordinate entity registry operations automatically.
        """
        if self._artifact_ops is None:
            from .artifact import ArtifactOperations
            self._artifact_ops = ArtifactOperations(self)
        return self._artifact_ops

    @property
    def conversation(self) -> "ConversationOperations":
        """Conversation operations for Project Studio.

        Lazy-loaded to avoid circular import issues.
        Operations are created on first access and cached.

        Note: Passes self (Core) to ConversationOperations so it can
        coordinate entity registry operations automatically.
        """
        if self._conversation_ops is None:
            from .conversation import ConversationOperations
            self._conversation_ops = ConversationOperations(self)
        return self._conversation_ops

    def has_admin_user(self) -> bool:
        """Check if any admin users exist in the database.

        Returns:
            True if at least one admin user exists, False otherwise
        """
        cursor = self._get_conn().execute(
            "SELECT COUNT(*) as count FROM users WHERE is_admin = 1"
        )
        row = cursor.fetchone()
        return row["count"] > 0

    def _get_conn(self) -> sqlite3.Connection:
        """Get connection, enforcing context manager usage.

        Returns:
            SQLite connection

        Raises:
            RuntimeError: If Core is not being used as context manager
        """
        if not self._in_context:
            raise RuntimeError(
                "Core must be used as context manager. "
                "Use: with get_core() as core: ..."
            )
        return self._conn

    def __enter__(self) -> "Core":
        """Enter context manager for transaction.

        Returns:
            self for use in with-statement
        """
        self._in_context = True
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
            if exc_type is None:
                # No exception - commit the transaction
                self._conn.commit()
            else:
                # Exception occurred - rollback the transaction
                self._conn.rollback()
        finally:
            # Always close connection
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

        Database path is resolved via RFC-004:
        - settings.database_path if provided (explicit path, backward compatible)
        - Otherwise get_db_path('core') using environment variables
    """
    # Resolve database path (RFC-004)
    if settings.database_path is None:
        from system.host.environment import get_db_path
        db_path = get_db_path('core')
    else:
        db_path = Path(settings.database_path)

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_core() -> Core:
    """
    Get a database Core instance.

    Returns:
        Core instance with entity/transaction operations

    Examples:
        Use Core as context manager for transactional operations:
        >>> with get_core() as core:
        ...     uuid = core.entity.create("transactions")
        ...     core.transaction.create(uuid, amount=100, ...)
        ...     core.entity.supersede(old_id, uuid)
        ...     # All operations commit together on exit

    Note:
        Core MUST be used as context manager. Operations will raise
        RuntimeError if called outside of 'with' statement.
    """
    conn = _create_connection()
    return Core(conn)


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

    Database path is resolved via RFC-004:
    - settings.database_path if provided (explicit path, backward compatible)
    - Otherwise get_db_path('core') using environment variables
    """
    # Resolve database path (RFC-004)
    if settings.database_path is None:
        from system.host.environment import get_db_path
        db_path = get_db_path('core')
    else:
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
        # Use RFC-004 schema access utilities
        from system.schemas import get_sql_schema

        try:
            schema_sql = get_sql_schema('core')
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Failed to load Core schema: {e}\n"
                "Ensure schemas are bundled in system/schemas/sql/"
            ) from e

        db.executescript(schema_sql)
        db.commit()
