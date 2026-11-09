"""Tests for RFC-011 / RFC-003 v5: exclusive scope activation + grants schema.

Covers:
- ContextFrame singular active_scope: enter/leave/switch semantics (INV-11a)
- Idempotent re-enter, mismatched-leave rejection, focus as no-op alias
- Builtin scope provisioning (triage, private) — idempotent (RFC-011 §4.1)
- The 20261106 -> 20261109 migration fold rule (live-data safety)
"""

import json
import sqlite3

import pytest

from system.core import (
    _apply_migration,
    ensure_builtin_scopes,
    get_core,
)
from system.exceptions import ValidationError


class TestExclusiveActivation:
    """RFC-003 v5 INV-11a: one active scope per principal."""

    def test_fresh_frame_is_unscoped(self, db_core):
        """New ContextFrames start unscoped (RFC-011 SG-1: global default)."""
        frame = db_core.context.get_context_frame("user-rfc011-a", "operator")
        assert frame.active_scope is None
        assert frame.to_dict()["active_scope"] is None

    def test_enter_sets_active_scope(self, db_core):
        frame = db_core.context.get_context_frame("user-rfc011-b", "operator")
        frame = db_core.context.enter_scope(frame, "scopeA")
        assert frame.active_scope == "scopeA"

    def test_reenter_same_scope_is_idempotent(self, db_core):
        """Entering the already-active scope is a no-op (RFC-011 §4.4)."""
        frame = db_core.context.get_context_frame("user-rfc011-c", "operator")
        frame = db_core.context.enter_scope(frame, "scopeA")
        frame = db_core.context.enter_scope(frame, "scopeA")
        assert frame.active_scope == "scopeA"

    def test_enter_while_inside_is_atomic_switch(self, db_core):
        """INV-11a: no co-activation — second enter replaces the first."""
        frame = db_core.context.get_context_frame("user-rfc011-d", "operator")
        frame = db_core.context.enter_scope(frame, "scopeA")
        previous = frame.active_scope
        frame = db_core.context.enter_scope(frame, "scopeB")
        assert previous == "scopeA"
        assert frame.active_scope == "scopeB"

    def test_leave_clears_active_scope(self, db_core):
        frame = db_core.context.get_context_frame("user-rfc011-e", "operator")
        frame = db_core.context.enter_scope(frame, "scopeA")
        frame = db_core.context.leave_scope(frame, "scopeA")
        assert frame.active_scope is None

    def test_leave_mismatched_scope_raises(self, db_core):
        """Leaving a scope that is not active is an error (SG-3)."""
        frame = db_core.context.get_context_frame("user-rfc011-f", "operator")
        frame = db_core.context.enter_scope(frame, "scopeA")
        with pytest.raises(ValidationError):
            db_core.context.leave_scope(frame, "scopeB")

    def test_enter_on_agent_frame_raises(self, db_core):
        """v1: activation lives on operator frames only."""
        frame = db_core.context.get_context_frame("agent-rfc011", "agent")
        with pytest.raises(ValidationError):
            db_core.context.enter_scope(frame, "scopeA")

    def test_focus_is_noop_alias(self, db_core):
        """`focus` is retired (RFC-003 v5): returns frame unchanged, no state."""
        frame = db_core.context.get_context_frame("user-rfc011-g", "operator")
        frame = db_core.context.enter_scope(frame, "scopeA")
        before = frame.to_dict()
        result = db_core.context.focus_scope(frame, "scopeB")
        assert result.active_scope == "scopeA"
        assert result.to_dict() == before


class TestBuiltinScopes:
    """RFC-011 §4.1: triage and private provisioned idempotently.

    Uses its own get_core() connections (not the db_core fixture, which
    rolls back) so the seeded entities persist within the module run.
    """

    def test_provision_creates_triage_and_private(self):
        created = ensure_builtin_scopes()
        assert len(created) == 2

    def test_provision_is_idempotent(self):
        ensure_builtin_scopes()
        again = ensure_builtin_scopes()
        assert again == []

    def test_builtin_scopes_are_scope_entities(self):
        ensure_builtin_scopes()
        with get_core() as core:
            rows = core._conn.execute(
                "SELECT data FROM entity WHERE type = 'Scope' "
                "AND json_extract(data, '$.label') IN "
                "('Triage / Untrusted', 'Private')"
            ).fetchall()
            labels = {json.loads(row["data"])["label"] for row in rows}
            assert labels == {"Triage / Untrusted", "Private"}


class TestMigration20261109:
    """Fold rule for the 20261106 -> 20261109 migration (live-data safety).

    Builds a minimal 20261106-shaped database by hand and applies the real
    migration file. Fold rule: primary_scope if set, else the LAST element
    of active_scopes, else NULL.
    """

    @pytest.fixture
    def migrated_db(self, tmp_path):
        db_path = tmp_path / "core_20261106.db"
        db = sqlite3.connect(str(db_path))
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE _schema_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO _schema_metadata VALUES
                ('version', '20261106', datetime('now'));
            CREATE TABLE context_frame (
                uuid TEXT PRIMARY KEY,
                project_uuid TEXT,
                participant TEXT NOT NULL,
                containers JSON NOT NULL,
                created_at TEXT NOT NULL,
                parent_frame_uuid TEXT,
                active_scopes JSON NOT NULL DEFAULT '[]',
                primary_scope TEXT
            );
            CREATE TABLE api_keys (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                key_prefix TEXT NOT NULL,
                agent_entity_id TEXT,
                expires_at TEXT
            );
            INSERT INTO context_frame (uuid, participant, containers, created_at, active_scopes, primary_scope)
                VALUES ('cf_primary', 'user1', '[]', '2026-11-01T00:00:00Z', '["scopeA","scopeB"]', 'scopeA');
            INSERT INTO context_frame (uuid, participant, containers, created_at, active_scopes, primary_scope)
                VALUES ('cf_agent', 'agent1', '[]', '2026-11-01T00:00:00Z', '[]', NULL);
            INSERT INTO context_frame (uuid, participant, containers, created_at, active_scopes, primary_scope)
                VALUES ('cf_multi_noprimary', 'user2', '[]', '2026-11-01T00:00:00Z', '["scopeX","scopeY","scopeZ"]', NULL);
            INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix)
                VALUES ('k1', 'user1', 'test', 'hashvalue', 'ABCD');
            """
        )
        db.commit()
        yield db
        db.close()

    def _column_names(self, db, table):
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}

    def test_fold_rule(self, migrated_db):
        _apply_migration(migrated_db, "20261106", "20261109")

        rows = {
            row["uuid"]: row["active_scope"]
            for row in migrated_db.execute("SELECT uuid, active_scope FROM context_frame")
        }
        # primary_scope wins when set
        assert rows["cf_primary"] == "scopeA"
        # empty active set + no primary -> NULL
        assert rows["cf_agent"] is None
        # no primary -> last element of the old active array
        assert rows["cf_multi_noprimary"] == "scopeZ"

    def test_old_columns_dropped_new_index_present(self, migrated_db):
        _apply_migration(migrated_db, "20261106", "20261109")

        cols = self._column_names(migrated_db, "context_frame")
        assert "active_scope" in cols
        assert "active_scopes" not in cols
        assert "primary_scope" not in cols

        indexes = {
            row[1] for row in migrated_db.execute("PRAGMA index_list(context_frame)")
        }
        assert "idx_context_frame_active_scope" in indexes

    def test_api_keys_grants_column_added(self, migrated_db):
        _apply_migration(migrated_db, "20261106", "20261109")

        assert "grants" in self._column_names(migrated_db, "api_keys")
        row = migrated_db.execute("SELECT grants FROM api_keys WHERE id = 'k1'").fetchone()
        assert row["grants"] == "[]"

    def test_schema_version_bumped(self, migrated_db):
        _apply_migration(migrated_db, "20261106", "20261109")

        version = migrated_db.execute(
            "SELECT value FROM _schema_metadata WHERE key = 'version'"
        ).fetchone()
        assert version["value"] == "20261109"
