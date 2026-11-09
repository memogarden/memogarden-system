-- Migration: 20261106 -> 20261109
-- Description: RFC-011 scopes & grants — exclusive scope activation + api_keys.grants
--
-- Usage: Apply this migration to existing databases created with schema version 20261106
--
-- This migration:
--   - context_frame: folds active_scopes (JSON array) + primary_scope into the
--     singular active_scope (RFC-003 v5 INV-11a: exclusive activation, no
--     co-activation). Fold rule: primary_scope if set, else the LAST element
--     of active_scopes, else NULL.
--   - api_keys: adds grants column (RFC-011 §4.3) — operator-issued scope
--     entry permissions; '[]' = unscoped-only.
--
-- Builtin scope seeding (triage, private) is NOT done here: entity rows are
-- hash-chained and must be created through Core.entity.create (see
-- ensure_builtin_scopes() in system/core/__init__.py, called at startup).
--
-- Note: fresh databases initialized from core.sql seed at version 20261109
-- with the singular column already present; this migration is for databases
-- created at 20261106.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261109', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011), recurrences, providers, and exclusive scope activation (RFC-003 v5)', updated_at = datetime('now')
WHERE key = 'description';

-- Singular active_scope (RFC-003 v5 INV-11a / RFC-011 §4.4)
ALTER TABLE context_frame ADD COLUMN active_scope TEXT;

UPDATE context_frame SET active_scope = COALESCE(
    primary_scope,
    CASE
        WHEN json_array_length(active_scopes) > 0
        THEN json_extract(active_scopes, '$[' || (json_array_length(active_scopes) - 1) || ']')
        ELSE NULL
    END
);

DROP INDEX IF EXISTS idx_context_frame_primary_scope;
ALTER TABLE context_frame DROP COLUMN primary_scope;
ALTER TABLE context_frame DROP COLUMN active_scopes;
CREATE INDEX IF NOT EXISTS idx_context_frame_active_scope ON context_frame(active_scope);

-- Grants column (RFC-011 §4.3): operator-issued scope entry permissions
ALTER TABLE api_keys ADD COLUMN grants JSON NOT NULL DEFAULT '[]';

-- Commit migration
COMMIT;
