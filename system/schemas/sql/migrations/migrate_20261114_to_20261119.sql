-- Migration: 20261114 -> 20261119
-- Description: RFC-013 ingestion register E3 — api_keys.capabilities
--
-- Restricted API-key classes: a key minted with a non-empty capabilities
-- list (JSON array of capability ids, e.g. ["provider_run"]) may ONLY
-- call the endpoints covered by that capability's route set; every
-- other endpoint 403s. Empty list = unrestricted (all legacy keys are
-- unrestricted — no behavior change for existing keys). Issuance stays
-- operator-gated JWT (RFC-010 invariant); enforcement is read-only over
-- the key row (auth hot-path invariant, RFC-010).
--
-- Note: fresh databases initialized from core.sql seed at version
-- 20261119 with the column already present.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261119', updated_at = datetime('now')
WHERE key = 'version';

-- Update schema description
UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011; restricted capability classes per E3), recurrences, providers (scope policies RFC-011 §4.8), and exclusive scope activation (RFC-003 v5)', updated_at = datetime('now')
WHERE key = 'description';

ALTER TABLE api_keys ADD COLUMN capabilities JSON NOT NULL DEFAULT '[]';

-- Commit migration
COMMIT;
