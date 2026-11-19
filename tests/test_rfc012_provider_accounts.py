"""Tests for RFC-012 provider accounts schema changes.

Covers:
- Fresh schema (core.sql) seeds at the current version with
  provider_accounts present (UNIQUE per provider+subject, due index)
- provider_runs.account_id present ('' = provider-level) in fresh schema
- Migration 20261113 -> 20261114 adds the column/table to existing
  databases and replaces the provider-level single-flight index with the
  per-(provider, account) one
- The per-account single-flight semantics: concurrent runs of different
  accounts are allowed; the same account twice is not
"""

import sqlite3

import pytest

from system.core import EXPECTED_SCHEMA_VERSION, _run_migrations


def _schema_version(db: sqlite3.Connection) -> str:
    return db.execute(
        "SELECT value FROM _schema_metadata WHERE key = 'version'"
    ).fetchone()[0]


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def _index_columns(db: sqlite3.Connection, index: str) -> list[str]:
    return [
        row[2]
        for row in db.execute(f"PRAGMA index_info({index})").fetchall()
    ]


class TestFreshSchema:
    """Databases built from canonical core.sql carry RFC-012 structures."""

    def test_fresh_schema_seeds_at_expected_version(self, tmp_path):
        from system.schemas import get_sql_schema

        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("core"))

        assert _schema_version(db) == EXPECTED_SCHEMA_VERSION

    def test_provider_accounts_table_shape(self, tmp_path):
        from system.schemas import get_sql_schema

        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("core"))

        cols = _column_names(db, "provider_accounts")
        for col in (
            "id", "provider_name", "platform", "subject", "label",
            "status", "credential_ref", "config", "capabilities",
            "error", "interval", "next_due_at", "last_run_id",
            "last_run_at", "last_status", "last_verified_at",
            "created_at", "updated_at",
        ):
            assert col in cols, f"provider_accounts.{col} missing"

        # UNIQUE (provider_name, subject)
        uniques = [
            row[1]
            for row in db.execute("PRAGMA index_list(provider_accounts)").fetchall()
            if row[2]  # row[2] = unique flag
        ]
        unique_cols = {
            tuple(_index_columns(db, idx)) for idx in uniques
        }
        assert ("provider_name", "subject") in unique_cols

    def test_provider_runs_account_id_and_single_flight(self, tmp_path):
        from system.schemas import get_sql_schema

        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("core"))

        assert "account_id" in _column_names(db, "provider_runs")
        assert _index_columns(db, "idx_provider_runs_single_flight") == [
            "provider_name",
            "account_id",
        ]


class TestMigrationFrom20261113:
    """An existing 20261113 database gains provider_accounts and
    provider_runs.account_id; the single-flight index is replaced."""

    @pytest.fixture()
    def old_db(self, tmp_path):
        db = sqlite3.connect(str(tmp_path / "old.db"))
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE _schema_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO _schema_metadata VALUES ('version', '20261113', datetime('now'));
            -- api_keys: created by migrate_20251223_to_20251229, so every real
            -- 20261113 database has it — the chain walk to 20261119 ALTERs it.
            -- Hand-built fixtures must carry what the chain guarantees.
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                agent_entity_id TEXT REFERENCES entity(uuid),
                grants JSON NOT NULL DEFAULT '[]',
                expires_at TEXT,
                created_at TEXT NOT NULL,
                last_seen TEXT,
                revoked_at TEXT
            );
            CREATE TABLE providers (
                name TEXT PRIMARY KEY,
                module TEXT NOT NULL,
                class TEXT NOT NULL,
                interval INTEGER NOT NULL DEFAULT 3600,
                enabled INTEGER NOT NULL DEFAULT 1,
                description TEXT,
                config TEXT NOT NULL DEFAULT '{}',
                capabilities TEXT NOT NULL DEFAULT '[]',
                scope_policy TEXT,
                registered_at TEXT NOT NULL,
                last_run_id TEXT,
                last_run_at TEXT,
                last_status TEXT,
                next_due_at TEXT
            );
            CREATE TABLE provider_runs (
                id TEXT PRIMARY KEY,
                provider_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at TEXT NOT NULL,
                finished_at TEXT,
                triggered_by TEXT,
                detail TEXT
            );
            CREATE UNIQUE INDEX idx_provider_runs_single_flight
                ON provider_runs(provider_name) WHERE status = 'running';
            INSERT INTO providers (name, module, class, registered_at)
                VALUES ('gmail', 'mod', 'Cls', '2026-11-13T00:00:00Z');
            INSERT INTO provider_runs (id, provider_name, status, started_at, finished_at)
                VALUES ('run_1', 'gmail', 'success', '2026-11-13T01:00:00Z', '2026-11-13T01:05:00Z');
            """
        )
        db.commit()
        return db

    def test_version_bumped(self, old_db):
        _run_migrations(old_db)
        assert _schema_version(old_db) == EXPECTED_SCHEMA_VERSION

    def test_account_id_added_preserving_rows(self, old_db):
        _run_migrations(old_db)
        assert "account_id" in _column_names(old_db, "provider_runs")
        # Historical rows are provider-level runs: '' sentinel, not NULL
        row = old_db.execute(
            "SELECT id, account_id FROM provider_runs WHERE id = 'run_1'"
        ).fetchone()
        assert row["account_id"] == ""

    def test_provider_accounts_created(self, old_db):
        _run_migrations(old_db)
        assert "credential_ref" in _column_names(old_db, "provider_accounts")
        assert "status" in _column_names(old_db, "provider_accounts")

    def test_single_flight_index_replaced(self, old_db):
        _run_migrations(old_db)
        assert _index_columns(old_db, "idx_provider_runs_single_flight") == [
            "provider_name",
            "account_id",
        ]


class TestPerAccountSingleFlight:
    """The partial unique index admits one running run per (provider,
    account); different accounts and provider-level runs run concurrently."""

    @pytest.fixture()
    def db(self, tmp_path):
        from system.schemas import get_sql_schema

        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("core"))
        return db

    @staticmethod
    def _claim(db, run_id, provider, account_id):
        db.execute(
            "INSERT INTO provider_runs (id, provider_name, account_id, status, started_at)"
            " VALUES (?, ?, ?, 'running', '2026-11-14T00:00:00Z')",
            (run_id, provider, account_id),
        )
        db.commit()

    def test_same_account_twice_rejected(self, db):
        self._claim(db, "r1", "gmail", "acc_1")
        with pytest.raises(sqlite3.IntegrityError):
            self._claim(db, "r2", "gmail", "acc_1")

    def test_different_accounts_admitted(self, db):
        self._claim(db, "r1", "gmail", "acc_1")
        self._claim(db, "r2", "gmail", "acc_2")  # no raise

    def test_provider_level_and_account_admitted(self, db):
        self._claim(db, "r1", "gmail", "")
        self._claim(db, "r2", "gmail", "acc_1")  # no raise

    def test_different_providers_same_slot_admitted(self, db):
        self._claim(db, "r1", "gmail", "")
        self._claim(db, "r2", "tada", "")  # no raise

    def test_finished_run_releases_lock(self, db):
        self._claim(db, "r1", "gmail", "acc_1")
        db.execute(
            "UPDATE provider_runs SET status = 'success', finished_at = '2026-11-14T00:05:00Z'"
            " WHERE id = 'r1'"
        )
        db.commit()
        self._claim(db, "r2", "gmail", "acc_1")  # no raise
