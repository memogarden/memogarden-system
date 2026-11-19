"""Tests for RFC-011 / RFC-003 v5: exclusive scope activation + grants schema.

Covers:
- ContextFrame singular active_scope: enter/leave/switch semantics (INV-11a)
- Idempotent re-enter, mismatched-leave rejection, focus as no-op alias
- Builtin scope provisioning (triage, personal) — idempotent (RFC-011 §4.1)
- The 2026-11-19 "Private" -> "Personal" rename migration (visibility addendum)
- The 20261106 -> 20261109 migration fold rule (live-data safety)
"""

import json
import sqlite3

import pytest

from system.core import (
    _apply_migration,
    ensure_builtin_scopes,
    get_core,
    migrate_private_scope_rename,
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
    """RFC-011 §4.1: triage and personal provisioned idempotently.

    Uses its own get_core() connections (not the db_core fixture, which
    rolls back) so the seeded entities persist within the module run.
    """

    def test_provision_creates_triage_and_personal(self):
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
                "('Triage / Untrusted', 'Personal')"
            ).fetchall()
            data = [json.loads(row["data"]) for row in rows]
            labels = {d["label"] for d in data}
            assert labels == {"Triage / Untrusted", "Personal"}
            # Builtins are restricted-by-default (visibility addendum).
            assert all(d["visibility"] == "private" for d in data)


class TestPrivateScopeRenameMigration:
    """2026-11-19 rename migration: builtin "Private" -> "Personal".

    Immutable memory: the rename lands as a NEW Scope entity; the
    migration moves every reference onto the new UUID (key grants,
    entity data.scopes, context frames, Soil item labels, provider
    policies), stamps the visibility attribute onto scopes that predate
    it, and supersedes the old entity for the audit trail.

    Uses its own get_core()/get_soil() connections (persisting, not the
    rolling db_core fixture), mirroring the startup order:
    ensure_builtin_scopes() -> migrate_private_scope_rename().
    """

    def _seed_legacy_state(self):
        """Recreate the pre-rename live shape: one active "Private" scope
        every reference site points at."""
        import uuid as uuid_mod

        from system.host.environment import get_db_path
        from system.soil import get_soil
        from system.soil.fact import Fact

        with get_core() as core:
            # The autouse fixture wipes entity/item only — clear the
            # auxiliary tables this helper writes so it can re-run.
            core._conn.execute("DELETE FROM providers WHERE name = 'rnmp'")
            core._conn.execute("DELETE FROM api_keys WHERE name = 'legacy-key'")
            core._conn.execute("DELETE FROM users WHERE username = 'rnm-op'")
            core._conn.execute(
                "DELETE FROM context_frame WHERE participant = 'user-rnm'"
            )
            old_uuid = core.entity.create(
                entity_type="Scope",
                data={"label": "Private", "active_participants": [], "artifact_uuids": []},
            )
            # api_keys.id/user_id are entity-FKs — create the identity rows.
            user_entity = core.entity.create(
                entity_type="Operator", data={"label": "rnm-operator"}
            )
            key_entity = core.entity.create(
                entity_type="ApiKey", data={"label": "legacy-key"}
            )
            core._conn.execute(
                "INSERT INTO users (id, username, password_hash, is_admin, created_at) "
                "VALUES (?, 'rnm-op', 'hash-rnm', 1, datetime('now'))",
                (user_entity,),
            )
            core._conn.execute(
                "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, grants, created_at) "
                "VALUES (?, ?, 'legacy-key', 'hash-rnm', 'mg_sk_rnm', ?, datetime('now'))",
                (key_entity, user_entity, json.dumps([{"scope_uuid": old_uuid, "granted_at": "2026-11-01T00:00:00"}])),
            )
            # A second key holding the core_-prefixed form of the same ref —
            # the migration must swap both forms (bare -> bare, prefixed -> prefixed).
            prefixed_key_entity = core.entity.create(
                entity_type="ApiKey", data={"label": "legacy-key-prefixed"}
            )
            core._conn.execute(
                "INSERT INTO api_keys (id, user_id, name, key_hash, key_prefix, grants, created_at) "
                "VALUES (?, ?, 'legacy-key-prefixed', 'hash-rnm-p', 'mg_sk_rnmp', ?, datetime('now'))",
                (prefixed_key_entity, user_entity, json.dumps([{"scope_uuid": f"core_{old_uuid}", "granted_at": "2026-11-01T00:00:00"}])),
            )
            labeled_entity = core.entity.create(
                entity_type="Task",
                data={"title": "labeled", "scopes": [old_uuid]},
            )
            frame = core.context.get_context_frame("user-rnm", "operator")
            core.context.enter_scope(frame, old_uuid)
            core._conn.execute(
                "INSERT INTO providers (name, module, class, registered_at, scope_policy) "
                "VALUES ('rnmp', 'rnmp.provider', 'RnmpProvider', datetime('now'), ?)",
                (json.dumps(
                    {
                        "rules": [{"match": {"account": "a@b.c"}, "scope": "private"}],
                        "default": "private",
                    }
                ),),
            )
            core._conn.commit()

        with get_soil(str(get_db_path("soil"))) as soil:
            fact = Fact(
                uuid=str(uuid_mod.uuid4()),
                _type="Email",
                realized_at="2026-11-01T00:00:00",
                canonical_at="2026-11-01T00:00:00",
                data={"subject": "labeled"},
                metadata={"scopes": [old_uuid]},
            )
            soil.create_fact(fact)
            soil._get_connection().commit()

        return old_uuid, labeled_entity

    def test_rename_migrates_every_reference_site(self):
        from system.host.environment import get_db_path
        from system.soil import get_soil

        old_uuid, _labeled_entity = self._seed_legacy_state()

        # Startup order: seeding creates "Personal" first, then the
        # migration moves the references across.
        created = ensure_builtin_scopes()
        assert len(created) == 2  # Triage + Personal on this wiped DB
        summary = migrate_private_scope_rename(str(get_db_path("soil")))
        assert summary["ran"] is True
        new_uuid = summary["new_uuid"]
        assert new_uuid and new_uuid != old_uuid

        with get_core() as core:
            # Grants re-point to the new scope — bare refs stay bare.
            grants = json.loads(
                core._conn.execute(
                    "SELECT grants FROM api_keys WHERE name = 'legacy-key'"
                ).fetchone()["grants"]
            )
            assert grants[0]["scope_uuid"] == new_uuid
            # Prefixed refs swap prefixed -> prefixed.
            prefixed = json.loads(
                core._conn.execute(
                    "SELECT grants FROM api_keys WHERE name = 'legacy-key-prefixed'"
                ).fetchone()["grants"]
            )
            assert prefixed[0]["scope_uuid"] == f"core_{new_uuid}"

            # Entity labels re-point.
            ent_data = json.loads(
                core._conn.execute(
                    "SELECT data FROM entity WHERE uuid = ?", (_labeled_entity,)
                ).fetchone()["data"]
            )
            assert ent_data["scopes"] == [new_uuid]

            # Context frames re-point.
            frame_scope = core._conn.execute(
                "SELECT active_scope FROM context_frame WHERE participant = 'user-rnm'"
            ).fetchone()["active_scope"]
            assert frame_scope == new_uuid

            # Provider policies lose the legacy alias.
            policy = json.loads(
                core._conn.execute(
                    "SELECT scope_policy FROM providers WHERE name = 'rnmp'"
                ).fetchone()["scope_policy"]
            )
            assert policy["rules"][0]["scope"] == "personal"
            assert policy["default"] == "personal"

            # Visibility stamped onto scopes that predate the attribute.
            scope_rows = core._conn.execute(
                "SELECT data FROM entity WHERE type = 'Scope' AND superseded_by IS NULL"
            ).fetchall()
            visibility = {
                json.loads(r["data"])["label"]: json.loads(r["data"]).get("visibility")
                for r in scope_rows
            }
            assert visibility["Triage / Untrusted"] == "private"
            assert visibility["Personal"] == "private"

            # Old scope superseded onto the new one (immutable memory).
            superseded_by = core._conn.execute(
                "SELECT superseded_by FROM entity WHERE uuid = ?", (old_uuid,)
            ).fetchone()["superseded_by"]
            assert superseded_by == new_uuid

        # Soil item labels re-point.
        with get_soil(str(get_db_path("soil"))) as soil:
            row = soil._get_connection().execute(
                "SELECT metadata FROM item WHERE "
                "json_extract(metadata, '$.scopes[0]') IS NOT NULL"
            ).fetchone()
            assert json.loads(row["metadata"])["scopes"] == [new_uuid]

    def test_rename_is_idempotent(self):
        from system.host.environment import get_db_path

        self._seed_legacy_state()
        ensure_builtin_scopes()
        first = migrate_private_scope_rename(str(get_db_path("soil")))
        assert first["ran"] is True

        # Second startup: old scope is superseded -> migration no-ops.
        second = migrate_private_scope_rename(str(get_db_path("soil")))
        assert second["ran"] is False
        # Re-seeding is a no-op too: "Personal" already exists.
        assert ensure_builtin_scopes() == []

    def test_fresh_database_never_migrates(self):
        from system.host.environment import get_db_path

        # Wiped DB, no legacy "Private" anywhere: seeding alone suffices.
        created = ensure_builtin_scopes()
        assert len(created) == 2
        summary = migrate_private_scope_rename(str(get_db_path("soil")))
        assert summary["ran"] is False


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


class TestProviderScopePolicyColumn:
    """RFC-011 §4.8 item 4: providers.scope_policy (schema 20261113)."""

    def test_fresh_schema_has_scope_policy_column(self, tmp_path):
        """A database built from canonical core.sql has the column and
        seeds directly at EXPECTED_SCHEMA_VERSION."""
        from system.core import EXPECTED_SCHEMA_VERSION
        from system.schemas import get_sql_schema

        db = sqlite3.connect(str(tmp_path / "fresh.db"))
        db.executescript(get_sql_schema("core"))

        version = db.execute(
            "SELECT value FROM _schema_metadata WHERE key = 'version'"
        ).fetchone()
        assert version[0] == EXPECTED_SCHEMA_VERSION == "20261119"
        cols = {row[1] for row in db.execute("PRAGMA table_info(providers)")}
        assert "scope_policy" in cols

    def test_migration_from_20261109_adds_column(self, tmp_path):
        """An existing 20261109 database gains providers.scope_policy; the
        chain runner walks it there in one init."""
        db = sqlite3.connect(str(tmp_path / "old.db"))
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE _schema_metadata (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            INSERT INTO _schema_metadata VALUES ('version', '20261109', datetime('now'));
            -- api_keys: created by migrate_20251223_to_20251229, so every real
            -- 20261109 database has it — the chain walk to EXPECTED ALTERs it
            -- (E3 capabilities). Hand-built fixtures carry what the chain
            -- guarantees, not just the table under test.
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
            INSERT INTO providers (name, module, class, registered_at)
                VALUES ('gmail_importer', 'mod', 'Cls', '2026-11-09T00:00:00Z');
            """
        )
        db.commit()

        from system.core import EXPECTED_SCHEMA_VERSION, _run_migrations
        _run_migrations(db)

        version = db.execute(
            "SELECT value FROM _schema_metadata WHERE key = 'version'"
        ).fetchone()[0]
        assert version == EXPECTED_SCHEMA_VERSION
        cols = {row[1] for row in db.execute("PRAGMA table_info(providers)")}
        assert "scope_policy" in cols
        # Existing row intact; policy NULL (= ratified default catch-all)
        row = db.execute(
            "SELECT name, scope_policy FROM providers WHERE name = 'gmail_importer'"
        ).fetchone()
        assert row["name"] == "gmail_importer"
        assert row["scope_policy"] is None
