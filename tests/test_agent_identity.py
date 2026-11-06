"""Tests for RFC-010 agent identity schema changes.

Covers:
- Fresh schema (core.sql) seeds at the current version with
  api_keys.agent_entity_id present
- Migration 20261105 -> 20261106 adds the column to existing databases
  without disturbing existing rows
- The 20260130 -> 20261106 chain (databases predating the providers
  tables) applies both pending migrations in order
- The Agent entity type schema is loadable via system.schemas
"""

import sqlite3

import pytest

from system.core import EXPECTED_SCHEMA_VERSION, _apply_migration, _run_migrations
from system.schemas import get_type_schema


def _base_tables_sql() -> str:
    """Table DDL shared by simulated pre-migration databases.

    Deliberately WITHOUT api_keys.agent_entity_id — that is what the
    migration under test is responsible for adding.
    """
    return """
CREATE TABLE _schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE entity (
    uuid TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    hash TEXT NOT NULL,
    previous_hash TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    group_id TEXT,
    superseded_by TEXT,
    superseded_at TEXT,
    derived_from TEXT,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT,
    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""


def _seed_version(db: sqlite3.Connection, version: str) -> None:
    db.execute(
        "INSERT INTO _schema_metadata (key, value, updated_at) VALUES ('version', ?, 'now')",
        (version,),
    )
    db.commit()


def _api_key_columns(db: sqlite3.Connection) -> set[str]:
    rows = db.execute("PRAGMA table_info(api_keys)").fetchall()
    return {row[1] for row in rows}


def _schema_version(db: sqlite3.Connection) -> str:
    row = db.execute(
        "SELECT value FROM _schema_metadata WHERE key = 'version'"
    ).fetchone()
    assert row is not None, "_schema_metadata.version missing"
    return row[0]


def test_fresh_schema_seeds_at_expected_version_with_agent_column(tmp_path):
    """A database built from canonical core.sql has the binding column
    and seeds directly at EXPECTED_SCHEMA_VERSION (no migration needed)."""
    from system.schemas import get_sql_schema

    db = sqlite3.connect(str(tmp_path / "fresh.db"))
    db.executescript(get_sql_schema("core"))

    assert _schema_version(db) == EXPECTED_SCHEMA_VERSION
    assert "agent_entity_id" in _api_key_columns(db)


def test_migration_from_20261105_adds_column_preserving_rows(tmp_path):
    """An existing 20261105 database gains the column; existing rows and
    values survive untouched."""
    db = sqlite3.connect(str(tmp_path / "old.db"))
    db.executescript(_base_tables_sql())
    _seed_version(db, "20261105")

    # An entity to bind to, and a pre-existing key with data that must survive
    db.execute(
        "INSERT INTO entity (uuid, type, hash, created_at, updated_at, data) "
        "VALUES ('core_agent_1', 'Agent', 'h', '2026-11-01', '2026-11-01', '{}')"
    )
    db.execute(
        "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, created_at) "
        "VALUES ('key_1', 'usr_1', 'gmail', 'hash', 'mg_sk_agent_x', '2026-11-01')"
    )
    db.commit()

    assert "agent_entity_id" not in _api_key_columns(db)

    _run_migrations(db)

    assert _schema_version(db) == EXPECTED_SCHEMA_VERSION
    assert "agent_entity_id" in _api_key_columns(db)

    # Existing row intact; column nullable (NULL for the unbound key)
    row = db.execute(
        "SELECT id, name, agent_entity_id FROM api_keys WHERE id = 'key_1'"
    ).fetchone()
    assert row == ("key_1", "gmail", None)


def test_migration_from_20261105_accepts_binding(tmp_path):
    """After migration the column accepts an Agent entity reference
    (RFC-010 bind path works against a migrated database)."""
    db = sqlite3.connect(str(tmp_path / "migrated.db"))
    db.executescript(_base_tables_sql())
    _seed_version(db, "20261105")
    _run_migrations(db)

    db.execute(
        "INSERT INTO entity (uuid, type, hash, created_at, updated_at, data) "
        "VALUES ('core_agent_1', 'Agent', 'h', '2026-11-01', '2026-11-01', '{}')"
    )
    db.execute(
        "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, created_at, agent_entity_id) "
        "VALUES ('key_1', 'usr_1', 'claude-code', 'hash', 'mg_sk_agent_x', '2026-11-01', 'core_agent_1')"
    )
    db.commit()

    row = db.execute(
        "SELECT agent_entity_id FROM api_keys WHERE id = 'key_1'"
    ).fetchone()
    assert row == ("core_agent_1",)


def test_migration_chain_from_20260130_applies_both_steps(tmp_path):
    """Databases predating the providers tables walk both pending
    migrations (20260130 -> 20261105 -> 20261106) in one init."""
    db = sqlite3.connect(str(tmp_path / "ancient.db"))
    db.executescript(_base_tables_sql())
    _seed_version(db, "20260130")

    _run_migrations(db)

    assert _schema_version(db) == EXPECTED_SCHEMA_VERSION
    assert "agent_entity_id" in _api_key_columns(db)

    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "providers" in tables
    assert "provider_runs" in tables


def test_agent_entity_type_schema_loads():
    """The Agent type schema is bundled and discoverable via the
    standard schema access path."""
    schema = get_type_schema("entities", "Agent")

    assert schema["title"] == "Agent"
    required = schema["allOf"][1]["properties"]["data"]["required"]
    assert set(required) == {"label", "owner"}


def test_expected_version_is_20261106():
    """Guard against accidental version regression in this branch."""
    assert EXPECTED_SCHEMA_VERSION == "20261106"
