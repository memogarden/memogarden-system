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

from utils.config import default_settings as settings

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
    # Wait instead of failing instantly on contention: provider runs execute
    # in parallel threads per account (RFC-012 §4.5), so concurrent
    # read/write on provider_runs is routine, not exceptional.
    conn.execute("PRAGMA busy_timeout = 5000")
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

EXPECTED_SCHEMA_VERSION = "20261124"

# Ordered schema version history; each consecutive pair maps to
# migrations/migrate_<from>_to_<to>.sql (applied by _run_migrations).
# Append the new version here + add the migration file when bumping.
SCHEMA_VERSION_CHAIN = [
    "20251223",
    "20251229",
    "20251230",
    "20260129",
    "20260130",
    "20261105",
    "20261106",
    "20261109",
    "20261113",
    "20261114",
    "20261119",
    "20261120",
    "20261121",
    "20261124",
]


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

    # SQL-level hash helpers for data migrations (e.g. the RFC-008 v1.3
    # entity re-sign): mirrors utils.hash_chain.compute_entity_hash's
    # `x or ''` None handling via COALESCE in the SQL itself.
    import hashlib

    db.create_function(
        "sha256", 1, lambda v: hashlib.sha256(v.encode()).hexdigest()
    )

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

    # Walk the version chain forward, applying each consecutive migration
    # (fresh databases seed at EXPECTED directly)
    if (
        current_version in SCHEMA_VERSION_CHAIN
        and EXPECTED_SCHEMA_VERSION in SCHEMA_VERSION_CHAIN
    ):
        from_idx = SCHEMA_VERSION_CHAIN.index(current_version)
        to_idx = SCHEMA_VERSION_CHAIN.index(EXPECTED_SCHEMA_VERSION)
        if from_idx < to_idx:
            for i in range(from_idx, to_idx):
                _apply_migration(db, SCHEMA_VERSION_CHAIN[i], SCHEMA_VERSION_CHAIN[i + 1])
            return

    if current_version < EXPECTED_SCHEMA_VERSION:
        # Skip migrations for versions unknown to the chain (would need
        # hand-crafted migration in production). This allows development to
        # continue without full migration support.
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
                "Ensure the schemas submodule is initialized: git submodule update --init"
            ) from e

        db.executescript(schema_sql)
        db.commit()


# ============================================================================
# BUILTIN SCOPES (RFC-011 §4.1)
# ============================================================================

# (slug, label) — ordinary restricted Scope entities, identified by label.
# "Personal" (renamed from "Private", 2026-11-19): the word "private" is
# reserved for the Scope.visibility axis (RFC-011 visibility addendum) —
# scopes name domains, not visibility.
BUILTIN_SCOPES = (
    ("triage", "Triage / Untrusted"),
    ("personal", "Personal"),
)


def ensure_builtin_scopes() -> list[str]:
    """Provision builtin scopes idempotently (RFC-011 §4.1, §8 step 1).

    Builtin scopes are ordinary Scope entities (restricted like every scope);
    nothing distinguishes them structurally — they are conventions the
    operator's grants and provider policies point at. Entity rows are
    hash-chained, so seeding goes through Core.entity.create, never raw SQL.

    Safe to call at every startup: creates only what is missing.

    Returns:
        UUIDs of scopes created by this call (empty if all existed).
    """
    created: list[str] = []
    with get_core() as core:
        for _slug, label in BUILTIN_SCOPES:
            row = core._conn.execute(
                "SELECT uuid FROM entity "
                "WHERE type = 'Scope' AND json_extract(data, '$.label') = ?",
                (label,),
            ).fetchone()
            if row is None:
                uuid = core.entity.create(
                    entity_type="Scope",
                    data={
                        "label": label,
                        "visibility": "private",
                        "active_participants": [],
                        "artifact_uuids": [],
                    },
                )
                created.append(uuid)
    return created


def migrate_private_scope_rename(soil_db_path: str | None = None) -> dict:
    """Rename migration: builtin scope "Private" -> "Personal" (2026-11-19).

    The builtin "Private" scope predates the visibility work: the word
    "private" is reserved for the Scope.visibility axis (RFC-011 visibility
    addendum), so the builtin is renamed to "Personal". Immutable memory:
    the old Scope entity is superseded, never rewritten — seeding creates
    "Personal" as a new entity and this migration moves every reference
    onto the new UUID:

    - api_keys.grants (JSON array of {scope_uuid, ...})
    - entity data.scopes arrays (entities carry labels in data.scopes)
    - context_frame.active_scope
    - Soil item metadata.scopes arrays
    - providers.scope_policy JSON ("private" alias/label/uuid values)

    Also stamps `visibility: "private"` onto every live Scope whose data
    predates the attribute (restricted-by-default), and supersedes the old
    "Private" entity for the audit trail.

    Idempotent: acts only while an ACTIVE (non-superseded) "Private" scope
    exists — fresh and already-migrated databases no-op. Safe to call at
    every startup (invoked from init_system after both DBs initialize).

    Returns:
        Summary dict for the init log ({"ran": False, ...} when no-op).
    """
    import json

    summary = {
        "ran": False,
        "old_uuid": None,
        "new_uuid": None,
        "grants_updated": 0,
        "entities_updated": 0,
        "frames_updated": 0,
        "items_updated": 0,
        "policies_updated": 0,
        "visibility_stamped": 0,
    }

    def _swap_uuid_refs(text: str, old: str, new: str) -> str:
        """Replace quoted uuid refs (bare and core_-prefixed forms) in JSON text."""
        return text.replace(f'"{old}"', f'"{new}"').replace(
            f'"core_{old}"', f'"core_{new}"'
        )

    with get_core() as core:
        old_row = core._conn.execute(
            "SELECT uuid FROM entity WHERE type = 'Scope' "
            "AND json_extract(data, '$.label') = 'Private' "
            "AND superseded_by IS NULL"
        ).fetchone()
        if old_row is None:
            return summary  # fresh or already-migrated database
        old_uuid = old_row["uuid"]

        new_row = core._conn.execute(
            "SELECT uuid FROM entity WHERE type = 'Scope' "
            "AND json_extract(data, '$.label') = 'Personal' "
            "AND superseded_by IS NULL"
        ).fetchone()
        if new_row is None:
            # ensure_builtin_scopes seeds "Personal" first; create
            # defensively if this migration ever runs before seeding.
            new_uuid = core.entity.create(
                entity_type="Scope",
                data={
                    "label": "Personal",
                    "visibility": "private",
                    "active_participants": [],
                    "artifact_uuids": [],
                },
            )
        else:
            new_uuid = new_row["uuid"]
        summary.update(ran=True, old_uuid=old_uuid, new_uuid=new_uuid)

        # 1. api_keys.grants — entry permissions re-point to the new scope.
        key_rows = core._conn.execute(
            "SELECT id, grants FROM api_keys WHERE grants LIKE ?",
            (f'%{old_uuid}%',),
        ).fetchall()
        for row in key_rows:
            new_text = _swap_uuid_refs(row["grants"] or "[]", old_uuid, new_uuid)
            if new_text != (row["grants"] or "[]"):
                core._conn.execute(
                    "UPDATE api_keys SET grants = ? WHERE id = ?",
                    (new_text, row["id"]),
                )
                summary["grants_updated"] += 1

        # 2. entity data.scopes — entities carry labels in data.scopes.
        ent_rows = core._conn.execute(
            "SELECT uuid, data FROM entity WHERE type != 'Scope' "
            "AND EXISTS (SELECT 1 FROM json_each(data, '$.scopes') "
            "WHERE value = ?)",
            (old_uuid,),
        ).fetchall()
        for row in ent_rows:
            data = json.loads(row["data"])
            scopes = data.get("scopes")
            if not isinstance(scopes, list):
                continue  # storage always writes arrays; skip stray shapes
            data["scopes"] = [new_uuid if s == old_uuid else s for s in scopes]
            core.entity.update_data(row["uuid"], data)
            summary["entities_updated"] += 1

        # 3. context_frame.active_scope — frames inside the old scope.
        cur = core._conn.execute(
            "UPDATE context_frame SET active_scope = ? WHERE active_scope = ?",
            (new_uuid, old_uuid),
        )
        summary["frames_updated"] = cur.rowcount

        # 4. providers.scope_policy — rewrite legacy alias/label/uuid refs.
        policy_rows = core._conn.execute(
            "SELECT name, scope_policy FROM providers WHERE scope_policy IS NOT NULL"
        ).fetchall()
        for row in policy_rows:
            try:
                policy = json.loads(row["scope_policy"])
            except (ValueError, TypeError):
                continue
            aliases = {"private", "Private", old_uuid, f"core_{old_uuid}"}
            changed = False
            for rule in policy.get("rules") or []:
                if rule.get("scope") in aliases:
                    rule["scope"] = "personal"
                    changed = True
            if policy.get("default") in aliases:
                policy["default"] = "personal"
                changed = True
            if changed:
                core._conn.execute(
                    "UPDATE providers SET scope_policy = ? WHERE name = ?",
                    (json.dumps(policy), row["name"]),
                )
                summary["policies_updated"] += 1

        # 5. Stamp visibility onto scopes that predate the attribute.
        stamp_rows = core._conn.execute(
            "SELECT uuid, data FROM entity WHERE type = 'Scope' "
            "AND superseded_by IS NULL "
            "AND json_extract(data, '$.visibility') IS NULL"
        ).fetchall()
        for row in stamp_rows:
            data = json.loads(row["data"])
            data["visibility"] = "private"
            core.entity.update_data(row["uuid"], data)
            summary["visibility_stamped"] += 1

    # 6. Soil items — metadata.scopes label arrays (metadata sits outside
    # the integrity hash, so in-place repair is legal here; RFC-011 §4.2).
    # Runs BEFORE the supersede: while the old scope is still active, any
    # crash simply re-runs the whole migration on next startup (every step
    # above is reference-targeted and idempotent). The supersede below is
    # the migration's point of no return.
    from system.soil.database import get_soil

    with get_soil(soil_db_path) as soil:
        conn = soil._get_connection()
        item_rows = conn.execute(
            "SELECT uuid, metadata FROM item "
            "WHERE EXISTS (SELECT 1 FROM json_each(metadata, '$.scopes') "
            "WHERE value = ?)",
            (old_uuid,),
        ).fetchall()
        for row in item_rows:
            new_text = _swap_uuid_refs(row["metadata"] or "{}", old_uuid, new_uuid)
            conn.execute(
                "UPDATE item SET metadata = ? WHERE uuid = ?",
                (new_text, row["uuid"]),
            )
            summary["items_updated"] += 1
        conn.commit()

    # 7. Supersede the old "Private" scope (audit trail — immutable
    # memory: the entity persists, references moved to "Personal").
    with get_core() as core:
        core.entity.supersede(old_uuid, new_uuid)

    return summary


# ============================================================================
# SYSTEM INITIALIZATION (Phase 7: Move DB initialization from API to System)
# ============================================================================

def init_system(verb: str = "run") -> dict:
    """Initialize both Core and Soil databases and run consistency checks.

    This function performs greenfield database initialization for the entire
    MemoGarden System. It is the public API for application startup.

    Performs:
    1. Creates databases if they don't exist (RFC-004 path resolution)
    2. Initializes schemas (applies migrations)
    3. Runs consistency checks (RFC-008)
    4. Returns system status

    The system is always-available - startup succeeds even if databases
    are missing (they will be created automatically).

    Args:
        verb: Deployment verb for path resolution (serve/run/deploy).
            Defaults to "run" for local development.

    Returns:
        Dict with keys:
        - 'status': SystemStatus enum value ('normal', 'inconsistent', 'read_only', 'safe_mode')
        - 'soil_db_path': Path to Soil database
        - 'core_db_path': Path to Core database
        - 'has_admin_user': True if admin user exists, False otherwise
        - 'databases_existed': True if databases existed before this call

    Raises:
        Exception: If database initialization fails (prevents app startup)
    """
    import os
    from system.host.environment import resolve_context
    from system.transaction_coordinator import TransactionCoordinator, SystemStatus
    from system.soil.database import get_soil

    # Get database paths (RFC-004 context resolution)
    ctx = resolve_context(verb)
    # Both paths resolve through the canonical runtime resolver —
    # RuntimeContext.get_db_path delegates to module-level get_db_path()
    # (RFC-004 INV-PKG-001 env chain, cwd fallback), the same resolution
    # get_core()/get_soil()/init_db() use. Before memogarden-system#7 the
    # context applied its own verb default: MEMOGARDEN_DATA_DIR deploys
    # initialized soil at the verb default while the runtime read the env
    # path, leaving the app with an empty soil.db (every Soil-touching
    # endpoint 500'd with "no such table: item").
    soil_db_path = ctx.get_db_path('soil')
    # Core additionally honors the settings-level override, exactly like
    # _create_connection()/init_db() do.
    if settings.database_path is not None:
        core_db_path = Path(settings.database_path)
    else:
        core_db_path = ctx.get_db_path('core')

    # Check if databases exist
    soil_exists = os.path.exists(soil_db_path)
    core_exists = os.path.exists(core_db_path)
    databases_existed = soil_exists and core_exists

    if not soil_exists or not core_exists:
        # Create parent directories if needed
        soil_dir = os.path.dirname(soil_db_path)
        core_dir = os.path.dirname(core_db_path)
        if soil_dir:
            os.makedirs(soil_dir, exist_ok=True)
        if core_dir:
            os.makedirs(core_dir, exist_ok=True)

    # Initialize Core database (creates if missing, applies migrations)
    init_db()

    # Provision builtin scopes (RFC-011 §4.1: triage, personal) — idempotent
    ensure_builtin_scopes()

    # Initialize Soil database (creates if missing, applies migrations)
    with get_soil(str(soil_db_path)) as soil:
        soil.init_schema()

    # Rename migration: builtin scope "Private" -> "Personal" (2026-11-19,
    # RFC-011 visibility addendum) — runs after both DBs initialize since it
    # moves references across Core (grants, frames, policies) and Soil (items).
    scope_rename = migrate_private_scope_rename(str(soil_db_path))
    if scope_rename.get("ran"):
        print(
            f"[init] scope rename: Private {scope_rename['old_uuid']} -> "
            f"Personal {scope_rename['new_uuid']} (grants={scope_rename['grants_updated']}, "
            f"entities={scope_rename['entities_updated']}, frames={scope_rename['frames_updated']}, "
            f"items={scope_rename['items_updated']}, policies={scope_rename['policies_updated']}, "
            f"visibility_stamped={scope_rename['visibility_stamped']})"
        )

    # Run consistency checks (RFC-008)
    coordinator = TransactionCoordinator(
        soil_db_path=soil_db_path,
        core_db_path=core_db_path
    )
    system_status = coordinator.check_consistency()

    # Check if admin user exists
    has_admin = False
    with get_core() as core:
        has_admin = core.has_admin_user()

    return {
        'status': system_status,
        'soil_db_path': str(soil_db_path),
        'core_db_path': str(core_db_path),
        'has_admin_user': has_admin,
        'databases_existed': databases_existed,
        'scope_rename': scope_rename,
    }
