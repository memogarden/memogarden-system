"""Live-view coherence helpers for Soil supersedes chains (system#11, system#14).

Relations pin to CAPTURE-TIME versions: when a fact is amended, the new
version gets a new uuid, its relation rows stay on the old uuid, and every
relation-traversing consumer must collapse supersedes chains to the live
version before rendering or writing. That collapse used to be re-implemented
(or forgotten) per consumer — the 2026-11-22 triage sessions hit the same
landmine family across four independent features. These helpers are the
single canonical implementations; see docs/supersedes.md for the semantics.

Layering: storage stays append-only (relation rows are NEVER rewritten on
amend — that pinning is correct storage behavior and preserves provenance /
time travel). What these helpers standardize is the READ layer: collapse to
live versions by default, with the chain kept available for historical
views.
"""

from __future__ import annotations

from typing import Mapping

from utils import uid, isodatetime

from system.exceptions import (
    ConsistencyError,
    ResourceNotFound,
    ValidationError,
)
from .database import Soil
from .fact import Fact, current_day, generate_soil_uuid
from .relation import SystemRelation

# Bounded retry budget for the amend sugar: a concurrent amend landing
# between resolve and write steps trips the strict guard. Chains are
# human-scale (an item is amended a handful of times), so 5 hops is
# generous headroom; exhausting it means something is pathologically
# wrong — surface that instead of looping forever.
_MAX_AMEND_RETRIES = 5

__all__ = [
    "resolve_chain",
    "resolve_live",
    "family",
    "amend_fact",
    "amend_metadata",
    "amend_scopes",
]


# ============================================================================
# READ HELPERS — live-view resolution
# ============================================================================

def resolve_chain(start: str, sup_map: Mapping[str, str]) -> tuple[str, list[str], bool]:
    """Forward-walk a supersedes chain over a preloaded edge map (pure).

    Args:
        start: uuid to resolve, in the same prefixed form as sup_map's keys
            (the form item rows store).
        sup_map: {uuid: superseded_by} forward edges (the item.superseded_by
            column materialized).

    Returns:
        (live, chain, cycle) — live is the terminal node (== start when
        already live); chain is [start, .., live] in walk order; cycle is
        True when the walk stopped on a node it already visited (data
        corruption). live is then the node the walk stopped ON, letting
        bulk callers degrade instead of hanging.
    """
    seen: set[str] = {start}
    chain = [start]
    u = start
    while u in sup_map:
        nxt = sup_map[u]
        if nxt in seen:
            return u, chain, True
        seen.add(nxt)
        chain.append(nxt)
        u = nxt
    return u, chain, False


def _walk_live(conn, start: str) -> tuple[str, list[str], bool]:
    """Forward walk to the live version via per-hop item-column queries.

    Same walk/chain/cycle return semantics as resolve_chain; used by
    resolve_live. Covers only versions NEWER than start — family() needs
    the full closure (_version_chain) because relations pin to older
    versions too.
    """
    seen: set[str] = {start}
    chain = [start]
    u = start
    while True:
        row = conn.execute(
            "SELECT superseded_by FROM item WHERE uuid = ?", (u,)
        ).fetchone()
        if row is None or row["superseded_by"] is None:
            return u, chain, False
        nxt = row["superseded_by"]
        if nxt in seen:
            return u, chain, True
        seen.add(nxt)
        chain.append(nxt)
        u = nxt


def _version_chain(conn, start: str) -> tuple[str, list[str], bool]:
    """Full version closure of a fact: every version in its supersedes chain.

    Two data sources, one per direction:
    - BACKWARD (older versions): supersedes RELATION rows (source=new,
      target=old) — follow source=cur to target=cur until none. These rows
      are the provenance record every amend writes.
    - FORWARD (newer versions): the item.superseded_by column, same as
      _walk_live.

    Returns (live, chain_oldest_first, cycle). family() needs this because
    relations pin to capture-time versions on EITHER side of the requested
    uuid: resolving the family of a live uuid must still find relations
    pinned to its historical versions (the console opens the inspector at
    the live triage uuid, whose attachments pin to the original email
    version).

    Cycle handling mirrors resolve_chain in BOTH directions: stop before
    revisiting, flag it — the flag covers backward (relation-row) cycles
    too, and backward hops are normalized to prefixed form so legacy
    bare-target supersedes rows can't poison the chain.
    """
    seen: set[str] = {start}
    back = [start]
    cur = start
    backward_cycle = False
    while True:
        row = conn.execute(
            "SELECT target FROM system_relation "
            "WHERE kind = 'supersedes' AND source = ? LIMIT 1", (cur,)
        ).fetchone()
        if row is None:
            break
        cur = uid.add_soil_prefix(uid.strip_prefix(row["target"]))
        if cur in seen:
            backward_cycle = True
            break
        seen.add(cur)
        back.append(cur)
    chain = list(reversed(back))  # oldest .. start
    u = chain[-1]
    while True:
        row = conn.execute(
            "SELECT superseded_by FROM item WHERE uuid = ?", (u,)
        ).fetchone()
        if row is None or row["superseded_by"] is None:
            return u, chain, backward_cycle
        nxt = row["superseded_by"]
        if nxt in seen:
            return u, chain, True
        seen.add(nxt)
        chain.append(nxt)
        u = nxt


def resolve_live(soil: Soil, uuid: str, *,
                 sup_map: Mapping[str, str] | None = None) -> tuple[str, list[str]]:
    """Resolve a (possibly historical) uuid to its live version.

    Args:
        soil: open Soil context (caller manages `with get_soil()`).
        uuid: bare or soil_-prefixed uuid. core_-prefixed input is NOT
            supported (it would normalize to a fake soil_ uuid) — entity
            resolution lives in the core store, out of scope here.
        sup_map: optional preloaded edge map — skips per-hop queries when
            the caller already holds the bulk map (operator triage's cache).

    Returns:
        (live_uuid, chain) — live_uuid prefixed; chain = [start, .., live].

    Raises:
        ConsistencyError: supersedes cycle (corruption is loud for single
            lookups; bulk callers prefer resolve_chain's cycle flag).
    """
    start = uid.add_soil_prefix(uid.strip_prefix(uuid))
    if sup_map is not None:
        live, chain, cycle = resolve_chain(start, sup_map)
    else:
        live, chain, cycle = _walk_live(soil._get_connection(), start)
    if cycle:
        raise ConsistencyError(
            f"supersedes cycle detected at {start}",
            {"start": start, "chain": chain},
        )
    return live, chain


def _load_edges(conn, seeds: list[str]) -> dict[str, str]:
    """Load superseded_by forward edges for seeds and their transitive targets.

    Frontier expansion: one IN-query per hop, stopping when no new edges
    appear. Family chains are short (amends are occasional), so this stays
    a couple of small queries instead of the bulk 67k-row map the triage
    queue needs.
    """
    edges: dict[str, str] = {}
    loaded: set[str] = set()
    frontier = list(seeds)
    while frontier:
        placeholders = ",".join("?" * len(frontier))
        rows = conn.execute(
            f"SELECT uuid, superseded_by FROM item WHERE uuid IN ({placeholders})",
            frontier,
        ).fetchall()
        next_frontier: list[str] = []
        for r in rows:
            loaded.add(r["uuid"])
            if r["superseded_by"] is not None:
                edges[r["uuid"]] = r["superseded_by"]
                if r["superseded_by"] not in loaded:
                    next_frontier.append(r["superseded_by"])
        frontier = next_frontier
    return edges


def family(soil: Soil, uuid: str, *,
           kinds: tuple[str, ...] = ("contained_by", "replies_to")) -> dict:
    """Family of a fact: relations resolved across the whole supersedes chain.

    Relations pin to capture-time versions (system#11), so relation rows are
    matched against the fact's FULL version closure (historical and live —
    _version_chain) and both endpoints of every match are COLLAPSED TO LIVE
    versions before dedupe — filtering either endpoint to live-only before
    collapsing drops amended family members (the 2026-11-22 inspector bug).
    Matching the full closure also fixes opening the family from the LIVE
    uuid: the console does exactly that, and the children still pin to the
    original captured version.

    Args:
        soil: open Soil context (caller manages `with get_soil()`).
        uuid: bare or soil_-prefixed uuid of any chain version.
        kinds: relation kinds to include (default contained_by + replies_to;
            extend for future relation kinds).

    Returns:
        {"chain": [...], "parents": [...], "children": [...]} where parents/
        children entries are kind-tagged {"uuid", "kind"} pairs, live-
        collapsed and deduped (order = first-seen). Non-item endpoints
        (entities) pass through uncollapsed — entity supersedes lives in
        the core store, outside these helpers' scope.

    Raises:
        ConsistencyError: supersedes cycle in the chain (loud corruption).
    """
    if not kinds:
        raise ValidationError("family requires at least one relation kind",
                              {"kinds": list(kinds)})
    start = uid.add_soil_prefix(uid.strip_prefix(uuid))
    conn = soil._get_connection()

    live, chain, cycle = _version_chain(conn, start)
    if cycle:
        raise ConsistencyError(
            f"supersedes cycle detected at {start}",
            {"start": start, "chain": chain},
        )

    kind_ph = ",".join("?" * len(kinds))
    chain_ph = ",".join("?" * len(chain))
    # Direction convention (docs/supersedes.md): contained_by / replies_to
    # rows run member -> parent, so X's CHILDREN are rows whose TARGET is a
    # chain version of X (source = the child), and X's PARENTS are rows
    # whose SOURCE is a chain version (target = the parent).
    child_rows = conn.execute(
        f"""SELECT source, source_type, kind FROM system_relation
            WHERE kind IN ({kind_ph}) AND target IN ({chain_ph})""",
        (*kinds, *chain),
    ).fetchall()
    parent_rows = conn.execute(
        f"""SELECT target, target_type, kind FROM system_relation
            WHERE kind IN ({kind_ph}) AND source IN ({chain_ph})""",
        (*kinds, *chain),
    ).fetchall()

    # Collapse endpoints to live: bulk-load forward edges for all endpoints
    # at once (frontier expansion), then resolve each through that map.
    endpoints = [r["source"] for r in child_rows] + [r["target"] for r in parent_rows]
    edges = _load_edges(conn, endpoints)

    def _live(endpoint: str, endpoint_type: str) -> str:
        if endpoint_type != "item":
            return endpoint  # entity endpoints: pass through uncollapsed
        return resolve_chain(endpoint, edges)[0]

    parents: list[dict] = []
    children: list[dict] = []
    seen_parents: set[tuple[str, str]] = set()
    seen_children: set[tuple[str, str]] = set()
    for r in child_rows:
        key = (_live(r["source"], r["source_type"]), r["kind"])
        if key not in seen_children:
            seen_children.add(key)
            children.append({"uuid": key[0], "kind": key[1]})
    for r in parent_rows:
        key = (_live(r["target"], r["target_type"]), r["kind"])
        if key not in seen_parents:
            seen_parents.add(key)
            parents.append({"uuid": key[0], "kind": key[1]})
    return {"chain": chain, "parents": parents, "children": children}


# ============================================================================
# WRITE SUGAR — amend with live resolution
# ============================================================================

def amend_fact(soil: Soil, target: str, *, data: dict, metadata: dict | None,
               canonical_at: str | None = None, evidence: dict | None = None,
               realized_at: str | None = None) -> Fact:
    """Mechanical amend of a fact: new version + supersedes link, one place.

    The single canonical implementation of the append-only amend dance that
    api/handlers/soil.py handle_amend used to inline: create the new Fact,
    mark the original superseded (item.superseded_by column), and write the
    `supersedes` relation row (source=new, target=old). Metadata passed here
    is the FINAL metadata — merging a patch over the original is
    amend_metadata's job.

    The superseded guard stays STRICT (no silent retargeting at the core
    layer): amending a historical uuid raises instead of silently amending
    the live version. The error is trivially actionable — details carries
    superseded_by — and the amend_metadata/amend_scopes sugar resolves and
    retries automatically.

    Args:
        soil: open Soil context (caller manages `with get_soil()`).
        target: uuid of the fact to amend (bare or prefixed).
        data: replacement data. REQUIRED — AmendRequest semantics; pass the
            original through unchanged for metadata-only amendments.
        metadata: FINAL metadata for the new version (None stores none).
        canonical_at: subjective time for the new version; defaults to the
            original's (an amend relabels/fixes, it does not re-time).
        evidence: provenance for the supersedes relation; defaults to
            {"source": "user_stated", "method": "soil_amend"}.
        realized_at: amend timestamp; defaults to now.

    Returns:
        The amended (new, live) Fact.

    Raises:
        ResourceNotFound: no fact with the target uuid.
        ValidationError: target already superseded (details.superseded_by
            points at the live version — the resolve-and-retry contract).
    """
    if data is None:
        raise ValidationError(
            "amend requires data (pass the original through unchanged for "
            "metadata-only amendments)",
            {"target": target},
        )

    t = uid.add_soil_prefix(uid.strip_prefix(target))
    original = soil.get_fact(t)
    if original is None:
        raise ResourceNotFound(f"Fact not found: {t}", details={"target": t})

    if original.superseded_by is not None:
        raise ValidationError(
            "Cannot amend fact that is already superseded — resolve the "
            "live version via details.superseded_by and retry (or use the "
            "amend_metadata/amend_scopes sugar, which resolves and retries "
            "automatically)",
            {
                "fact_uuid": t,
                "superseded_by": uid.add_soil_prefix(original.superseded_by),
            },
        )

    now = realized_at or isodatetime.now()
    amended = Fact(
        uuid=generate_soil_uuid(),
        _type=original._type,
        realized_at=now,
        canonical_at=canonical_at or original.canonical_at,
        data=data,
        metadata=metadata if metadata else None,
        integrity_hash=None,  # computed by create_fact
        fidelity="full",
    )
    amended_uuid = soil.create_fact(amended)
    soil.mark_superseded(original_uuid=t, superseded_by_uuid=amended_uuid,
                         superseded_at=now)
    soil.create_relation(SystemRelation(
        uuid=generate_soil_uuid(),
        kind="supersedes",
        source=amended_uuid,
        source_type="item",
        target=t,
        target_type="item",
        created_at=current_day(),
        evidence=evidence or {"source": "user_stated", "method": "soil_amend"},
    ))
    return soil.get_fact(amended_uuid)


def amend_metadata(soil: Soil, uuid: str, patch: dict, *,
                   canonical_at: str | None = None) -> Fact:
    """Amend only the metadata of a fact's live version (resolve-and-retry).

    Resolves the supersedes chain to the live version, merges `patch` over
    the original's metadata (patch keys win), passes `data` through
    unchanged — AmendRequest requires it, and omitting it is the gotcha
    that 50/50-failed the 2026-11-22 family_reset pilot — and preserves
    canonical_at unless explicitly overridden. If a concurrent amend lands
    between resolve and write, the strict guard fires; we fold the newer
    base in and retry (bounded).

    Args:
        soil: open Soil context (caller manages `with get_soil()`).
        uuid: uuid of any version in the fact's chain (bare or prefixed).
        patch: metadata keys to overlay on the original's metadata.
        canonical_at: optional explicit subjective time for the new version.

    Returns:
        The amended (new, live) Fact.

    Raises:
        ResourceNotFound: no fact anywhere on the chain.
        ValidationError: non-supersession validation failure (e.g. missing
            data — impossible here, data passes through).
        ConsistencyError: retry budget exhausted on a racing chain.
    """
    for _attempt in range(_MAX_AMEND_RETRIES):
        live, _chain = resolve_live(soil, uuid)
        original = soil.get_fact(live)
        if original is None:
            raise ResourceNotFound(
                f"Fact not found: {live}", details={"uuid": uuid, "live": live}
            )
        merged = {**(original.metadata or {}), **patch}
        try:
            return amend_fact(soil, live, data=original.data, metadata=merged,
                              canonical_at=canonical_at)
        except ValidationError as exc:
            if not (exc.details or {}).get("superseded_by"):
                raise
            # Concurrent supersession between resolve and write: loop
            # re-resolves and re-merges over the newer base.
    raise ConsistencyError(
        f"amend_metadata retry budget exhausted for {uuid}",
        {"uuid": uid.add_soil_prefix(uid.strip_prefix(uuid))},
    )


def amend_scopes(soil: Soil, uuid: str, scopes: list, *,
                 canonical_at: str | None = None) -> Fact:
    """Amend a fact's scope labels (metadata.scopes) on its live version.

    Thin wrapper over amend_metadata writing {"scopes": list(scopes)} —
    the canonical relabel path for scripts and the SDK (RFC-015 triage
    decisions are scope writes).

    Args:
        soil: open Soil context (caller manages `with get_soil()`).
        uuid: uuid of any version in the fact's chain (bare or prefixed).
        scopes: replacement scope label list (plain uuids per DB convention).
        canonical_at: optional explicit subjective time for the new version.

    Returns:
        The amended (new, live) Fact.
    """
    return amend_metadata(soil, uuid, {"scopes": list(scopes)},
                          canonical_at=canonical_at)
