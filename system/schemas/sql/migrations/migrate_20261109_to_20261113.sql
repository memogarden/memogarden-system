-- Migration: 20261109 -> 20261113
-- Description: RFC-011 §4.8 — providers.scope_policy column
--
-- Adds the operator's scope policy to the provider registry (RFC-011
-- item 4, ratified 2026-11-12): rules-based labeling at creation with a
-- default catch-all → triage. NULL = unconfigured = the ratified default.
-- Applied by the provider runtime at fact creation (SG-7: providers are
-- exempt from scope gates but label at creation per operator policy).
--
-- Note: fresh databases initialized from core.sql seed at version
-- 20261113 with the column already present.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261113', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011), recurrences, providers (scope policies RFC-011 §4.8), and exclusive scope activation (RFC-003 v5)', updated_at = datetime('now')
WHERE key = 'description';

ALTER TABLE providers ADD COLUMN scope_policy TEXT;

-- Commit migration
COMMIT;
