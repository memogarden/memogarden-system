"""system#15 — Soil.write_txn: BEGIN IMMEDIATE write transactions.

Deferred transactions that read before writing take a snapshot, then must
UPGRADE to writer — and SQLite returns BUSY immediately on a snapshot
upgrade after another writer committed (the busy handler deliberately
never runs for that case, so busy_timeout never applies). These tests pin
the fix: write_txn acquires the write lock UP FRONT (the acquisition wait
honors busy_timeout) and keeps reads/writes consistent for the
transaction's duration.
"""

import tempfile
import threading
from pathlib import Path

import pytest

from system.soil import Fact, generate_soil_uuid
from system.soil.database import Soil

_NOW = "2026-11-28T12:00:00+00:00"


def _fact(uuid: str, marker: str = "x") -> Fact:
    return Fact(
        uuid=uuid,
        _type="Note",
        realized_at=_NOW,
        canonical_at=_NOW,
        data={"title": marker},
        metadata={"source": "test"},
        integrity_hash=None,
        fidelity="full",
    )


class TestWriteTxnBasics:
    def test_write_txn_commits(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                with soil.write_txn():
                    soil.create_fact(_fact(generate_soil_uuid()))
                count = soil._get_connection().execute(
                    "SELECT COUNT(*) FROM item").fetchone()[0]
        assert count == 1  # write_txn committed on clean exit

    def test_write_txn_rolls_back_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with Soil(Path(tmpdir) / "soil.db") as soil:
                soil.init_schema()
                with pytest.raises(RuntimeError):
                    with soil.write_txn():
                        soil.create_fact(_fact(generate_soil_uuid()))
                        raise RuntimeError("boom")
                count = soil._get_connection().execute(
                    "SELECT COUNT(*) FROM item").fetchone()[0]
        assert count == 0  # rolled back

class TestWriteTxnBurst:
    def test_eight_writers_zero_busy(self):
        """The flip-day shape: many concurrent writer threads on one DB.
        With write_txn every writer waits politely at BEGIN IMMEDIATE and
        every fact lands."""
        workers, per_worker = 8, 25
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "soil.db"
            with Soil(path) as soil:
                soil.init_schema()
            errors = []

            def worker(n: int) -> None:
                try:
                    with Soil(path) as soil:
                        for i in range(per_worker):
                            with soil.write_txn():
                                soil.create_fact(_fact(
                                    generate_soil_uuid(), f"w{n}-{i}"))
                            soil.commit()
                except Exception as e:  # noqa: BLE001 — the assertion IS the absence of errors
                    errors.append(repr(e))

            threads = [threading.Thread(target=worker, args=(n,))
                       for n in range(workers)]
            [t.start() for t in threads]
            [t.join() for t in threads]

            with Soil(path) as soil:
                count = soil._get_connection().execute(
                    "SELECT COUNT(*) FROM item").fetchone()[0]
        assert errors == []
        assert count == workers * per_worker
