"""Tests for the FTS5 lexical plane (api#95 Phase 1).

item_fts is external-content FTS5 over item.data, maintained by the
item_fts_ai trigger (items are append-only: no UPDATE touches `data`, no
DELETE FROM item exists). These tests pin:

- trigger indexing on create_fact (prefix terms, implicit AND, BM25 rank)
- RFC-011 §4.9(3) pre-match scope enforcement (allowed_scopes)
- console contracts: scope_filter (must-match), live_only, types
- legacy backfill: init_schema on an existing DB records the pre-trigger
  rowid boundary; backfill_item_fts() indexes exactly those rows and is
  idempotent (a duplicate external-content insert would corrupt the index)
"""

import json
import sqlite3
from pathlib import Path

from system.host.environment import get_db_path
from system.soil import get_soil
from system.soil.database import Soil
from system.soil.fact import Fact, generate_soil_uuid


def _fact(title: str, body: str = "ledger", scopes=None, _type: str = "Note") -> str:
    with get_soil(str(get_db_path("soil"))) as soil:
        metadata = {"scopes": scopes} if scopes is not None else {}
        fact = Fact(
            uuid=generate_soil_uuid(),
            _type=_type,
            realized_at="2026-11-24T00:00:00",
            canonical_at="2026-11-24T00:00:00",
            data={"title": title, "body": body},
            metadata=metadata,
        )
        uuid = soil.create_fact(fact)
        soil.commit()
        return uuid


def _fts(query: str, **kwargs):
    with get_soil(str(get_db_path("soil"))) as soil:
        return soil.search_items_fts(query, **kwargs)


def _titles(rows) -> set[str]:
    return {json.loads(r["data"])["title"] for r in rows}


# ============================================================================
# Trigger indexing + query hardening
# ============================================================================


class TestFtsTriggerAndQueries:

    def test_new_facts_are_indexed(self):
        """create_fact fires the trigger — FTS finds it without backfill."""
        a = _fact("alpha senoko ledger")
        _fact("beta ledger")

        rows = _fts("senoko")
        assert [r["uuid"] for r in rows] == [a]
        assert rows[0]["rank"] < 0  # bm25: negative, more negative = better

    def test_prefix_terms_match_partial_words(self):
        """Hardened queries are quoted prefix terms — 'seno' finds 'Senoko'."""
        _fact("Senoko electricity bill", body="pay by Friday")

        assert len(_fts("seno")) == 1
        assert len(_fts("electric frid")) == 1  # multi-token implicit AND

    def test_multi_token_requires_all_terms(self):
        _fact("alpha senoko bill")
        _fact("beta groceries")

        hits = _fts("senoko bill")
        assert _titles(hits) == {"alpha senoko bill"}
        assert _titles(_fts("senoko groceries")) == set()

    def test_punctuation_and_empty_queries_are_safe(self):
        _fact("alpha ledger")

        assert _fts("   ") == []          # no tokens -> no MATCH at all
        assert _fts('"weird"-chars(here)') == []
        assert _fts("a-b(c):d") == []
        assert _fts("…") == []

    def test_superseded_rows_match_by_default_parity(self):
        """Parity with search_items: superseded rows are matches unless
        live_only=True (the console's live view filters them)."""
        live = _fact("gamma senoko")
        dead = _fact("delta senoko")
        with get_soil(str(get_db_path("soil"))) as soil:
            soil._get_connection().execute(
                "UPDATE item SET superseded_by = 'later' WHERE uuid = ?", (dead,))
            soil.commit()

        assert _titles(_fts("senoko")) == {"gamma senoko", "delta senoko"}
        assert _titles(_fts("senoko", live_only=True)) == {"gamma senoko"}
        assert live

    def test_types_filter(self):
        _fact("epsilon senoko", _type="Email")
        _fact("zeta senoko", _type="Note")

        rows = _fts("senoko", types=["Email"])
        assert {r["_type"] for r in rows} == {"Email"}


# ============================================================================
# RFC-011 §4.9(3) scope contracts
# ============================================================================


class TestFtsScopeContracts:

    def test_allowed_scopes_prematch_exclusion(self):
        """Scoped items outside the allowed set never become matches —
        even when they would displace a real match at LIMIT."""
        _fact("open ledger one")
        _fact("secret ledger", scopes=["scope-x"])

        rows = _fts("ledger", allowed_scopes=["scope-a"], limit=1)
        assert _titles(rows) == {"open ledger one"}

    def test_allowed_scopes_unlabeled_visible(self):
        """Unlabeled items are visible (SG-1); [] = only unlabeled."""
        _fact("plain ledger")
        _fact("labeled ledger", scopes=["scope-a"])

        assert _titles(_fts("ledger", allowed_scopes=[])) == {"plain ledger"}
        assert len(_fts("ledger", allowed_scopes=["scope-a"])) == 2

    def test_scope_filter_is_must_match(self):
        """scope_filter (console narrow-to-scopes): unlabeled items do
        NOT pass — unlike allowed_scopes."""
        _fact("in scope ledger", scopes=["scope-a"])
        _fact("unlabeled ledger")

        assert _titles(_fts("ledger", scope_filter=["scope-a"])) == {
            "in scope ledger"}

    def test_scope_uuid_refinement(self):
        a = _fact("refined ledger", scopes=["scope-a"])
        _fact("elsewhere ledger", scopes=["scope-b"])

        rows = _fts("ledger", scope_uuid="scope-a")
        assert [r["uuid"] for r in rows] == [a]


# ============================================================================
# Legacy backfill (existing DBs get the DDL via init_schema)
# ============================================================================


class TestFtsLegacyBackfill:

    def _legacy_db(self, path: Path) -> None:
        """Build a pre-FTS soil DB: base tables + version stamp + rows."""
        conn = sqlite3.connect(str(path))
        conn.executescript("""
            CREATE TABLE _schema_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO _schema_metadata VALUES ('version', '20260130', datetime('now'));
            CREATE TABLE item(
                uuid TEXT PRIMARY KEY, _type TEXT NOT NULL,
                realized_at TEXT NOT NULL, canonical_at TEXT NOT NULL,
                integrity_hash TEXT, fidelity TEXT NOT NULL DEFAULT 'full',
                superseded_by TEXT, superseded_at TEXT,
                data JSON NOT NULL, metadata JSON);
        """)
        rows = [
            ("legacy-1", json.dumps({"title": "Senoko bill November"}), None,
             json.dumps({"scopes": ["scope-a"]})),
            ("legacy-2", json.dumps({"title": "plain ledger draft"}), None, None),
            ("legacy-3", json.dumps({"title": "old Senoko quote"}), "newer-3", None),
        ]
        for u, d, sup, m in rows:
            conn.execute(
                "INSERT INTO item(uuid,_type,realized_at,canonical_at,integrity_hash,"
                "fidelity,superseded_by,superseded_at,data,metadata) "
                "VALUES (?,?,'2026-01-01T00:00:00','2026-01-01T00:00:00',"
                "NULL,'full',?,?,?,?)",
                (u, "Email", sup, None, d, m))
        conn.commit()
        conn.close()

    def test_backfill_indexes_exactly_the_legacy_rows(self, tmp_path):
        db = tmp_path / "legacy-soil.db"
        self._legacy_db(db)

        # The server-start path: full soil.sql on an existing DB — FTS table,
        # trigger, and the pre-trigger boundary stamp all land together.
        with Soil(db) as soil:
            soil.init_schema()
            conn = soil._get_connection()
            boundary = int(conn.execute(
                "SELECT value FROM _schema_metadata "
                "WHERE key = 'item_fts_created_maxrowid'").fetchone()[0])
            assert boundary == 3  # the three legacy rowids

            # A post-DDL insert is trigger-indexed immediately…
            fact = Fact(uuid=generate_soil_uuid(), _type="Note",
                        realized_at="2026-11-24T00:00:00",
                        canonical_at="2026-11-24T00:00:00",
                        data={"title": "fresh senoko note"}, metadata=None)
            soil.create_fact(fact)
            soil.commit()

            # …and the backfill must index exactly the legacy rows — no
            # overlap with trigger work. The corruption canary is FTS5's
            # own integrity check: count(*) reads the CONTENT table and
            # cannot see duplicate index entries, but 'integrity-check'
            # walks the index and raises on any inconsistency (verified:
            # it raises "database disk image is malformed" on a duplicated
            # external-content insert).
            assert soil.backfill_item_fts(batch_size=2) == 3
            conn.execute(
                "INSERT INTO item_fts(item_fts, rank) VALUES('integrity-check', 1)")
            assert conn.execute("SELECT count(*) FROM item_fts").fetchone()[0] == 4
            assert soil.backfill_item_fts() == 0  # idempotent

            hits = {json.loads(r["data"])["title"]
                    for r in soil.search_items_fts("senoko")}
            assert hits == {"Senoko bill November", "old Senoko quote",
                            "fresh senoko note"}

    def test_rebuild_marks_backfill_done(self, tmp_path):
        db = tmp_path / "legacy-soil-rebuild.db"
        self._legacy_db(db)

        with Soil(db) as soil:
            soil.init_schema()
            soil.rebuild_item_fts()
            conn = soil._get_connection()
            # Everything is indexed and consistent; a later backfill must
            # not re-insert.
            conn.execute(
                "INSERT INTO item_fts(item_fts, rank) VALUES('integrity-check', 1)")
            assert soil.backfill_item_fts() == 0
            assert len(soil.search_items_fts("senoko")) == 2


# ============================================================================
# Lifecycle sync (20261125): supersede-flip + DELETE leave the index
# ============================================================================


class TestFtsLifecycleSync:

    def test_supersede_flip_keeps_index_consistent(self):
        """mark_superseded does NOT touch the index (there is deliberately
        no AU trigger — an AU 'delete' would double-fire with the row's
        eventual DELETE and corrupt). Superseded rows keep matching by
        default (parity with fuzzy); live_only filters them."""
        victim = _fact("xylophone supersede victim", body="rare zabbit token")
        keeper = _fact("alpha ledger")

        assert _titles(_fts("zabbit")) == {"xylophone supersede victim"}

        with get_soil(str(get_db_path("soil"))) as soil:
            assert soil.mark_superseded(
                victim, keeper, "2026-11-25T00:00:00") is True

        # default parity unchanged: superseded rows still match...
        assert _titles(_fts("zabbit")) == {"xylophone supersede victim"}
        # ...and live_only excludes them (Phase 1 contract intact)
        assert _fts("zabbit", live_only=True) == []
        assert _titles(_fts("alpha")) == {"alpha ledger"}

    def test_non_supersede_update_leaves_tokens_alone(self):
        """Updates that don't flip superseded_by (e.g. lifecycle column
        writes with the flip guard) must not desync the index."""
        uuid = _fact("brontometer ledger", body="rare squale token")

        with get_soil(str(get_db_path("soil"))) as soil:
            conn = soil._get_connection()
            # a lifecycle-column write that does NOT set superseded_by
            conn.execute("UPDATE item SET superseded_at = ? WHERE uuid = ?",
                         ("2026-11-25T01:00:00", uuid))
            conn.commit()

        assert _titles(_fts("squale")) == {"brontometer ledger"}

    def test_hard_delete_leaves_index_consistent(self):
        """item_fts_ad removes deleted rows' tokens — no ghosts, and the
        index stays query-consistent after the delete (the rowid-reuse
        corruption vector)."""
        import sqlite3 as _sq
        victim = _fact("quartzling ledger", body="rare velnok token")

        assert _titles(_fts("velnok")) == {"quartzling ledger"}

        with get_soil(str(get_db_path("soil"))) as soil:
            conn = soil._get_connection()
            conn.execute("DELETE FROM item WHERE uuid = ?", (uuid := victim,))
            conn.commit()

        assert _fts("velnok") == []
        # subsequent writes + matches stay consistent (no external-content
        # desync from the delete)
        survivor = _fact("post delete ledger", body="marrowfresh token")
        assert _titles(_fts("marrowfresh")) == {"post delete ledger"}
        assert _fts("velnok") == []
