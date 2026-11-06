-- Migration: 20261105 -> 20261106
-- Description: Add api_keys.agent_entity_id for agent identity binding (RFC-010)
--
-- Usage: Apply this migration to existing databases created with schema version 20261105
--
-- This migration adds:
--   - api_keys.agent_entity_id (nullable FK to entity(uuid)): links each API
--     key to the Agent entity its operations are attributed to. NULL means
--     the key is unbound and attributes to the owning username (RFC-010
--     "unbound is a first-class state"; binding is explicit and
--     operator-gated — no backfill).
--
-- Note: fresh databases initialized from core.sql seed at version 20261106
-- with the column already present; this migration is for databases created
-- at 20261105.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261106', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound, RFC-010), recurrences, and providers', updated_at = datetime('now')
WHERE key = 'description';

-- Agent identity binding (RFC-010): nullable link from API key to Agent entity
ALTER TABLE api_keys ADD COLUMN agent_entity_id TEXT REFERENCES entity(uuid);

-- Commit migration
COMMIT;
