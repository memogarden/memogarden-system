"""entity_fts — the Core layer's full-text index over entity content.

Internal derived metadata: runtime-ensured on every core connection
(no migration), boundary-stamped backfill, rebuildable. These tests pin
the ensure/backfill lifecycle, the AU trigger's update-sync (the piece
item_fts deliberately lacks — entity data is mutable), supersession
interaction, and the search contract (live_only, RFC-011 scope clauses,
query hardening).
"""

from system.core import get_core
from system.core.fts import (
    _fts_match_query,
    ensure_entity_fts,
    rebuild_entity_fts,
    search_entity_fts,
)


def _make_artifact(core, label, content, scopes=None):
    data = {
        "label": label,
        "content": content,
        "content_type": "text/markdown",
        "scopes": scopes or [],
    }
    return core.entity.create(entity_type="Artifact", data=data)


def _fts_rowids(conn):
    """Indexed rowids with multiplicity — the double-insert detector."""
    return [
        row[0]
        for row in conn.execute(
            "SELECT rowid FROM entity_fts ORDER BY rowid"
        ).fetchall()
    ]


def _drop_fts(conn):
    """Strip the FTS objects and stamps (simulate a pre-index database)."""
    conn.execute("DROP TRIGGER IF EXISTS entity_fts_ai")
    conn.execute("DROP TRIGGER IF EXISTS entity_fts_au")
    conn.execute("DROP TRIGGER IF EXISTS entity_fts_ad")
    conn.execute("DROP TABLE IF EXISTS entity_fts")
    conn.execute(
        "DELETE FROM _schema_metadata WHERE key LIKE 'entity_fts%'"
    )
    conn.commit()


class TestEnsureLifecycle:
    def test_first_ensure_creates_and_second_is_a_noop(self):
        with get_core() as core:
            conn = core._conn
            _drop_fts(conn)
            created = ensure_entity_fts(conn)
            assert created is True
            objects = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE name LIKE 'entity_fts%'"
                ).fetchall()
            }
            assert {"entity_fts", "entity_fts_ai", "entity_fts_au", "entity_fts_ad"} <= objects
            assert ensure_entity_fts(conn) is False

    def test_ensure_on_open_is_automatic(self):
        # get_core -> _create_connection -> ensure_entity_fts: a plain open
        # leaves the index present without any explicit call.
        with get_core() as core:
            row = core._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'entity_fts'"
            ).fetchone()
            assert row is not None

    def test_recovery_backfills_when_ddl_exists_but_stamp_does_not(self):
        """The crash-recovery path: a crash between DDL creation and the
        backfill stamp leaves the DDL present and the flag unset — the
        next ensure must complete the backfill without double-inserting."""
        with get_core() as core:
            conn = core._conn
            _drop_fts(conn)
            ensure_entity_fts(conn)                       # creation path
            # Simulate the crash window: stamp gone, one more row added
            # (the AI trigger covers it — but the flag must still heal).
            conn.execute(
                "DELETE FROM _schema_metadata WHERE key = 'entity_fts_backfilled'"
            )
            recovered = _make_artifact(core, "Healed", "recovery window content")
            ensure_entity_fts(conn)
            rows = search_entity_fts(conn, "recovery window")
            assert [r["uuid"] for r in rows] == [recovered]
            rowids = _fts_rowids(conn)
            assert len(rowids) == len(set(rowids))
            assert conn.execute(
                "SELECT value FROM _schema_metadata WHERE key = 'entity_fts_backfilled'"
            ).fetchone()["value"] == "1"

    def test_backfill_covers_rows_older_than_the_ddl_exactly_once(self):
        with get_core() as core:
            conn = core._conn
            legacy = _make_artifact(core, "Legacy bill", "senoko electricity charges")

            # Strip the index and its stamps: the legacy row now predates
            # the DDL, exactly the shape of a database the index landed on
            # later. A row inserted now would be trigger-indexed; the
            # legacy row would not.
            _drop_fts(conn)
            ensure_entity_fts(conn)
            boundary_row = conn.execute(
                "SELECT value FROM _schema_metadata WHERE key = 'entity_fts_created_maxrowid'"
            ).fetchone()
            assert boundary_row is not None

            rows = search_entity_fts(conn, "senoko electricity")
            assert [r["uuid"] for r in rows] == [legacy]
            assert conn.execute(
                "SELECT value FROM _schema_metadata WHERE key = 'entity_fts_backfilled'"
            ).fetchone()["value"] == "1"
            # No double-insert: exactly one index entry per entity row.
            rowids = _fts_rowids(conn)
            entity_rows = conn.execute("SELECT count(*) FROM entity").fetchone()[0]
            assert len(rowids) == entity_rows
            assert len(set(rowids)) == entity_rows


class TestTriggerSync:
    def test_update_data_reindexes_the_new_content(self):
        with get_core() as core:
            conn = core._conn
            uuid = _make_artifact(core, "Bill", "electricity tariff statement october")
            assert [r["uuid"] for r in search_entity_fts(conn, "electricity tariff")] == [uuid]

            core.entity.update_data(
                uuid,
                {
                    "label": "Bill",
                    "content": "gas supply invoice november",
                    "content_type": "text/markdown",
                    "scopes": [],
                },
            )
            assert [r["uuid"] for r in search_entity_fts(conn, "gas supply")] == [uuid]
            assert search_entity_fts(conn, "electricity tariff") == []

    def test_supersede_stamps_do_not_corrupt_and_live_only_excludes(self):
        with get_core() as core:
            conn = core._conn
            old = _make_artifact(core, "Old", "parking fine notice")
            new = _make_artifact(core, "New", "parking fine notice amended")
            core.entity.supersede(old, new)

            # The supersede UPDATE fired the AU trigger with unchanged data;
            # the index must stay consistent and live_only must hide the old.
            live = search_entity_fts(conn, "parking fine", live_only=True)
            assert [r["uuid"] for r in live] == [new]
            everything = search_entity_fts(conn, "parking fine", live_only=False)
            assert {r["uuid"] for r in everything} == {old, new}

    def test_rebuild_reindexes_everything(self):
        with get_core() as core:
            conn = core._conn
            _drop_fts(conn)
            a = _make_artifact(core, "A", "water conservancy bill")  # no trigger yet
            ensure_entity_fts(conn)
            b = _make_artifact(core, "B", "town council service charge")
            rebuild_entity_fts(conn)
            for term, expected in (
                ("water conservancy", a),
                ("town council", b),
            ):
                assert [r["uuid"] for r in search_entity_fts(conn, term)] == [expected]
            rowids = _fts_rowids(conn)
            assert len(rowids) == len(set(rowids))


class TestSearchContract:
    def test_scope_masking(self):
        with get_core() as core:
            conn = core._conn
            scoped = _make_artifact(core, "Scoped", "bank statement may", scopes=["core_test-scope-a"])
            other = _make_artifact(core, "Other", "bank statement june", scopes=["core_test-scope-b"])
            free = _make_artifact(core, "Free", "bank statement july", scopes=[])

            got = {r["uuid"] for r in search_entity_fts(conn, "bank statement", allowed_scopes=["core_test-scope-a"])}
            assert got == {scoped, free}          # unlabeled always visible (SG-1)
            got = {r["uuid"] for r in search_entity_fts(conn, "bank statement", allowed_scopes=[])}
            assert got == {free}                  # only unlabeled
            got = {r["uuid"] for r in search_entity_fts(conn, "bank statement", scope_filter=["core_test-scope-b"])}
            assert got == {other}                 # filter, not enforcement: unlabeled excluded

    def test_hardening_never_raises_and_empty_gives_nothing(self):
        with get_core() as core:
            conn = core._conn
            assert _fts_match_query("   ") is None
            assert search_entity_fts(conn, "   ") == []
            # Quotes, punctuation, CJK, emoji — safe MATCH, no exception.
            for junk in ('"senoko" (bill!)', "电费 emoji🎉 OR-injection*", "a'b\"c"):
                search_entity_fts(conn, junk)


class TestCoreDelegate:
    def test_core_method_matches_module_function(self):
        with get_core() as core:
            _make_artifact(core, "Receipt", "grocery receipt march")
            via_core = core.search_entity_fts("grocery receipt", types=["Artifact"])
            direct = search_entity_fts(core._conn, "grocery receipt", types=["Artifact"])
            assert [dict(r) for r in via_core] == [dict(r) for r in direct]
            assert via_core and via_core[0]["type"] == "Artifact"
            assert via_core[0]["rank"] < 0
