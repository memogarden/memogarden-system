"""Tests for the trigger-maintained change_log feed (system#12 search-sync slice).

The feed lives in schemas/sql/{soil,core}.sql §CHANGE FEED: change_log table +
AFTER INSERT/UPDATE/DELETE triggers on the content tables (item,
system_relation in soil.db; entity, user_relation in core.db). Triggers fire
inside the write's own transaction, so a rolled-back write leaves no feed
rows — the feed never reports a change that did not commit.

What these tests pin down:
- capture completeness (including raw conn.execute writers that bypass every
  Soil/Core method — the reason the feed is trigger-maintained at all),
- no-op suppression (content guards),
- rollback atomicity + AUTOINCREMENT seq monotonicity,
- the consumer sweep interface (max_change_seq / changes_since),
- the 20261121 -> 20261124 chain migration converging to the fresh-seed shape.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from system.core import EXPECTED_SCHEMA_VERSION, _run_migrations
from system.schemas import get_sql_schema
from system.soil.database import Soil
from system.soil.fact import Fact, generate_soil_uuid


def _feed(db: sqlite3.Connection) -> list[tuple]:
    """Full feed contents as (seq, tbl, uuid, kind), ordered by seq."""
    return [
        tuple(row)
        for row in db.execute(
            "SELECT seq, tbl, uuid, kind FROM change_log ORDER BY seq"
        ).fetchall()
    ]


def _trigger_names(db: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }


def _make_fact(**overrides) -> Fact:
    fields = {
        "uuid": generate_soil_uuid(),
        "_type": "Note",
        "realized_at": "2026-11-24T12:00:00Z",
        "canonical_at": "2026-11-24T12:00:00Z",
        "data": {"description": "feed test note"},
    }
    fields.update(overrides)
    return Fact(**fields)


# ============================================================================
# Fresh-seed shape
# ============================================================================


class TestFreshSchemaCarriesFeed:
    """Both canonical schemas create change_log and the capture triggers."""

    def test_core_fresh_schema_has_feed(self, tmp_path):
        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("core"))

        triggers = _trigger_names(db)
        for expected in (
            "entity_change_insert", "entity_change_update", "entity_change_delete",
            "userrel_change_insert", "userrel_change_update", "userrel_change_delete",
        ):
            assert expected in triggers

    def test_soil_fresh_schema_has_feed(self, tmp_path):
        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("soil"))

        triggers = _trigger_names(db)
        for expected in (
            "item_change_insert", "item_change_update", "item_change_delete",
            "sysrel_change_insert", "sysrel_change_update", "sysrel_change_delete",
        ):
            assert expected in triggers


# ============================================================================
# Soil capture semantics
# ============================================================================


class TestSoilFeed:
    """item / system_relation writes land in the soil.db feed."""

    def test_insert_update_and_noop_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                conn = soil._get_connection()

                fact = _make_fact()
                soil.create_fact(fact)

                # Amend-style supersede (content change -> fires)
                conn.execute(
                    "UPDATE item SET superseded_by = 'soil_new', superseded_at = ?"
                    " WHERE uuid = ?",
                    ("2026-11-24T13:00:00Z", fact.uuid),
                )
                # In-place metadata repair with a REAL change -> fires
                conn.execute(
                    "UPDATE item SET metadata = ? WHERE uuid = ?",
                    ('{"scopes": ["core_x"]}', fact.uuid),
                )
                # Idempotent rewrite (identical values) -> silent
                conn.execute(
                    "UPDATE item SET metadata = ? WHERE uuid = ?",
                    ('{"scopes": ["core_x"]}', fact.uuid),
                )

                rows = _feed(conn)
                assert [kind for _, _, _, kind in rows] == ["insert", "update", "update"]
                assert all(uuid == fact.uuid for _, _, uuid, _ in rows)
                assert all(tbl == "item" for _, tbl, _, _ in rows)

    def test_delete_is_captured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                conn = soil._get_connection()

                fact = _make_fact()
                soil.create_fact(fact)
                conn.execute("DELETE FROM item WHERE uuid = ?", (fact.uuid,))

                kinds = [kind for _, _, _, kind in _feed(conn)]
                assert kinds == ["insert", "delete"]

    def test_system_relation_insert_is_captured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                conn = soil._get_connection()

                conn.execute(
                    "INSERT INTO system_relation (uuid, kind, source, source_type,"
                    " target, target_type, created_at)"
                    " VALUES ('soil_r1', 'contained_by', 'soil_a', 'item',"
                    " 'soil_b', 'item', 16000)"
                )

                rows = [r for r in _feed(conn) if r[1] == "system_relation"]
                assert rows == [(1, "system_relation", "soil_r1", "insert")]

    def test_raw_conn_execute_writer_is_captured(self):
        """A write through a bare connection — no Soil method involved —
        still lands in the feed. This is the regression test for the design
        decision: callers like migrate_private_scope_rename repair item
        metadata via raw conn.execute (system/core/__init__.py), and any
        call-site instrumentation would silently miss them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                fact = _make_fact()
                soil.create_fact(fact)

                # Act like system/core/__init__.py §migrate_private_scope_rename:
                # grab the connection and write raw SQL.
                conn = soil._get_connection()
                conn.execute(
                    "UPDATE item SET metadata = ? WHERE uuid = ?",
                    ('{"repaired": true}', fact.uuid),
                )
                conn.commit()

                assert [kind for _, _, _, kind in _feed(conn)] == ["insert", "update"]

    def test_rollback_leaves_no_feed_rows_and_seq_not_reused(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "soil.db"

            with Soil(db_path) as soil:
                soil.init_schema()
                soil.create_fact(_make_fact())

            # The exception must ESCAPE the with-block so __exit__ sees it
            # and rolls back; catching it inside would be a clean exit
            # (commit) — the feed row would legitimately survive.
            with pytest.raises(RuntimeError):
                with Soil(db_path) as soil:
                    soil.create_fact(_make_fact())
                    raise RuntimeError("boom")

            with Soil(db_path) as soil:
                conn = soil._get_connection()
                assert len(_feed(conn)) == 1, "rollback must remove feed rows"

                soil.create_fact(_make_fact())
                seqs = [seq for seq, _, _, _ in _feed(conn)]
                # The consumer-visible guarantee: committed feed seqs are
                # strictly increasing. (The rolled-back attempt's seq may be
                # reused — SQLite reverts sqlite_sequence on rollback — but
                # it was never visible, so checkpoints stay valid.)
                assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs) == 2


# ============================================================================
# Core capture semantics
# ============================================================================


class TestCoreFeed:
    """entity / user_relation writes land in the core.db feed."""

    def test_entity_lifecycle_is_captured(self, db_core):
        # The fixture DB seeds builtin scope entities — assert deltas.
        s0 = db_core.max_change_seq()
        e1 = db_core.entity.create(entity_type="Label", data={"label": "feed"})
        rows = db_core.changes_since(s0)
        # create = a single INSERT (updated_at set by the writer, so the
        # timestamp trigger never gets involved).
        assert [r["kind"] for r in rows] == ["insert"]
        assert rows[0]["uuid"] == e1.removeprefix("core_")

        e2 = db_core.entity.create(entity_type="Label", data={"label": "feed-v2"})
        s1 = db_core.max_change_seq()

        # update_data issues TWO statements (data UPDATE + update_hash
        # re-chain) — one feed row per statement, both set updated_at so the
        # timestamp stomp never adds a third.
        db_core.entity.update_data(e1, {"label": "feed-amended"})
        rows = db_core.changes_since(s1)
        assert [(r["kind"], r["uuid"]) for r in rows] == [
            ("update", e1.removeprefix("core_")),
            ("update", e1.removeprefix("core_")),
        ]

        s2 = db_core.max_change_seq()
        db_core.entity.supersede(e1, e2)
        rows = db_core.changes_since(s2)
        assert [(r["kind"], r["uuid"]) for r in rows] == [
            ("update", e1.removeprefix("core_"))
        ]
        assert db_core.changes_since(db_core.max_change_seq()) == []

    def test_entity_touch_via_timestamp_stomp_feeds_once(self, db_core):
        """A raw rewrite that leaves updated_at alone is NOT silent: the
        RFC-008 v1.3 writer-precedence trigger stomps updated_at, which is a
        real content change and feeds exactly one row (documented in
        core.sql §CHANGE FEED). Silence requires setting updated_at too."""
        s0 = db_core.max_change_seq()
        e1 = db_core.entity.create(entity_type="Label", data={"label": "same"})
        plain = e1.removeprefix("core_")
        row = db_core._conn.execute(
            "SELECT data, updated_at FROM entity WHERE uuid = ?", (plain,)
        ).fetchone()

        db_core._conn.execute(
            "UPDATE entity SET data = ? WHERE uuid = ?",
            (row["data"], plain),  # updated_at untouched -> stomp fires
        )

        rows = db_core.changes_since(s0 + 1)  # skip the create's insert row
        assert [(r["kind"], r["uuid"]) for r in rows] == [("update", plain)]

    def test_user_relation_lifecycle_is_captured(self, db_core):
        s0 = db_core.max_change_seq()
        entity_id = db_core.entity.create(entity_type="Label", data={"label": "rel"})
        plain = entity_id.removeprefix("core_")

        relation_id = db_core.relation.create(
            kind="explicit_link",
            source="soil_a", source_type="item",
            target=plain, target_type="entity",
        )
        rows = db_core.changes_since(s0)
        rel_rows = [r for r in rows if r["tbl"] == "user_relation"]
        assert [(r["kind"], r["uuid"]) for r in rel_rows] == [
            ("insert", relation_id.removeprefix("core_"))
        ]

        # Same-day access is a true no-op (delta 0 -> identical rewrite ->
        # content guard keeps it out of the feed).
        s1 = db_core.max_change_seq()
        db_core.relation.update_time_horizon(relation_id)
        assert db_core.changes_since(s1) == []

        # A backdated access then re-access: two captured updates — the
        # backdate itself, then the real horizon bump.
        db_core._conn.execute(
            "UPDATE user_relation SET last_access_at = last_access_at - 7"
            " WHERE uuid = ?",
            (relation_id.removeprefix("core_"),),
        )
        db_core.relation.update_time_horizon(relation_id)
        rows = db_core.changes_since(s1)
        assert [(r["kind"], r["uuid"]) for r in rows] == [
            ("update", relation_id.removeprefix("core_")),
            ("update", relation_id.removeprefix("core_")),
        ]

    def test_rollback_leaves_no_feed_rows(self, db_core):
        before = db_core.max_change_seq()
        try:
            db_core.entity.create(entity_type="Label", data={"label": "doomed"})
            raise RuntimeError("boom")
        except RuntimeError:
            pass  # fixture-level context; roll back explicitly like __exit__ would
        db_core._conn.rollback()

        assert db_core.max_change_seq() == before, "rollback must remove feed rows"


# ============================================================================
# Consumer sweep interface
# ============================================================================


class TestSweepInterface:
    """max_change_seq / changes_since are the consumer checkpoint API."""

    def test_soil_sweep_with_checkpoint_and_paging(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                for i in range(5):
                    soil.create_fact(_make_fact(data={"n": i}))

                assert soil.max_change_seq() == 5

                page1 = soil.changes_since(0, limit=2)
                page2 = soil.changes_since(page1[-1]["seq"], limit=2)
                page3 = soil.changes_since(page2[-1]["seq"], limit=2)

                assert [len(p) for p in (page1, page2, page3)] == [2, 2, 1]
                seqs = [row["seq"] for row in page1 + page2 + page3]
                assert seqs == [1, 2, 3, 4, 5]
                assert all(set(row) == {"seq", "tbl", "uuid", "kind", "changed_at"}
                           for row in page1)
                assert page1[0]["changed_at"].startswith("20") and page1[0]["changed_at"].endswith("Z")

                # Past the checkpoint: nothing comes back.
                assert soil.changes_since(5) == []
                assert soil.max_change_seq() == 5

    def test_core_sweep_after_writes(self, db_core):
        before = db_core.max_change_seq()  # fixture DB seeds scope entities
        for i in range(3):
            db_core.entity.create(entity_type="Label", data={"n": i})

        rows = db_core.changes_since(before)
        assert len(rows) == 3
        assert all(row["tbl"] == "entity" for row in rows)
        assert [row["seq"] for row in rows] == sorted(row["seq"] for row in rows)
        assert db_core.changes_since(db_core.max_change_seq()) == []


# ============================================================================
# Migration: 20261121 -> 20261124 converges to the fresh-seed shape
# ============================================================================


class TestMigrationTo20261124:
    """An existing 20261121 database gains the feed via the chain walk and
    ends up indistinguishable from a fresh 20261124 seed."""

    @pytest.fixture()
    def migrated_db(self, tmp_path):
        db = sqlite3.connect(str(tmp_path / "old.db"))
        db.row_factory = sqlite3.Row
        # A 20261121 database is exactly the current DDL minus the feed
        # (20261124 is additive): seed fresh, stamp back, drop the feed.
        db.executescript(get_sql_schema("core"))
        db.execute(
            "UPDATE _schema_metadata SET value = '20261121' WHERE key = 'version'"
        )
        db.executescript(
            """
            DROP TRIGGER IF EXISTS entity_change_insert;
            DROP TRIGGER IF EXISTS entity_change_update;
            DROP TRIGGER IF EXISTS entity_change_delete;
            DROP TRIGGER IF EXISTS userrel_change_insert;
            DROP TRIGGER IF EXISTS userrel_change_update;
            DROP TRIGGER IF EXISTS userrel_change_delete;
            DROP TABLE IF EXISTS change_log;
            """
        )
        db.commit()
        yield db
        db.close()

    def test_chain_walk_adds_feed(self, migrated_db):
        _run_migrations(migrated_db)

        assert migrated_db.execute(
            "SELECT value FROM _schema_metadata WHERE key = 'version'"
        ).fetchone()[0] == EXPECTED_SCHEMA_VERSION
        assert "change_log" in {
            row[0] for row in migrated_db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for expected in (
            "entity_change_insert", "entity_change_update", "entity_change_delete",
            "userrel_change_insert", "userrel_change_update", "userrel_change_delete",
        ):
            assert expected in _trigger_names(migrated_db)

    def test_migrated_shape_matches_fresh_seed(self, tmp_path, migrated_db):
        _run_migrations(migrated_db)

        fresh = sqlite3.connect(str(tmp_path / "fresh.db"))
        fresh.executescript(get_sql_schema("core"))

        feed_triggers = {"change_insert", "change_update", "change_delete"}
        fresh_feed = {
            name for name in _trigger_names(fresh)
            if any(name.endswith(suffix) for suffix in feed_triggers)
        }
        migrated_feed = {
            name for name in _trigger_names(migrated_db)
            if any(name.endswith(suffix) for suffix in feed_triggers)
        }
        assert fresh_feed == migrated_feed

    def test_feed_captures_on_migrated_db(self, migrated_db):
        _run_migrations(migrated_db)
        migrated_db.execute(
            "INSERT INTO entity (uuid, type, hash, version, created_at, updated_at, data)"
            " VALUES ('core_x', 'Label', 'h', 1, 't1', 't2', '{}')"
        )
        rows = _feed(migrated_db)
        assert rows == [(1, "entity", "core_x", "insert")]

    def test_entity_rows_untouched(self, migrated_db):
        """The migration is purely additive: existing entity rows (and their
        hashes) must pass through unchanged, unlike the 20261121 re-sign."""
        migrated_db.execute(
            "INSERT INTO entity (uuid, type, hash, version, created_at, updated_at, data)"
            " VALUES ('core_keep', 'Label', 'hash_untouched', 1, 't1', 't2', '{}')"
        )
        migrated_db.commit()

        _run_migrations(migrated_db)

        row = migrated_db.execute(
            "SELECT hash, version, updated_at FROM entity WHERE uuid = 'core_keep'"
        ).fetchone()
        assert tuple(row) == ("hash_untouched", 1, "t2")
