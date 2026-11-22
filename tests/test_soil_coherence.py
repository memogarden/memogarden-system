"""Coherence invariants for Soil supersedes chains (system#11, system#14).

The facts the 2026-11-22 triage sessions had to (re)discover empirically,
pinned as tests:
- supersedes chains are forward walks with cycle guards;
- family() resolves capture-time-pinned relations across the whole chain
  and is stable under re-amend;
- relation directions are fixed (docs/supersedes.md is the reference);
- the amend guard is strict but machine-actionable, and the amend sugar
  resolves-and-retries transparently.
"""

import tempfile
from pathlib import Path

import pytest

from system.exceptions import ConsistencyError, ResourceNotFound, ValidationError
from system.soil import (
    Soil,
    Fact,
    SystemRelation,
    generate_soil_uuid,
    resolve_chain,
    resolve_live,
    family,
    amend_fact,
    amend_metadata,
    amend_scopes,
)
from system.soil.fact import current_day


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fact(soil, _type="Email", data=None, metadata=None,
          canonical_at="2026-11-01T09:00:00Z"):
    f = Fact(
        uuid=generate_soil_uuid(),
        _type=_type,
        realized_at="2026-11-01T10:00:00Z",
        canonical_at=canonical_at,
        data=data if data is not None else {"subject": "t"},
        metadata=metadata,
    )
    soil.create_fact(f)
    return f


def _rel(soil, kind, source, target, source_type="item", target_type="item"):
    conn = soil._get_connection()
    conn.execute(
        "INSERT INTO system_relation (uuid, kind, source, source_type, "
        "target, target_type, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (generate_soil_uuid(), kind, source, source_type, target,
         target_type, current_day()),
    )
    conn.commit()


def _sup_map(soil):
    rows = soil._get_connection().execute(
        "SELECT uuid, superseded_by FROM item WHERE superseded_by IS NOT NULL"
    ).fetchall()
    return {r["uuid"]: r["superseded_by"] for r in rows}


@pytest.fixture()
def soil():
    with tempfile.TemporaryDirectory() as tmpdir:
        with Soil(Path(tmpdir) / "test.db") as s:
            s.init_schema()
            yield s


class TestResolveChain:
    """resolve_chain: pure forward-walk over a preloaded edge map."""

    def test_terminal_when_no_edges(self):
        assert resolve_chain("soil_a", {}) == ("soil_a", ["soil_a"], False)

    def test_single_hop(self):
        assert resolve_chain("soil_a", {"soil_a": "soil_b"}) == \
            ("soil_b", ["soil_a", "soil_b"], False)

    def test_multi_hop(self):
        sup_map = {"soil_a": "soil_b", "soil_b": "soil_c"}
        assert resolve_chain("soil_a", sup_map) == \
            ("soil_c", ["soil_a", "soil_b", "soil_c"], False)

    def test_two_node_cycle_flagged_not_hung(self):
        sup_map = {"soil_a": "soil_b", "soil_b": "soil_a"}
        live, chain, cycle = resolve_chain("soil_a", sup_map)
        assert cycle is True
        assert chain == ["soil_a", "soil_b"]

    def test_self_cycle_flagged(self):
        live, chain, cycle = resolve_chain("soil_a", {"soil_a": "soil_a"})
        assert cycle is True
        assert chain == ["soil_a"]


class TestResolveLive:
    """resolve_live: per-hop (or bulk-map) resolution against a Soil db."""

    def test_live_item_passes_through(self, soil):
        f = _fact(soil)
        assert resolve_live(soil, f.uuid) == (f.uuid, [f.uuid])

    def test_historical_resolves_forward(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={"relabeled": True})
        live, chain = resolve_live(soil, v1.uuid)
        assert live == v2.uuid
        assert chain == [v1.uuid, v2.uuid]

    def test_bare_input_returns_prefixed(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        live, _chain = resolve_live(soil, v1.uuid.removeprefix("soil_"))
        assert live == v2.uuid

    def test_unknown_uuid_passes_through(self, soil):
        ghost = "soil_11111111-1111-4111-8111-111111111111"
        assert resolve_live(soil, ghost) == (ghost, [ghost])

    def test_bulk_map_matches_per_hop(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        v3 = amend_fact(soil, v2.uuid, data=v2.data, metadata={})
        by_hop = resolve_live(soil, v1.uuid)
        by_map = resolve_live(soil, v1.uuid, sup_map=_sup_map(soil))
        assert by_hop == by_map == (v3.uuid, [v1.uuid, v2.uuid, v3.uuid])

    def test_cycle_raises_consistency_error(self, soil):
        a = _fact(soil)
        b = _fact(soil)
        # corrupt the store directly: a -> b -> a
        soil.mark_superseded(a.uuid, b.uuid, "2026-11-02T00:00:00Z")
        soil.mark_superseded(b.uuid, a.uuid, "2026-11-02T00:00:00Z")
        with pytest.raises(ConsistencyError) as exc:
            resolve_live(soil, a.uuid)
        assert exc.value.details["start"] == a.uuid


class TestFamily:
    """family(): relations resolved across the whole chain, collapsed live.

    Direction convention pinned here (docs/supersedes.md is the reference):
    contained_by / replies_to rows run member -> parent, and supersedes
    rows run new -> old.
    """

    def test_children_and_parents_by_direction(self, soil):
        parent = _fact(soil)
        child = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", child.uuid, parent.uuid)
        fam_parent = family(soil, parent.uuid)
        assert fam_parent["children"] == [{"uuid": child.uuid, "kind": "contained_by"}]
        assert fam_parent["parents"] == []
        fam_child = family(soil, child.uuid)
        assert fam_child["parents"] == [{"uuid": parent.uuid, "kind": "contained_by"}]
        assert fam_child["children"] == []

    def test_replies_to_kind(self, soil):
        parent = _fact(soil)
        reply = _fact(soil, _type="Message", data={"description": "re"})
        _rel(soil, "replies_to", reply.uuid, parent.uuid)
        assert family(soil, parent.uuid)["children"] == \
            [{"uuid": reply.uuid, "kind": "replies_to"}]
        assert family(soil, reply.uuid)["parents"] == \
            [{"uuid": parent.uuid, "kind": "replies_to"}]

    def test_parent_pin_resolves_across_parent_chain(self, soil):
        # Attachment captured against email v1; email later amended to v2.
        # The relation row STAYS on v1 (capture-time pinning) but family()
        # must resolve it onto the live v2 from either version's viewpoint.
        v1 = _fact(soil)
        child = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", child.uuid, v1.uuid)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={"scopes": ["s"]})
        assert family(soil, v2.uuid)["children"] == \
            [{"uuid": child.uuid, "kind": "contained_by"}]
        assert family(soil, v1.uuid)["children"] == \
            [{"uuid": child.uuid, "kind": "contained_by"}]
        assert family(soil, child.uuid)["parents"] == \
            [{"uuid": v2.uuid, "kind": "contained_by"}]

    def test_child_amend_collapses_to_live_child(self, soil):
        parent = _fact(soil)
        c1 = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", c1.uuid, parent.uuid)
        c2 = amend_fact(soil, c1.uuid, data=c1.data, metadata={"scopes": ["s"]})
        assert family(soil, parent.uuid)["children"] == \
            [{"uuid": c2.uuid, "kind": "contained_by"}]

    def test_dedupe_same_pair_across_chain_versions(self, soil):
        # The same child linked to TWO versions of the parent (e.g. a
        # re-link after amend) must render once per (uuid, kind).
        v1 = _fact(soil)
        child = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", child.uuid, v1.uuid)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        _rel(soil, "contained_by", child.uuid, v2.uuid)
        assert family(soil, v2.uuid)["children"] == \
            [{"uuid": child.uuid, "kind": "contained_by"}]

    def test_entity_endpoint_passes_through(self, soil):
        child = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", child.uuid, "core_abc", target_type="entity")
        assert family(soil, child.uuid)["parents"] == \
            [{"uuid": "core_abc", "kind": "contained_by"}]

    def test_stable_under_child_re_amend(self, soil):
        parent = _fact(soil)
        c1 = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", c1.uuid, parent.uuid)
        c2 = amend_fact(soil, c1.uuid, data=c1.data, metadata={"scopes": ["s1"]})
        c3 = amend_fact(soil, c2.uuid, data=c2.data, metadata={"scopes": ["s2"]})
        assert family(soil, parent.uuid)["children"] == \
            [{"uuid": c3.uuid, "kind": "contained_by"}]

    def test_unknown_uuid_family_is_empty(self, soil):
        ghost = "soil_22222222-2222-4222-8222-222222222222"
        fam = family(soil, ghost)
        assert fam == {"chain": [ghost], "parents": [], "children": []}

    def test_chain_reports_full_walk(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        v3 = amend_fact(soil, v2.uuid, data=v2.data, metadata={})
        assert family(soil, v1.uuid)["chain"] == [v1.uuid, v2.uuid, v3.uuid]


class TestAmendFact:
    """amend_fact: the mechanical amend + strict guard + direction pins."""

    def test_creates_new_version_with_supersedes_relation(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data={"subject": "fixed"},
                        metadata={"scopes": ["s"]})
        assert v2.uuid != v1.uuid
        assert v2._type == v1._type
        rows = soil._get_connection().execute(
            "SELECT source, target FROM system_relation WHERE kind = 'supersedes'"
        ).fetchall()
        # DIRECTION PIN (probed empirically 2026-11-22): source=new, target=old.
        assert [(r["source"], r["target"]) for r in rows] == [(v2.uuid, v1.uuid)]

    def test_marks_original_superseded(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        after = soil.get_fact(v1.uuid)
        assert after.superseded_by == v2.uuid
        assert after.superseded_at is not None
        assert soil.get_fact(v2.uuid).superseded_by is None

    def test_guard_raises_with_actionable_details(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        with pytest.raises(ValidationError) as exc:
            amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        assert exc.value.details["superseded_by"] == v2.uuid
        assert exc.value.details["fact_uuid"] == v1.uuid

    def test_canonical_at_defaults_to_original(self, soil):
        v1 = _fact(soil, canonical_at="2026-11-01T09:00:00Z")
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        assert v2.canonical_at == "2026-11-01T09:00:00Z"
        v3 = amend_fact(soil, v2.uuid, data=v2.data, metadata={},
                        canonical_at="2026-11-05T08:00:00Z")
        assert v3.canonical_at == "2026-11-05T08:00:00Z"

    def test_metadata_is_final_not_merged(self, soil):
        v1 = _fact(soil, metadata={"keep_me": 1})
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={"only": 2})
        assert v2.metadata == {"only": 2}

    def test_missing_fact_raises_not_found(self, soil):
        ghost = "soil_33333333-3333-4333-8333-333333333333"
        with pytest.raises(ResourceNotFound):
            amend_fact(soil, ghost, data={}, metadata=None)

    def test_missing_data_raises(self, soil):
        v1 = _fact(soil)
        with pytest.raises(ValidationError):
            amend_fact(soil, v1.uuid, data=None, metadata=None)

    def test_evidence_recorded(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={},
                        evidence={"source": "user_stated", "method": "cli"})
        row = soil._get_connection().execute(
            "SELECT evidence FROM system_relation WHERE kind = 'supersedes' "
            "AND source = ?", (v2.uuid,)
        ).fetchone()
        assert '"cli"' in row["evidence"]


class TestAmendSugar:
    """amend_metadata / amend_scopes: resolve-and-retry write sugar."""

    def test_merges_patch_over_original(self, soil):
        v1 = _fact(soil, metadata={"account": "gmail", "scopes": ["s1"]})
        v2 = amend_metadata(soil, v1.uuid, {"note": "added"})
        assert v2.metadata["account"] == "gmail"
        assert v2.metadata["scopes"] == ["s1"]
        assert v2.metadata["note"] == "added"

    def test_patch_wins_on_conflict(self, soil):
        v1 = _fact(soil, metadata={"label": "old"})
        v2 = amend_metadata(soil, v1.uuid, {"label": "new"})
        assert v2.metadata["label"] == "new"

    def test_data_passthrough_unchanged(self, soil):
        payload = {"subject": "same", "body": "identical"}
        v1 = _fact(soil, data=payload)
        v2 = amend_metadata(soil, v1.uuid, {"note": "x"})
        assert v2.data == payload
        assert v2.integrity_hash == v1.integrity_hash

    def test_canonical_at_preserved_by_default(self, soil):
        v1 = _fact(soil, canonical_at="2026-11-01T09:00:00Z")
        v2 = amend_metadata(soil, v1.uuid, {"note": "x"})
        assert v2.canonical_at == "2026-11-01T09:00:00Z"

    def test_resolves_superseded_target(self, soil):
        v1 = _fact(soil, metadata={"scopes": ["s1"]})
        v2 = amend_fact(soil, v1.uuid, data=v1.data,
                        metadata={"scopes": ["s2"], "via": "direct"})
        # historical uuid is fine for the sugar: resolves to v2, merges over
        # v2's metadata
        v3 = amend_metadata(soil, v1.uuid, {"note": "sugared"})
        assert v3.metadata["scopes"] == ["s2"]
        assert v3.metadata["via"] == "direct"
        assert v3.metadata["note"] == "sugared"
        live, chain = resolve_live(soil, v1.uuid)
        assert live == v3.uuid
        assert chain == [v1.uuid, v2.uuid, v3.uuid]

    def test_amend_scopes_replaces_scopes_keeps_rest(self, soil):
        v1 = _fact(soil, metadata={"scopes": ["s1"], "account": "gmail"})
        v2 = amend_scopes(soil, v1.uuid, ["s2", "s3"])
        assert v2.metadata["scopes"] == ["s2", "s3"]
        assert v2.metadata["account"] == "gmail"

    def test_amend_scopes_accepts_bare_uuid(self, soil):
        v1 = _fact(soil, metadata={"scopes": []})
        v2 = amend_scopes(soil, v1.uuid.removeprefix("soil_"), ["s1"])
        assert v2.metadata["scopes"] == ["s1"]

    def test_missing_fact_raises_not_found(self, soil):
        ghost = "soil_44444444-4444-4444-8444-444444444444"
        with pytest.raises(ResourceNotFound):
            amend_metadata(soil, ghost, {"note": "x"})


class TestFamilyFromLiveUuid:
    """The console opens the family at the LIVE uuid (triage queue tap).

    Children pin to the ORIGINAL captured version, so a forward-only walk
    from the live uuid sees an empty family — the full version closure
    (_version_chain: backward via supersedes relations, forward via the
    superseded_by column) is what makes family(live) complete. Regression
    kit for the inspector's shipped blind spot (system#14).
    """

    def test_live_uuid_finds_capture_time_pinned_children(self, soil):
        v1 = _fact(soil)
        child = _fact(soil, _type="Attachment", data={"filename": "a.pdf"})
        _rel(soil, "contained_by", child.uuid, v1.uuid)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={"scopes": ["s"]})
        v3 = amend_fact(soil, v2.uuid, data=v2.data, metadata={"scopes": ["s2"]})
        # from the LIVE uuid — the forward-only walk missed this
        assert family(soil, v3.uuid)["children"] == \
            [{"uuid": child.uuid, "kind": "contained_by"}]
        # from any historical uuid — same answer
        assert family(soil, v1.uuid)["children"] == \
            [{"uuid": child.uuid, "kind": "contained_by"}]
        assert family(soil, v2.uuid)["children"] == \
            [{"uuid": child.uuid, "kind": "contained_by"}]

    def test_live_uuid_reports_full_chain_oldest_first(self, soil):
        v1 = _fact(soil)
        v2 = amend_fact(soil, v1.uuid, data=v1.data, metadata={})
        v3 = amend_fact(soil, v2.uuid, data=v2.data, metadata={})
        fam = family(soil, v3.uuid)
        assert fam["chain"] == [v1.uuid, v2.uuid, v3.uuid]
