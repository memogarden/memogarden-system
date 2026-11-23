"""Soil database operations for MemoGarden Soil."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC
from pathlib import Path

from .fact import SOIL_UUID_PREFIX, Evidence, Fact, current_day, generate_soil_uuid
from .relation import SystemRelation


class Soil:
    """Soil database for immutable Facts and System Relations.

    CONNECTION LIFECYCLE (Session 6.5 Refactor):
    - Soil MUST be used as context manager (enforced at runtime)
    - __enter__: Marks Soil as active, creates connection, returns self
    - __exit__: Commits on success, rollbacks on exception, always closes
    - Operations call _get_connection() which raises RuntimeError if not in context
    """

    def __init__(self, db_path: str | Path = "soil.db"):
        """Initialize Soil database.

        Args:
            db_path: Path to SQLite database file or SQLite URI connection string

        Note:
            Soil must be used as context manager. Operations will raise
            RuntimeError if called outside of 'with' statement.
        """
        # Preserve URI strings, convert file paths to Path objects
        if isinstance(db_path, str) and db_path.startswith("file:"):
            self.db_path = db_path  # Keep URI as string
        else:
            self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._in_context = False  # Track if we're inside a context manager

    def _get_connection(self) -> sqlite3.Connection:
        """Get connection, enforcing context manager usage.

        Returns:
            SQLite connection

        Raises:
            RuntimeError: If Soil is not being used as context manager
        """
        if not self._in_context:
            raise RuntimeError(
                "Soil must be used as context manager. "
                "Use: with get_soil() as soil: ..."
            )
        if self._conn is None:
            # Detect if db_path is a SQLite URI
            is_uri = isinstance(self.db_path, str) and self.db_path.startswith("file:")

            # Only create parent directory if not a URI path
            if not is_uri:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                db_path = str(self.db_path)
            else:
                db_path = self.db_path  # Use URI as-is

            # Use uri=True for URI paths
            self._conn = sqlite3.connect(db_path, uri=is_uri)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            # RFC-012: N account runs + the CLI attachments pass now write
            # one soil.db concurrently (heartbeat at :00 collides with
            # manual runs) — WAL allows one writer; without a busy timeout
            # a second writer fails instantly ("database is locked") and
            # fails the whole run. Wait instead.
            self._conn.execute("PRAGMA busy_timeout = 5000")
        return self._conn

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def commit(self) -> None:
        """Commit the current transaction without leaving the context.

        Needed when other connections must observe this instance's writes
        mid-context — e.g. the provider runtime committing each fact before
        a provider's create_relations() looks it up through its own Soil
        instance (RFC-004 §12.2: providers yield, the runtime stores, and
        relation resolution reads what the runtime just stored).
        """
        if self._conn is not None:
            self._conn.commit()

    def __enter__(self) -> Soil:
        """Enter context manager for transaction.

        Returns:
            self for use in with-statement
        """
        self._in_context = True
        self._get_connection()  # Ensure connection is created
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
            if self._conn is not None:  # Only commit/rollback if connection exists
                if exc_type is None:
                    # No exception - commit the transaction
                    self._conn.commit()
                else:
                    # Exception occurred - rollback the transaction
                    self._conn.rollback()
        finally:
            # Always close connection
            self.close()

    # ==========================================================================
    # INITIALIZATION
    # ==========================================================================

    def init_schema(self):
        """Initialize database schema from bundled schema.sql.

        Uses RFC-004 schema access utilities:
        - Tries importlib.resources first (bundled package)
        - Falls back to file reading (development mode)
        """
        from system.schemas import get_sql_schema

        try:
            schema_sql = get_sql_schema('soil')
        except FileNotFoundError as e:
            raise RuntimeError(
                f"Failed to load Soil schema: {e}\n"
                "Ensure schemas are bundled in system/schemas/sql/"
            ) from e

        conn = self._get_connection()
        conn.executescript(schema_sql)
        conn.commit()

    def get_schema_version(self) -> str | None:
        """Get current schema version."""
        cursor = self._get_connection().execute(
            "SELECT value FROM _schema_metadata WHERE key = 'version'"
        )
        row = cursor.fetchone()
        return row[0] if row else None

    # ==========================================================================
    # ITEM OPERATIONS
    # ==========================================================================

    def create_fact(self, fact: Fact) -> str:
        """Create a new Fact in Soil.

        Args:
            fact: Fact to create

        Returns:
            UUID of created Fact
        """
        # Compute hash if not provided
        if fact.integrity_hash is None:
            fact.integrity_hash = fact.compute_hash()

        conn = self._get_connection()
        conn.execute(
            """INSERT INTO item (uuid, _type, realized_at, canonical_at, integrity_hash,
                              fidelity, superseded_by, superseded_at, data, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fact.uuid,
                fact._type,
                fact.realized_at,
                fact.canonical_at,
                fact.integrity_hash,
                fact.fidelity,
                fact.superseded_by,
                fact.superseded_at,
                json.dumps(fact.data),
                json.dumps(fact.metadata) if fact.metadata else None,
            )
        )
        return fact.uuid

    def get_fact(self, uuid: str) -> Fact | None:
        """Get Fact by UUID.

        Args:
            uuid: Fact UUID (with or without "soil_" prefix)

        Returns:
            Fact if found, None otherwise
        """
        # Ensure prefix
        if not uuid.startswith(SOIL_UUID_PREFIX):
            uuid = f"{SOIL_UUID_PREFIX}{uuid}"

        cursor = self._get_connection().execute(
            "SELECT * FROM item WHERE uuid = ?", (uuid,)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return Fact(
            uuid=row["uuid"],
            _type=row["_type"],
            realized_at=row["realized_at"],
            canonical_at=row["canonical_at"],
            integrity_hash=row["integrity_hash"],
            fidelity=row["fidelity"],
            superseded_by=row["superseded_by"],
            superseded_at=row["superseded_at"],
            data=json.loads(row["data"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def mark_superseded(self, original_uuid: str, superseded_by_uuid: str, superseded_at: str) -> bool:
        """Mark a Fact as superseded by another Fact.

        Updates the original Fact's superseded_by and superseded_at fields
        to indicate it has been replaced by a new version.

        Args:
            original_uuid: UUID of the Fact being superseded
            superseded_by_uuid: UUID of the new Fact that supersedes it
            superseded_at: ISO 8601 timestamp when supersession occurred

        Returns:
            True if Fact was found and updated, False if not found
        """
        # Ensure UUIDs have prefix
        if not original_uuid.startswith(SOIL_UUID_PREFIX):
            original_uuid = f"{SOIL_UUID_PREFIX}{original_uuid}"
        if not superseded_by_uuid.startswith(SOIL_UUID_PREFIX):
            superseded_by_uuid = f"{SOIL_UUID_PREFIX}{superseded_by_uuid}"

        conn = self._get_connection()
        cursor = conn.execute(
            """UPDATE item
               SET superseded_by = ?, superseded_at = ?
               WHERE uuid = ?""",
            (superseded_by_uuid, superseded_at, original_uuid)
        )
        return cursor.rowcount > 0

    # ==========================================================================
    # CHANGE FEED (system#12 search-sync slice)
    # ==========================================================================
    # The change_log table (schemas/sql/soil.sql §CHANGE FEED) is maintained
    # by triggers: every INSERT/UPDATE/DELETE on item / system_relation appends
    # a row inside the write's own transaction. These read helpers are the
    # consumer interface for derived indexes (FTS5 lexical sweep, embed-daemon
    # catch-up): checkpoint max_change_seq(), sweep changes_since().

    def max_change_seq(self) -> int:
        """Current high-water mark of the Soil change feed.

        Returns:
            Largest committed seq in change_log (0 if the feed is empty)

        Note:
            Consumers store this value only AFTER persisting everything
            derived from the swept rows, so a crash replays, never skips.
        """
        row = self._get_connection().execute(
            "SELECT COALESCE(MAX(seq), 0) FROM change_log"
        ).fetchone()
        return int(row[0])

    def changes_since(self, seq: int, limit: int = 1000) -> list[dict]:
        """Sweep committed feed rows after a checkpoint seq.

        Args:
            seq: High-water checkpoint (from max_change_seq / consumer state);
                 rows with seq <= this are never returned again.
            limit: Max rows to return per call; sweep in a loop until fewer
                   than limit come back, then checkpoint.

        Returns:
            Feed rows as dicts: seq, tbl ('item'|'system_relation'), uuid,
            kind ('insert'|'update'|'delete'), changed_at, ordered by seq.
        """
        cursor = self._get_connection().execute(
            """SELECT seq, tbl, uuid, kind, changed_at
               FROM change_log
               WHERE seq > ?
               ORDER BY seq
               LIMIT ?""",
            (seq, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    def find_item_by_rfc_message_id(self, message_id: str) -> Fact | None:
        """Find Email fact by RFC Message-ID.

        Args:
            message_id: RFC 822 Message-ID header value

        Returns:
            Email Fact if found, None otherwise
        """
        cursor = self._get_connection().execute(
            """SELECT * FROM item
               WHERE _type = 'Email'
               AND json_extract(data, '$.rfc_message_id') = ?""",
            (message_id,)
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return Fact(
            uuid=row["uuid"],
            _type=row["_type"],
            realized_at=row["realized_at"],
            canonical_at=row["canonical_at"],
            integrity_hash=row["integrity_hash"],
            fidelity=row["fidelity"],
            superseded_by=row["superseded_by"],
            superseded_at=row["superseded_at"],
            data=json.loads(row["data"]),
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
        )

    def list_items(self, _type: str | None = None, limit: int = 100) -> list[Fact]:
        """List Facts, optionally filtered by type.

        Args:
            _type: Filter by Fact type (e.g., 'Email', 'Note')
            limit: Maximum number of Facts to return

        Returns:
            List of Facts
        """
        if _type:
            cursor = self._get_connection().execute(
                """SELECT * FROM item WHERE _type = ? ORDER BY realized_at DESC LIMIT ?""",
                (_type, limit)
            )
        else:
            cursor = self._get_connection().execute(
                """SELECT * FROM item ORDER BY realized_at DESC LIMIT ?""",
                (limit,)
            )

        items = []
        for row in cursor.fetchall():
            items.append(Fact(
                uuid=row["uuid"],
                _type=row["_type"],
                realized_at=row["realized_at"],
                canonical_at=row["canonical_at"],
                integrity_hash=row["integrity_hash"],
                fidelity=row["fidelity"],
                superseded_by=row["superseded_by"],
                superseded_at=row["superseded_at"],
                data=json.loads(row["data"]),
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            ))
        return items

    def search_items(
        self,
        query: str,
        coverage: str = "content",
        limit: int = 20,
        allowed_scopes: list[str] | None = None,
        scope_uuid: str | None = None,
    ) -> list[sqlite3.Row]:
        """Search items (facts) by fuzzy text matching.

        Session 9: Public API for fact/item search.
        Uses SQLite LIKE with wildcards for fuzzy matching.

        Args:
            query: Search string (will be wrapped with % wildcards)
            coverage: Coverage level - "names" (_type only), "content" (_type+data), "full" (all fields)
            limit: Maximum results to return
            allowed_scopes: RFC-011 §4.9(3) pre-match enforcement — plain
                scope uuids the requester may see (active frame + public
                visibility); None = unrestricted. Scoped items outside the
                set never become matches (excluded before LIKE evaluation,
                ORDER, and LIMIT).
            scope_uuid: Convenience `scope` refinement (RFC-005 v8) —
                restrict to one scope uuid; enforcement is independent.

        Returns:
            List of matching fact rows (raw database rows, not Fact objects)

        Note:
            Results ordered by realized_at DESC (most recent first)
            Returns sqlite3.Row objects for efficiency (handler converts to dict)
        """
        # Build search pattern with wildcards
        search_pattern = f"%{query}%"

        # Build WHERE clause based on coverage level
        where_conditions = []
        params = []

        if coverage == "names":
            # Search in item type only
            where_conditions.append("_type LIKE ?")
            params.append(search_pattern)
        elif coverage == "content":
            # Search in type and JSON data
            where_conditions.append("(_type LIKE ? OR data LIKE ?)")
            params.extend([search_pattern, search_pattern])
        else:  # full
            # Search all fields
            where_conditions.append("(_type LIKE ? OR data LIKE ? OR metadata LIKE ?)")
            params.extend([search_pattern, search_pattern, search_pattern])

        # RFC-011 §4.9(3): scope enforcement PRE-MATCH — scoped items outside
        # the allowed set never become matches (a hit itself is a leak, and
        # a scoped hit displacing a real one corrupts pagination). Applied
        # here at WHERE level, before ORDER/LIMIT, on every coverage level.
        # allowed_scopes None = unrestricted (flag-off / sovereign callers);
        # items are visible iff unlabeled (SG-1) or ANY label is allowed
        # (active frame or public visibility — most permissive wins).
        if allowed_scopes is not None:
            if allowed_scopes:
                placeholders = ", ".join("?" for _ in allowed_scopes)
                where_conditions.append(
                    "(NOT EXISTS (SELECT 1 FROM json_each(metadata, '$.scopes')) "
                    f"OR EXISTS (SELECT 1 FROM json_each(metadata, '$.scopes') "
                    f"WHERE value IN ({placeholders})))"
                )
                params.extend(allowed_scopes)
            else:
                # Empty allowed set (no frame, no public scopes): only
                # unlabeled items are visible (SG-1).
                where_conditions.append(
                    "NOT EXISTS (SELECT 1 FROM json_each(metadata, '$.scopes'))"
                )
        # Convenience `scope` refinement (RFC-005 v8) — narrows results to
        # one scope; enforcement is automatic and independent of it.
        if scope_uuid is not None:
            where_conditions.append(
                "EXISTS (SELECT 1 FROM json_each(metadata, '$.scopes') "
                "WHERE value IN (?, ?))"
            )
            params.extend([scope_uuid, f"core_{scope_uuid}"])

        # Build query
        sql = f"""
            SELECT uuid, _type, data, integrity_hash,
                   realized_at, canonical_at, metadata, fidelity
            FROM item
            WHERE {' AND '.join(where_conditions) if where_conditions else '1=1'}
            ORDER BY realized_at DESC
            LIMIT ?
        """
        params.append(limit)

        # Execute query
        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        return cursor.fetchall()

    # ==========================================================================
    # FULL-TEXT SEARCH (FTS5 — api#95 Phase 1)
    # ==========================================================================

    @staticmethod
    def _fts_match_query(query: str) -> str | None:
        """Harden a raw user query into a safe FTS5 MATCH expression.

        Every whitespace-separated token becomes a quoted prefix term
        ('"seno"*'), implicitly ANDed — syntactically safe regardless of
        what the user types (quotes, punctuation, CJK, emoji). Returns None
        when the query has no tokens (an empty MATCH raises a syntax error).
        """
        tokens = [t for t in query.split() if t]
        if not tokens:
            return None
        parts = []
        for token in tokens:
            escaped = token.replace('"', '""')
            parts.append(f'"{escaped}"*')
        return " ".join(parts)

    def search_items_fts(
        self,
        query: str,
        limit: int = 20,
        offset: int = 0,
        allowed_scopes: list[str] | None = None,
        scope_uuid: str | None = None,
        scope_filter: list[str] | None = None,
        live_only: bool = False,
        types: list[str] | None = None,
    ) -> list[sqlite3.Row]:
        """Full-text search over item data (BM25 rank, FTS5 index).

        api#95 Phase 1: the ranked lexical plane. The index is maintained by
        the item_fts_ai trigger (items are append-only), so it is always
        complete for rows inserted after the DDL landed; rows that predate
        it appear as backfill_item_fts() progresses.

        Args:
            query: Raw search text — hardened internally (safe MATCH
                syntax, quoted prefix terms, implicit AND).
            limit: Maximum results.
            offset: Rank-order offset for pagination.
            allowed_scopes: RFC-011 §4.9(3) pre-match enforcement — same
                contract as search_items (None = unrestricted; [] = only
                unlabeled items; scoped items outside the set never become
                matches).
            scope_uuid: Convenience `scope` refinement (RFC-005 v8).
            scope_filter: Narrow-to-scopes filter (item must carry at
                least one of these labels; unlike allowed_scopes this is a
                filter, not an enforcement boundary — unlabeled items do
                NOT pass).
            live_only: Exclude superseded items (the console's live view).
                Default False keeps parity with search_items, which also
                matches superseded rows.
            types: Restrict to these fact _type values (console type
                chips); None = all types.

        Returns:
            Matching item rows ordered by bm25 rank (best first) — same
            columns as search_items plus `rank` (negative; more negative
            is better).
        """
        match = self._fts_match_query(query)
        if match is None:
            return []

        conditions = ["item_fts MATCH ?"]
        params: list = [match]
        if live_only:
            conditions.append("i.superseded_by IS NULL")
        # RFC-011 §4.9(3): same pre-match shape as search_items — applied
        # at WHERE level, before ORDER/LIMIT, on every caller.
        if allowed_scopes is not None:
            if allowed_scopes:
                placeholders = ", ".join("?" for _ in allowed_scopes)
                conditions.append(
                    "(NOT EXISTS (SELECT 1 FROM json_each(i.metadata, '$.scopes')) "
                    f"OR EXISTS (SELECT 1 FROM json_each(i.metadata, '$.scopes') "
                    f"WHERE value IN ({placeholders})))"
                )
                params.extend(allowed_scopes)
            else:
                conditions.append(
                    "NOT EXISTS (SELECT 1 FROM json_each(i.metadata, '$.scopes'))"
                )
        if scope_uuid is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM json_each(i.metadata, '$.scopes') "
                "WHERE value IN (?, ?))"
            )
            params.extend([scope_uuid, f"core_{scope_uuid}"])
        if scope_filter:
            placeholders = ", ".join("?" for _ in scope_filter)
            conditions.append(
                f"EXISTS (SELECT 1 FROM json_each(i.metadata, '$.scopes') "
                f"WHERE value IN ({placeholders}))"
            )
            params.extend(scope_filter)
        if types:
            placeholders = ", ".join("?" for _ in types)
            conditions.append(f"i._type IN ({placeholders})")
            params.extend(types)

        sql = f"""
            SELECT i.uuid, i._type, i.data, i.integrity_hash,
                   i.realized_at, i.canonical_at, i.metadata, i.fidelity,
                   bm25(item_fts) AS rank
            FROM item_fts
            JOIN item i ON i.rowid = item_fts.rowid
            WHERE {' AND '.join(conditions)}
            ORDER BY rank, i.rowid
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        conn = self._get_connection()
        return conn.execute(sql, params).fetchall()

    def rebuild_item_fts(self) -> None:
        """Atomically rebuild the FTS index from item content.

        Uses FTS5's 'rebuild' command: wipes the index and re-inserts every
        item row in a single transaction. Correct but lock-heavy — it holds
        the soil write lock for the whole rebuild (minutes at prod corpus
        size). For live systems prefer backfill_item_fts(). After a rebuild
        the backfill watermark is set to the done state so a later
        backfill_item_fts() call cannot re-insert already-indexed rows.
        """
        conn = self._get_connection()
        conn.execute("INSERT INTO item_fts(item_fts) VALUES('rebuild')")
        # Everything is indexed now — mark the backfill done (watermark =
        # the legacy boundary, falling back to the current max rowid if the
        # boundary stamp is somehow absent) so a later backfill_item_fts()
        # cannot re-insert already-indexed rows.
        conn.execute(
            """INSERT INTO _schema_metadata(key, value, updated_at)
               VALUES ('item_fts_backfill_rowid',
                       COALESCE(
                           (SELECT value FROM _schema_metadata
                            WHERE key = 'item_fts_created_maxrowid'),
                           (SELECT CAST(COALESCE(MAX(rowid), 0) AS TEXT)
                            FROM item)),
                       datetime('now'))
               ON CONFLICT(key) DO UPDATE SET
                   value = excluded.value, updated_at = excluded.updated_at"""
        )
        conn.commit()

    def backfill_item_fts(self, batch_size: int = 2000) -> int:
        """Index item rows the FTS trigger never saw (legacy databases).

        The item_fts_ai trigger indexes everything inserted after the DDL
        landed; rows that predate it are backfilled here in small atomic
        transactions keyed by a rowid high-water mark
        (_schema_metadata 'item_fts_backfill_rowid'). The legacy boundary
        is 'item_fts_created_maxrowid' (recorded at trigger creation), so
        batches can never overlap trigger-indexed rows. Idempotent: safe to
        re-run and to interleave with live inserts. Each batch commits its
        FTS inserts and watermark update together — a crash mid-backfill
        leaves the previous watermark intact.

        Args:
            batch_size: Rows per transaction.

        Returns:
            Number of item rows newly indexed.
        """
        conn = self._get_connection()

        def _meta_int(key: str) -> int:
            row = conn.execute(
                "SELECT value FROM _schema_metadata WHERE key = ?", (key,)
            ).fetchone()
            return int(row["value"]) if row else 0

        boundary = _meta_int("item_fts_created_maxrowid")
        watermark = _meta_int("item_fts_backfill_rowid")

        total = 0
        while watermark < boundary:
            # One BEGIN IMMEDIATE per batch: the watermark read and the FTS
            # inserts commit atomically AND hold the writer lock for the
            # whole batch, so two concurrent backfills can never read the
            # same watermark and double-index a range.
            conn.execute("BEGIN IMMEDIATE")
            try:
                batch = conn.execute(
                    """SELECT rowid, data FROM item
                       WHERE rowid > ? AND rowid <= ?
                       ORDER BY rowid LIMIT ?""",
                    (watermark, min(watermark + batch_size, boundary),
                     batch_size),
                ).fetchall()
                if not batch:
                    conn.rollback()
                    break
                conn.executemany(
                    "INSERT INTO item_fts(rowid, data) VALUES (?, ?)",
                    [(r["rowid"], r["data"]) for r in batch],
                )
                watermark = batch[-1]["rowid"]
                conn.execute(
                    """INSERT INTO _schema_metadata(key, value, updated_at)
                       VALUES ('item_fts_backfill_rowid', ?, datetime('now'))
                       ON CONFLICT(key) DO UPDATE SET
                           value = excluded.value, updated_at = excluded.updated_at""",
                    (str(watermark),),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            total += len(batch)
        return total

    # ==========================================================================
    # SYSTEM RELATION OPERATIONS
    # ==========================================================================

    def create_relation(self, relation: SystemRelation) -> str:
        """Create a System Relation.

        Args:
            relation: SystemRelation to create

        Returns:
            UUID of created relation
        """
        # Convert Evidence to dict if needed
        evidence_data = None
        if relation.evidence:
            if isinstance(relation.evidence, Evidence):
                evidence_data = relation.evidence.to_dict()
            else:
                evidence_data = relation.evidence

        conn = self._get_connection()
        try:
            conn.execute(
                """INSERT INTO system_relation (uuid, kind, source, source_type, target, target_type,
                                              created_at, evidence, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    relation.uuid,
                    relation.kind,
                    relation.source,
                    relation.source_type,
                    relation.target,
                    relation.target_type,
                    relation.created_at,
                    json.dumps(evidence_data) if evidence_data else None,
                    json.dumps(relation.metadata) if relation.metadata else None,
                )
            )
        except sqlite3.IntegrityError:
            # Relation already exists (unique constraint on kind, source, target)
            # Fetch existing relation's UUID
            cursor = conn.execute(
                """SELECT uuid FROM system_relation
                   WHERE kind = ? AND source = ? AND target = ?""",
                (relation.kind, relation.source, relation.target)
            )
            row = cursor.fetchone()
            return row[0] if row else relation.uuid

        return relation.uuid

    def create_replies_to_relation(
        self,
        reply_uuid: str,
        parent_uuid: str,
        evidence: Evidence | dict | None = None,
    ) -> str | None:
        """Create a 'replies_to' system relation for email threading.

        Args:
            reply_uuid: UUID of reply Fact
            parent_uuid: UUID of parent Fact being replied to
            evidence: Optional provenance information

        Returns:
            UUID of created relation, or None if parent not found
        """
        parent = self.get_fact(parent_uuid)
        if parent is None:
            return None

        relation = SystemRelation(
            uuid=generate_soil_uuid(),
            kind="replies_to",
            source=reply_uuid,
            source_type="item",
            target=parent_uuid,
            target_type="item",
            created_at=current_day(),
            evidence=evidence,
        )
        return self.create_relation(relation)

    def get_relations(self, source: str | None = None, kind: str | None = None) -> list[SystemRelation]:
        """Get System Relations.

        Args:
            source: Filter by source UUID
            kind: Filter by relation kind

        Returns:
            List of SystemRelations
        """
        query = "SELECT * FROM system_relation WHERE 1=1"
        params = []

        if source:
            query += " AND source = ?"
            params.append(source)
        if kind:
            query += " AND kind = ?"
            params.append(kind)

        cursor = self._get_connection().execute(f"{query} ORDER BY created_at DESC", params)

        relations = []
        for row in cursor.fetchall():
            relations.append(SystemRelation(
                uuid=row["uuid"],
                kind=row["kind"],
                source=row["source"],
                source_type=row["source_type"],
                target=row["target"],
                target_type=row["target_type"],
                created_at=row["created_at"],
                evidence=json.loads(row["evidence"]) if row["evidence"] else None,
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            ))
        return relations

    # ==========================================================================
    # UTILITY METHODS
    # ==========================================================================

    def count_items(self, _type: str | None = None) -> int:
        """Count Facts.

        Args:
            _type: Filter by Fact type

        Returns:
            Count of Facts
        """
        if _type:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM item WHERE _type = ?", (_type,)
            )
        else:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM item"
            )
        return cursor.fetchone()[0]

    def count_relations(self, kind: str | None = None) -> int:
        """Count System Relations.

        Args:
            kind: Filter by relation kind

        Returns:
            Count of relations
        """
        if kind:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM system_relation WHERE kind = ?", (kind,)
            )
        else:
            cursor = self._get_connection().execute(
                "SELECT COUNT(*) FROM system_relation"
            )
        return cursor.fetchone()[0]


# ============================================================================
# CONVENIENCE FUNCTIONS
# ============================================================================

def get_soil(db_path: str | Path | None = None, init: bool = False) -> Soil:
    """Get or create Soil database.

    Database path is resolved via RFC-004:
    - db_path if provided (explicit path, backward compatible)
    - Otherwise get_db_path('soil') using environment variables

    Args:
        db_path: Path to SQLite database file. If None, resolved via
            get_db_path('soil') using environment variables.
        init: If True, initialize schema if not exists

    Returns:
        Soil instance

    Examples:
        >>> # Environment variable resolution
        >>> os.environ['MEMOGARDEN_SOIL_DB'] = '/custom/soil.db'
        >>> soil = get_soil()

        >>> # Explicit path (backward compatible)
        >>> soil = get_soil('/path/to/soil.db')

        >>> # Default (current directory)
        >>> soil = get_soil()
    """
    # Resolve database path (RFC-004)
    if db_path is None:
        from system.host.environment import get_db_path
        db_path = get_db_path('soil')

    soil = Soil(db_path)

    if init:
        # Check if already initialized
        if soil.get_schema_version() is None:
            soil.init_schema()

    return soil


def create_email_item(**kwargs) -> Fact:
    """Create an Email Fact from keyword arguments.

    Accepts structured dict with _type, data, metadata.
    Example:
        create_email_item(
            _type="Email",
            realized_at="2026-01-30T12:35:00Z",
            canonical_at="2026-01-30T12:34:56Z",
            fidelity="full",
            data={...},
            metadata={...},
        )

    Args:
        **kwargs: Email fact fields

    Returns:
        Email Fact
    """
    from datetime import datetime

    # Extract data fields
    data = kwargs.get("data", {})

    return Fact(
        uuid=generate_soil_uuid(),
        _type=kwargs.get("_type", "Email"),
        realized_at=kwargs.get("realized_at", datetime.now(UTC).isoformat()),
        canonical_at=kwargs.get("canonical_at", data.get("sent_at")),
        fidelity=kwargs.get("fidelity", "full"),
        data=data,
        metadata=kwargs.get("metadata", {}),
    )
