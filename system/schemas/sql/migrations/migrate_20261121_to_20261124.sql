-- Migration 20261121 -> 20261124 (memogarden-system#12 search-sync slice)
-- Add the trigger-maintained change_log feed to the CORE database:
--
--   change_log(seq AUTOINCREMENT, tbl, uuid, kind, changed_at)
--   + AFTER INSERT/UPDATE/DELETE triggers on entity and user_relation
--
-- Content tables get a monotonic per-DB "what changed since seq N" sweep for
-- derived consumers (FTS5 lexical index, embed-daemon vector catch-up).
-- Triggers fire inside the write's own transaction — a rolled-back write
-- leaves no feed rows. Content guards suppress identical-value rewrites.
-- Infra tables (providers, api_keys, users, ...) are deliberately outside
-- the feed; soil.db runs its own feed (applied by soil.init_schema()'s
-- executescript at startup, all IF NOT EXISTS) — this migration is core-only.
--
-- Fresh seeds (root schemas pinned via system/schemas/memogarden at c02c6be)
-- carry the feed directly at 20261124; this migration exists so existing DBs
-- converge to the identical shape. Replay is idempotent (IF NOT EXISTS).
--
-- See core.sql §CHANGE FEED for the full rationale (why triggers, not
-- call-site instrumentation; seq monotonicity; the timestamp-stomp /
-- multi-statement feed-row semantics).

BEGIN;

CREATE TABLE IF NOT EXISTS change_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    tbl TEXT NOT NULL,                  -- 'entity' | 'user_relation'
    uuid TEXT NOT NULL,
    kind TEXT NOT NULL,                 -- 'insert' | 'update' | 'delete'
    changed_at TEXT NOT NULL            -- UTC ISO 8601 (trigger time)
);

CREATE TRIGGER IF NOT EXISTS entity_change_insert
AFTER INSERT ON entity
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('entity', NEW.uuid, 'insert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS entity_change_update
AFTER UPDATE ON entity
    WHEN NEW.type IS NOT OLD.type
      OR NEW.hash IS NOT OLD.hash
      OR NEW.previous_hash IS NOT OLD.previous_hash
      OR NEW.version IS NOT OLD.version
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.updated_at IS NOT OLD.updated_at
      OR NEW.group_id IS NOT OLD.group_id
      OR NEW.superseded_by IS NOT OLD.superseded_by
      OR NEW.superseded_at IS NOT OLD.superseded_at
      OR NEW.derived_from IS NOT OLD.derived_from
      OR NEW.data IS NOT OLD.data
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('entity', NEW.uuid, 'update', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS entity_change_delete
AFTER DELETE ON entity
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('entity', OLD.uuid, 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS userrel_change_insert
AFTER INSERT ON user_relation
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('user_relation', NEW.uuid, 'insert', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS userrel_change_update
AFTER UPDATE ON user_relation
    WHEN NEW.kind IS NOT OLD.kind
      OR NEW.source IS NOT OLD.source
      OR NEW.target IS NOT OLD.target
      OR NEW.source_type IS NOT OLD.source_type
      OR NEW.target_type IS NOT OLD.target_type
      OR NEW.time_horizon IS NOT OLD.time_horizon
      OR NEW.last_access_at IS NOT OLD.last_access_at
      OR NEW.created_at IS NOT OLD.created_at
      OR NEW.evidence IS NOT OLD.evidence
      OR NEW.metadata IS NOT OLD.metadata
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('user_relation', NEW.uuid, 'update', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

CREATE TRIGGER IF NOT EXISTS userrel_change_delete
AFTER DELETE ON user_relation
BEGIN
    INSERT INTO change_log (tbl, uuid, kind, changed_at)
    VALUES ('user_relation', OLD.uuid, 'delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
END;

-- Stamp the new schema version
UPDATE _schema_metadata
SET value = '20261124', updated_at = datetime('now')
WHERE key = 'version';

-- Update schema description
UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011; restricted capability classes per E3), recurrences, providers (scope policies RFC-011 §4.8), provider accounts (per-account scope_policy override RFC-012), exclusive scope activation (RFC-003 v5); entity hashes re-signed under per-row integrity semantics + guarded update timestamp trigger (RFC-008 v1.3, memogarden-system#6); trigger-maintained change_log feed on entity + user_relation (system#12 search-sync slice)', updated_at = datetime('now')
WHERE key = 'description';

COMMIT;
