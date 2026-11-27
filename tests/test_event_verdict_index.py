"""system#5 — event verdict identity (partial UNIQUE index on soil item).

The review verdicts (mark-occurred / dismiss) land Event facts carrying
data.event_uuid; "one verdict per live event entity" is now a DB
constraint rather than only the process-local _MARK_LOCK + in-transaction
re-probe (api/v1/events.py). Without the index, a cross-process
double-submit silently duplicates the verdict — two facts, no error, found
only by reconciliation. With it, the second insert fails at the DB with
IntegrityError (which the API layer translates into the idempotent
response).

Covers: fresh-seed presence, double-insert fails at the DB (the system#5
acceptance shape), tada-completion facts (which carry no event_uuid)
unaffected, superseding frees the identity for a new live verdict, and an
existing DB converging at the next boot via the executescript mechanism.
"""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from system.soil import Fact, generate_soil_uuid
from system.soil.database import Soil

_NOW = "2026-11-27T12:00:00+00:00"


def _verdict_fact(uuid: str, event_uuid: str) -> Fact:
    """A review verdict: the flat Event fact with the entity link."""
    return Fact(
        uuid=uuid,
        _type="Event",
        realized_at=_NOW,
        canonical_at=_NOW,
        data={
            "event_uuid": event_uuid,
            "source_id": "evt_1",
            "kind": "event",
        },
        metadata={"source": "google_calendar", "status": "occurred"},
        integrity_hash=None,
        fidelity="full",
    )


def _tada_fact(uuid: str) -> Fact:
    """A tada completion: no event_uuid (the Task is retired at conversion;
    native source_id is the stable join) — must stay OUTSIDE the index."""
    return Fact(
        uuid=uuid,
        _type="Event",
        realized_at=_NOW,
        canonical_at=_NOW,
        data={"source_id": "tada_1", "kind": "task", "title": "buy milk"},
        metadata={"source": "tada", "status": "completed"},
        integrity_hash=None,
        fidelity="full",
    )


def test_fresh_seed_carries_the_index():
    with tempfile.TemporaryDirectory() as tmpdir:
        with Soil(Path(tmpdir) / "soil.db") as soil:
            soil.init_schema()
            names = [
                row[0]
                for row in soil._get_connection().execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='item'"
                )
            ]
    assert "idx_event_verdict_identity" in names


def test_double_verdict_fails_at_db():
    """The system#5 acceptance shape: the same live event_uuid twice → the
    second insert fails with IntegrityError (per-process locks cannot
    give this guarantee)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with Soil(Path(tmpdir) / "soil.db") as soil:
            soil.init_schema()
            soil.create_fact(_verdict_fact(generate_soil_uuid(), "ent_a"))
            with pytest.raises(sqlite3.IntegrityError):
                soil.create_fact(
                    _verdict_fact(generate_soil_uuid(), "ent_a"))


def test_distinct_verdicts_coexist():
    with tempfile.TemporaryDirectory() as tmpdir:
        with Soil(Path(tmpdir) / "soil.db") as soil:
            soil.init_schema()
            soil.create_fact(_verdict_fact(generate_soil_uuid(), "ent_a"))
            soil.create_fact(_verdict_fact(generate_soil_uuid(), "ent_b"))
            count = soil._get_connection().execute(
                "SELECT COUNT(*) FROM item WHERE _type='Event' "
                "AND json_extract(data, '$.event_uuid') IS NOT NULL"
            ).fetchone()[0]
    assert count == 2


def test_tada_completions_outside_the_index():
    """tada completions carry no event_uuid — many coexist (the IS NOT NULL
    guard keeps them out of the index)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with Soil(Path(tmpdir) / "soil.db") as soil:
            soil.init_schema()
            for _ in range(3):
                soil.create_fact(_tada_fact(generate_soil_uuid()))
            count = soil._get_connection().execute(
                "SELECT COUNT(*) FROM item WHERE _type='Event'"
            ).fetchone()[0]
    assert count == 3


def test_supersede_frees_the_identity():
    """A superseded verdict no longer holds the identity — the live chain
    carries exactly one live verdict for the event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with Soil(Path(tmpdir) / "soil.db") as soil:
            soil.init_schema()
            v1 = generate_soil_uuid()
            soil.create_fact(_verdict_fact(v1, "ent_a"))
            conn = soil._get_connection()
            conn.execute(
                "UPDATE item SET superseded_by = ? WHERE uuid = ?",
                (generate_soil_uuid(), v1),
            )
            soil.create_fact(_verdict_fact(generate_soil_uuid(), "ent_a"))
            conn.commit()


def test_existing_db_converges_at_boot():
    """The deploy mechanism: init_system calls Soil.init_schema()
    unconditionally at every boot (executescript, all IF NOT EXISTS) —
    an existing DB without the index converges at the next restart."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "pre-index.db"

        with Soil(path) as soil:
            soil.init_schema()
            conn = soil._get_connection()
            conn.execute("DROP INDEX idx_event_verdict_identity")
            conn.commit()

        with Soil(path) as soil:  # init=False, then the boot call
            soil.init_schema()
            names = [
                row[0]
                for row in soil._get_connection().execute(
                    "SELECT name FROM sqlite_master WHERE type='index' "
                    "AND tbl_name='item'"
                )
            ]
    assert "idx_event_verdict_identity" in names
