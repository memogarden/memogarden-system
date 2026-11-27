"""entity_fts — the Core layer's full-text index over entity content.

Internal derived metadata (operator ruling, 2026-11-27): a rebuildable
token index over entity.data — never migrated, never versioned. The DDL is
ensured at runtime on every core connection (mirroring the
ensure_builtin_scopes pattern), the legacy backfill is boundary-stamped in
_schema_metadata exactly like soil's item_fts, and rebuild_entity_fts() is
the maintenance path.

Why the triggers differ from item_fts: soil items are append-only in
content, so item_fts deliberately has no AU trigger (a supersede UPDATE
never changes data). Entities are MUTABLE — update_data() rewrites data in
place — so entity_fts requires the AU trigger (FTS5 delete-command with
OLD.data + insert of NEW.data). Exactly-once per event: UPDATE fires AU
only, DELETE fires AD only, and AD's OLD.data always equals the text AU
last indexed (the last AU's NEW.data is the row content at delete time).

Caveats inherited from the external-content design: a VACUUM may renumber
rowids and desync the index (rebuild_entity_fts() is the cure — same
exposure item_fts carries), and dropping the FTS table without cleaning
its stamps leaves a stale "backfilled" flag — run rebuild_entity_fts()
after any manual drop.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

FTS_TABLE = "entity_fts"
_BOUNDARY_KEY = "entity_fts_created_maxrowid"
_BACKFILLED_KEY = "entity_fts_backfilled"

_DDL = (
    # Indexed column is the whole entity data JSON — same choice item_fts
    # makes for facts (the label and the document text both live in data).
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(
        data,
        content='entity',
        content_rowid='rowid'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entity_fts_ai AFTER INSERT ON entity BEGIN
        INSERT INTO entity_fts(rowid, data) VALUES (NEW.rowid, NEW.data);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entity_fts_au AFTER UPDATE ON entity BEGIN
        INSERT INTO entity_fts(entity_fts, rowid, data)
        VALUES ('delete', OLD.rowid, OLD.data);
        INSERT INTO entity_fts(rowid, data) VALUES (NEW.rowid, NEW.data);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS entity_fts_ad AFTER DELETE ON entity BEGIN
        INSERT INTO entity_fts(entity_fts, rowid, data)
        VALUES ('delete', OLD.rowid, OLD.data);
    END
    """,
)

_TABLE_CHECK = (
    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entity'"
)


def _fts_match_query(query: str) -> str | None:
    """Harden a raw user query into a safe FTS5 MATCH expression.

    Same contract as soil's _fts_match_query (database.py): every
    whitespace-separated token becomes a quoted prefix term ('"seno"*'),
    implicitly ANDed — syntactically safe regardless of input. None when
    the query has no tokens (an empty MATCH raises a syntax error).
    """
    tokens = [t for t in query.split() if t]
    if not tokens:
        return None
    parts = []
    for token in tokens:
        escaped = token.replace('"', '""')
        parts.append(f'"{escaped}"*')
    return " ".join(parts)


def ensure_entity_fts(conn: sqlite3.Connection) -> bool:
    """Ensure the entity_fts index exists on this database.

    Called on every core connection open: the DDL statements are no-ops
    once present, and the legacy backfill (rows the DDL creation never
    saw) runs once, boundary-stamped in _schema_metadata. Skips silently
    on a fresh/uninitialized file (no entity table yet) — init_db creates
    it later and the next open ensures again.

    Returns:
        True when this call created the FTS DDL (first ensure).
    """
    row = conn.execute(_TABLE_CHECK).fetchone()
    if row is None:
        return False

    have_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (FTS_TABLE,),
    ).fetchone()
    if have_table is not None:
        _backfill_if_needed(conn)
        return False

    # Creation path: DDL + boundary stamp + legacy backfill in ONE atomic
    # transaction, under explicit autocommit handling (python's implicit
    # transaction management would otherwise leave the boundary INSERT's
    # transaction dangling — commit-skipping here broke every later writer).
    #
    # The write lock is taken FIRST and the have_table/boundary checks run
    # INSIDE it (soil.sql's BEGIN IMMEDIATE pattern): another writer — the
    # minute-timer ingest is a routine concurrent core writer — may insert
    # entities while we wait for the lock, and rows landing in that window
    # are neither trigger-indexed (no triggers yet) nor backfilled (past
    # the boundary) if the boundary was computed before the lock. Stamps
    # use INSERT OR IGNORE because a creation-race loser arrives here with
    # the winner's stamps already committed; the in-lock re-checks are
    # what keep that safe, not the ignore.
    with _explicit_txn(conn):
        have_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (FTS_TABLE,),
        ).fetchone()
        if have_table is not None:
            # A racing winner created everything while we held the lock
            # queue — only the backfill check remains ours to run.
            _backfill_locked(conn)
            return False
        boundary = conn.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM entity"
        ).fetchone()[0]
        for stmt in _DDL:
            conn.execute(stmt)
        conn.execute(
            """INSERT OR IGNORE INTO _schema_metadata(key, value, updated_at)
               VALUES (?, ?, datetime('now'))""",
            (_BOUNDARY_KEY, str(boundary)),
        )
        conn.execute(
            f"""INSERT INTO {FTS_TABLE}(rowid, data)
                SELECT rowid, data FROM entity WHERE rowid <= ?""",
            (boundary,),
        )
        _stamp_backfilled(conn)
    return True


class _explicit_txn:
    """Explicit BEGIN IMMEDIATE under autocommit — never touches a
    transaction python's legacy isolation mode may have opened implicitly.

    Yields while the transaction is open; commits on clean exit, rolls
    back on error. If the connection entered with a transaction already
    open (caller's implicit DML), it does NOT begin one and does NOT
    commit — the work joins the caller's transaction (the Core context
    commits on exit; the stamps are idempotent, so the crash window is
    self-healing on the next ensure).
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._prev_isolation = conn.isolation_level
        self._own = False

    def __enter__(self):
        # Disable python's implicit BEGIN so our explicit transaction
        # control is the only one acting on this connection.
        self._conn.isolation_level = None
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN IMMEDIATE")
            self._own = True
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                if self._own:
                    self._conn.execute("COMMIT")
            else:
                if self._own:
                    self._conn.execute("ROLLBACK")
        finally:
            self._conn.isolation_level = self._prev_isolation
        return False


def _stamp_backfilled(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT INTO _schema_metadata(key, value, updated_at)
           VALUES (?, '1', datetime('now'))
           ON CONFLICT(key) DO UPDATE SET
               value = '1', updated_at = datetime('now')""",
        (_BACKFILLED_KEY,),
    )


def _backfill_if_needed(conn: sqlite3.Connection) -> None:
    """Index rows the DDL creation never saw — the crash-recovery path.

    Normal flow backfills inside the creation transaction; this handles a
    database whose DDL exists but whose backfill stamp never landed (a
    crash between the two, or an index dropped by hand). Same atomicity
    rules as the creation path.
    """
    try:
        row = conn.execute(
            "SELECT value FROM _schema_metadata WHERE key = ?", (_BACKFILLED_KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        # _schema_metadata itself is missing (raw file) — init_db creates
        # it; a later open re-runs the check.
        return
    if row is not None and str(row["value"]) == "1":
        return

    with _explicit_txn(conn):
        _backfill_locked(conn)


def _backfill_locked(conn: sqlite3.Connection) -> None:
    """The backfill body — the caller holds the explicit transaction.

    No-ops when the backfill stamp is set (the racing-winner path arrives
    here with the winner's stamps committed).
    """
    try:
        row = conn.execute(
            "SELECT value FROM _schema_metadata WHERE key = ?", (_BACKFILLED_KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return
    if row is not None and str(row["value"]) == "1":
        return
    boundary_row = conn.execute(
        "SELECT value FROM _schema_metadata WHERE key = ?", (_BOUNDARY_KEY,)
    ).fetchone()
    boundary = int(boundary_row["value"]) if boundary_row is not None else 0
    conn.execute(
        f"""INSERT INTO {FTS_TABLE}(rowid, data)
            SELECT rowid, data FROM entity WHERE rowid <= ?""",
        (boundary,),
    )
    _stamp_backfilled(conn)


def rebuild_entity_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the index from entity content (FTS5 'rebuild' command).

    Wipes and re-indexes every entity row in one transaction — correct but
    lock-heavy (holds the core write lock for the whole rebuild). The
    backfill stamp is set to the done state so a later pass cannot
    re-insert.
    """
    row = conn.execute(_TABLE_CHECK).fetchone()
    if row is None:
        return
    ensure_ddl_present(conn)
    with _explicit_txn(conn):
        conn.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES('rebuild')")
        _stamp_backfilled(conn)


def ensure_ddl_present(conn: sqlite3.Connection) -> None:
    """Create the DDL if missing without touching the boundary stamp."""
    row = conn.execute(_TABLE_CHECK).fetchone()
    if row is None:
        return
    for stmt in _DDL:
        conn.execute(stmt)


def search_entity_fts(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    offset: int = 0,
    live_only: bool = True,
    allowed_scopes: list[str] | None = None,
    scope_filter: list[str] | None = None,
    types: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Full-text search over entity data (BM25 rank, FTS5 index).

    Mirror of Soil.search_items_fts for the Core layer. The index is
    maintained by the entity_fts triggers (AI insert / AU update-sync /
    AD delete) and completed by the boundary backfill.

    Args:
        query: Raw search text — hardened internally (safe MATCH syntax,
            quoted prefix terms, implicit AND).
        limit / offset: Rank-order pagination.
        live_only: Exclude superseded entities. Default True (entities
            are versioned; readers see the live set — the operator
            console's semantics).
        allowed_scopes: RFC-011 §4.9(3) pre-match enforcement, same
            contract as entity.search: None = unrestricted; [] = only
            unlabeled entities; labeled entities outside the set never
            become matches.
        scope_filter: Narrow-to-scopes filter (must carry at least one of
            these labels; a filter, not an enforcement boundary).
        types: Restrict to these entity type values (None = all types).

    Returns:
        Matching entity rows ordered by bm25 rank (best first) with
        `rank` (negative; more negative is better).
    """
    match = _fts_match_query(query)
    if match is None:
        return []

    conditions = ["entity_fts MATCH ?"]
    params: list = [match]
    if live_only:
        conditions.append("e.superseded_by IS NULL")
    if allowed_scopes is not None:
        if allowed_scopes:
            placeholders = ", ".join("?" for _ in allowed_scopes)
            conditions.append(
                "(NOT EXISTS (SELECT 1 FROM json_each(e.data, '$.scopes')) "
                f"OR EXISTS (SELECT 1 FROM json_each(e.data, '$.scopes') "
                f"WHERE value IN ({placeholders})))"
            )
            params.extend(allowed_scopes)
        else:
            # Empty allowed set (no frame, no public scopes): only
            # unlabeled entities are visible (SG-1).
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM json_each(e.data, '$.scopes'))"
            )
    if scope_filter:
        placeholders = ", ".join("?" for _ in scope_filter)
        conditions.append(
            f"EXISTS (SELECT 1 FROM json_each(e.data, '$.scopes') "
            f"WHERE value IN ({placeholders}))"
        )
        params.extend(scope_filter)
    if types:
        placeholders = ", ".join("?" for _ in types)
        conditions.append(f"e.type IN ({placeholders})")
        params.extend(types)

    sql = f"""
        SELECT e.uuid, e.type, e.data, e.version, e.created_at, e.updated_at,
               e.superseded_by, bm25(entity_fts) AS rank
        FROM entity_fts
        JOIN entity e ON e.rowid = entity_fts.rowid
        WHERE {' AND '.join(conditions)}
        ORDER BY rank, e.rowid
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    return conn.execute(sql, params).fetchall()
