-- Migration: 20261121 -> 20261124
-- Description: Change feed (system#12 slice — search-sync high-water mark)
--
-- Usage: Apply this migration to existing CORE databases created with
-- schema version 20261121. Fresh databases seed at 20261124 directly and
-- never run this file (init_db skips _run_migrations when versions match).
--
-- Adds the trigger-maintained change_log feed for the content tables Core
-- owns (entity, user_relation), mirroring the fresh-seed DDL in core.sql
-- at the schemas pin (memogarden/memogarden cd5f752, which also carries
-- the soil.db feed + item_fts index for api#95 — soil needs no migration
-- file: init_databases() re-runs soil.init_schema() on every startup and
-- the seed DDL is all IF NOT EXISTS / INSERT OR IGNORE).
--
-- Rationale and documented semantics (double-fire note, pruning, per-DB
-- checkpoints) live in the schema files — this file only replicates the
-- fresh-seed DDL for databases that predate it.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261124', updated_at = datetime('now')
WHERE key = 'version';

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

COMMIT;
