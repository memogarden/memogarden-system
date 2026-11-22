# Soil supersedes semantics — capture-time pinning, live-view resolution

system#11 (2026-11-20) made facts append-only under amend: amending a fact
creates a NEW fact with a new uuid, marks the original (`item.superseded_by`
column), and writes a `supersedes` relation row. **Relation rows pin to
capture-time versions** — they are never rewritten. system#14 (2026-11-23)
extracted the consequences into shared helpers
(`system/soil/coherence.py`) after four independent consumers hit the same
landmine family in one session.

## What this means for consumers

1. **A uuid is a version, not an identity.** An amended email's attachments
   still point at the version they were captured for. Any uuid may be
   historical (superseded).
2. **Every relation-traversing read must collapse supersedes chains to live
   versions by default.** Filtering `superseded_by IS NULL` BEFORE
   collapsing silently drops amended family members — collapse first, then
   filter (the 2026-11-22 inspector "No attachments" bug, api `5618f09`).
3. **Family reads need the FULL version closure, both directions.** From a
   live uuid, children still pin to the original captured version; from a
   historical uuid, later versions exist. Forward-only walks miss half.
   Use `family()`, which walks backward via supersedes relation rows and
   forward via the `superseded_by` column.
4. **Caches keyed by uuid go stale on amend.** Labels/sidecars must be
   re-read live at every rebuild (a stale anchor briefly recommended
   Household for 193 template siblings on 2026-11-22).

## Relation direction convention

| kind          | direction             | meaning                       |
|---------------|-----------------------|-------------------------------|
| `supersedes`  | source=new, target=old | new version → replaced version |
| `contained_by`| source=member, target=container | child → parent |
| `replies_to`  | source=reply, target=parent | message → thread parent |

`supersedes` direction was probed empirically against production data on
2026-11-22 before writing the inspector's forward walk; it is pinned by
`tests/test_soil_coherence.py::TestAmendFact::test_creates_new_version_with_supersedes_relation`.
Other kinds (`triggers`, `cites`, `derives_from`, `contains`, `continues`,
`result_of`) exist in `relation.py` but have no family semantics yet —
document direction here when introducing one.

## Helper reference (`system/soil/coherence.py`)

Read layer (collapse to live by default; storage untouched):

- **`resolve_chain(start, sup_map) -> (live, chain, cycle)`** — pure
  forward-walk over a preloaded `{uuid: superseded_by}` map. Bulk callers
  (operator triage's cached 67k-row map) use this; a cycle returns with
  `cycle=True` so callers can degrade instead of hanging.
- **`resolve_live(soil, uuid, sup_map=None) -> (live, chain)`** — resolve
  any version to live via per-hop queries (or the optional bulk map).
  Raises `ConsistencyError` on a cycle: corruption is loud for single
  lookups. Accepts bare or `soil_`-prefixed input; returns prefixed.
- **`family(soil, uuid, kinds=("contained_by","replies_to"))`** —
  `{"chain", "parents", "children"}` with kind-tagged, live-collapsed,
  deduped entries, resolved across the full version closure. Non-item
  (entity) endpoints pass through uncollapsed. Raises `ConsistencyError`
  on a cycle.

Write sugar (the strict core guard, made convenient at the edges):

- **`amend_fact(soil, target, *, data, metadata, canonical_at=None,
  evidence=None, realized_at=None) -> Fact`** — the mechanical amend in one
  place (create new Fact + `mark_superseded` + supersedes relation; uuids
  stored normalized-prefixed). `metadata` is the FINAL metadata. The guard
  is STRICT: amending a historical uuid raises `ValidationError` — no
  silent retargeting at the core layer — but the error is machine-actionable:
  `details.superseded_by` names the live version.
- **`amend_metadata(soil, uuid, patch, canonical_at=None) -> Fact`** —
  resolve live → merge patch over the original's metadata → **pass data
  through unchanged** (AmendRequest requires it; omitting it is the gotcha
  that 50/50-failed the 2026-11-22 family_reset pilot) → keep canonical_at
  unless overridden. Retries (bounded) if a concurrent amend lands between
  resolve and write.
- **`amend_scopes(soil, uuid, scopes, canonical_at=None) -> Fact`** — the
  canonical relabel path (triage decisions are scope writes).

## Resolve-and-retry contract (for API/SDK callers)

`POST /mg` with `op=amend` on a historical uuid returns 400
`Cannot amend fact that is already superseded` with
`details.superseded_by` = the live uuid. **The contract: retarget to
`details.superseded_by` and retry** (repeat while 400s carry a new
`superseded_by`; chains are short). The SDK's
`amend_metadata`/`amend_scopes` do this automatically. The strict guard
itself stays — do not add silent retargeting at the core layer
(system#14 Q1).

## Design note: forwarding relations (deferred)

On amend, one could also write *forwarding* relation rows targeting the new
version, making live-graph queries collapse-free. Deferred (system#14 Q2):
relation-row growth scales with amend frequency across attachment-heavy
email + relabel churn, and consumers that don't dedupe by resolved uuid
double-count. At 68k relations the collapse-with-cache pattern is already
sub-millisecond. Revisit only if a hot path measurably needs it.
