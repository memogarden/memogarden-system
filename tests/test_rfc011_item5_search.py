"""Tests for RFC-011 §4.9(3) — search PRE-MATCH at the system layer.

The system search functions (soil.search_items, core.entity.search)
exclude scoped items outside the allowed set at WHERE level, before
ORDER and LIMIT — a scoped item never becomes a match, so scoped hits
cannot displace real ones and page counts stay truthful.

allowed_scopes contract:
- None = unrestricted (flag-off / sovereign callers)
- list of plain scope uuids = unlabeled items + items with ANY label in
  the set are visible (most permissive wins, visibility addendum)
- [] = only unlabeled items (empty allowed set: no frame, no public)
"""

import json
import uuid as uuid_mod

from system.core import get_core
from system.host.environment import get_db_path
from system.soil import get_soil
from system.soil.database import Soil
from system.soil.fact import Fact


def _fact(title: str, scopes=None) -> str:
    with get_soil(str(get_db_path("soil"))) as soil:
        metadata = {"scopes": scopes} if scopes is not None else {}
        fact = Fact(
            uuid=str(uuid_mod.uuid4()),
            _type="Note",
            realized_at="2026-11-19T00:00:00",
            canonical_at="2026-11-19T00:00:00",
            data={"title": title, "body": "ledger"},
            metadata=metadata,
        )
        uuid = soil.create_fact(fact)
        soil._get_connection().commit()
        return uuid


def _entity(title: str, scopes=None) -> str:
    with get_core() as core:
        data = {"title": title, "body": "ledger"}
        if scopes is not None:
            data["scopes"] = scopes
        return core.entity.create(entity_type="Task", data=data)


def _search_facts(**kwargs):
    with get_soil(str(get_db_path("soil"))) as soil:
        rows = soil.search_items(query="ledger", **kwargs)
    return {json.loads(r["data"])["title"] for r in rows}


def _search_entities(**kwargs):
    with get_core() as core:
        rows = core.entity.search(query="ledger", **kwargs)
    return {r["data"]["title"] for r in rows}


class TestFactSearchPreMatch:
    def test_unrestricted_when_allowed_none(self):
        _fact("ledger scoped", ["s-1"])
        _fact("ledger open")
        titles = _search_facts()
        assert {"ledger scoped", "ledger open"} <= titles

    def test_allowed_set_includes_labeled_items(self):
        _fact("ledger in", ["s-1"])
        _fact("ledger out", ["s-2"])
        _fact("ledger open")
        titles = _search_facts(allowed_scopes=["s-1"])
        assert "ledger in" in titles
        assert "ledger open" in titles
        assert "ledger out" not in titles

    def test_empty_allowed_set_is_unlabeled_only(self):
        _fact("ledger labeled", ["s-1"])
        _fact("ledger open")
        titles = _search_facts(allowed_scopes=[])
        assert titles == {"ledger open"}

    def test_pre_match_displacement(self):
        """Two matches, limit 1: the scoped one (newer) must not win the
        page — pre-match means it never became a match."""
        _fact("ledger scoped-first", ["s-1"])
        _fact("ledger open-second")
        with get_soil(str(get_db_path("soil"))) as soil:
            rows = soil.search_items(
                query="ledger", limit=1, allowed_scopes=["s-2"]
            )
        titles = [json.loads(r["data"])["title"] for r in rows]
        assert titles == ["ledger open-second"]

    def test_scope_uuid_convenience_narrows(self):
        _fact("ledger alpha", ["s-1"])
        _fact("ledger beta", ["s-2"])
        titles = _search_facts(scope_uuid="s-1", allowed_scopes=["s-1", "s-2"])
        assert titles == {"ledger alpha"}

    def test_odd_scopes_shapes_do_not_crash_and_match_sensibly(self):
        """json_each over scalar/object/null scopes values must not crash
        the query. Scalar strings behave as a one-element label set (they
        match the IN clause); objects emit per-key rows that never match;
        explicit nulls match nothing and are excluded (never become
        matches under enforcement — the API never writes these shapes)."""
        with get_soil(str(get_db_path("soil"))) as soil:
            for title, metadata in (
                ("ledger scalar", {"scopes": "s-1"}),  # bare string, not array
                ("ledger obj", {"scopes": {"a": 1}}),  # object, not array
                ("ledger null", {"scopes": None}),  # explicit null
            ):
                fact = Fact(
                    uuid=str(uuid_mod.uuid4()),
                    _type="Note",
                    realized_at="2026-11-19T00:00:00",
                    canonical_at="2026-11-19T00:00:00",
                    data={"title": title, "body": "ledger"},
                    metadata=metadata,
                )
                soil.create_fact(fact)
            soil._get_connection().commit()
        _fact("ledger clean", ["s-1"])

        # Scalar label matches like an array element would.
        titles = _search_facts(allowed_scopes=["s-1"])
        assert titles == {"ledger clean", "ledger scalar"}

        titles_other = _search_facts(allowed_scopes=["s-2"])
        assert titles_other == set()

        titles_open = _search_facts(allowed_scopes=None)
        assert {"ledger scalar", "ledger obj", "ledger null", "ledger clean"} <= titles_open


class TestEntitySearchPreMatch:
    def test_allowed_set_filters_data_scopes(self):
        _entity("ledger ent-in", ["s-1"])
        _entity("ledger ent-out", ["s-2"])
        _entity("ledger ent-open")
        titles = _search_entities(allowed_scopes=["s-1"])
        assert "ledger ent-in" in titles
        assert "ledger ent-open" in titles
        assert "ledger ent-out" not in titles

    def test_empty_allowed_set_is_unlabeled_only(self):
        _entity("ledger ent-labeled", ["s-1"])
        _entity("ledger ent-open")
        titles = _search_entities(allowed_scopes=[])
        assert titles == {"ledger ent-open"}

    def test_scope_uuid_convenience_narrows(self):
        _entity("ledger ent-alpha", ["s-1"])
        _entity("ledger ent-beta", ["s-2"])
        titles = _search_entities(scope_uuid="s-1", allowed_scopes=["s-1", "s-2"])
        assert titles == {"ledger ent-alpha"}


def _supersede(old_uuid: str, new_title: str, scopes=None) -> str:
    """A relabel: the new version carries the decision, the old row
    points at it (superseded_by). Written directly — the predicate tests
    need the edge, not amend's full flow."""
    new_uuid = _fact(new_title, scopes)
    with get_soil(str(get_db_path("soil"))) as soil:
        soil._get_connection().execute(
            "UPDATE item SET superseded_by = ? WHERE uuid = ?",
            (new_uuid, old_uuid),
        )
        soil._get_connection().commit()
    return new_uuid


def _search_titles(**kwargs):
    """Fact titles through both search planes (LIKE + FTS), allowed_scopes
    and scope_uuid pass through."""
    like = _search_facts(**kwargs)
    with get_soil(str(get_db_path("soil"))) as soil:
        rows = soil.search_items_fts(query="ledger", **{
            k: v for k, v in kwargs.items() if k != "coverage"
        })
    fts = {json.loads(r["data"])["title"] for r in rows}
    return like, fts


class TestSupersededSuccessorInherit:
    """api#97 option (a): a superseded row is read-gated by its LIVE
    successor's labels — own labels are never consulted, the relabel's
    decision lives on the live version."""

    def test_unlabeled_predecessor_gated_by_successor(self):
        old = _fact("ledger relabel me")
        _supersede(old, "ledger relabeled", ["s-1"])
        like, fts = _search_titles(allowed_scopes=["s-1"])
        assert "ledger relabel me" in like and "ledger relabel me" in fts
        like, fts = _search_titles(allowed_scopes=["s-2"])
        assert "ledger relabel me" not in like
        assert "ledger relabel me" not in fts
        like, fts = _search_titles(allowed_scopes=[])
        assert "ledger relabel me" not in like
        assert "ledger relabel me" not in fts
        like, fts = _search_titles(allowed_scopes=None)
        assert "ledger relabel me" in like and "ledger relabel me" in fts

    def test_stale_own_labels_do_not_keep_a_row_visible(self):
        old = _fact("ledger stale", ["s-2"])     # labelled before the relabel
        _supersede(old, "ledger current", ["s-1"])
        like, fts = _search_titles(allowed_scopes=["s-2"])
        assert "ledger stale" not in like, "stale own label kept the row visible"
        assert "ledger stale" not in fts
        like, fts = _search_titles(allowed_scopes=["s-1"])
        assert "ledger stale" in like and "ledger stale" in fts

    def test_chain_walks_to_the_live_version(self):
        old = _fact("ledger chain head")
        mid = _supersede(old, "ledger chain mid", ["s-2"])
        _supersede(mid, "ledger chain live", ["s-1"])
        like, fts = _search_titles(allowed_scopes=["s-1"])
        assert "ledger chain head" in like and "ledger chain head" in fts
        like, fts = _search_titles(allowed_scopes=["s-2"])
        assert "ledger chain head" not in like and "ledger chain head" not in fts

    def test_successor_unlabeled_is_global_history(self):
        old = _fact("ledger unscoped later", ["s-2"])
        _supersede(old, "ledger unscoped now")   # successor carries no scopes
        like, fts = _search_titles(allowed_scopes=[])
        assert "ledger unscoped later" in like and "ledger unscoped later" in fts

    def test_dangling_successor_drops_the_row(self):
        old = _fact("ledger dangling")
        with get_soil(str(get_db_path("soil"))) as soil:
            soil._get_connection().execute(
                "UPDATE item SET superseded_by = ? WHERE uuid = ?",
                ("soil_nonexistent", old),
            )
            soil._get_connection().commit()
        like, fts = _search_titles(allowed_scopes=["s-1"])
        assert "ledger dangling" not in like
        assert "ledger dangling" not in fts

    def test_cycle_drops_the_row(self):
        a = _fact("ledger cycle a")
        b = _fact("ledger cycle b")
        with get_soil(str(get_db_path("soil"))) as soil:
            conn = soil._get_connection()
            conn.execute("UPDATE item SET superseded_by = ? WHERE uuid = ?", (b, a))
            conn.execute("UPDATE item SET superseded_by = ? WHERE uuid = ?", (a, b))
            conn.commit()
        like, fts = _search_titles(allowed_scopes=["s-1"])
        assert "ledger cycle a" not in like
        assert "ledger cycle a" not in fts

    def test_scope_refinement_inherits_for_superseded_rows(self):
        old = _fact("ledger refine me")
        _supersede(old, "ledger refined", ["s-1"])
        titles = _search_facts(scope_uuid="s-1", allowed_scopes=["s-1", "s-2"])
        assert "ledger refine me" in titles
        titles = _search_facts(scope_uuid="s-2", allowed_scopes=["s-1", "s-2"])
        assert "ledger refine me" not in titles
