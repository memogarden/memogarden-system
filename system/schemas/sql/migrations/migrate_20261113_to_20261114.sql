-- Migration: 20261113 -> 20261114
-- Description: RFC-012 — provider accounts (multi-account ingestion)
--
-- Introduces provider_accounts (RFC-012 §4.1): a first-class binding of one
-- provider to one verified upstream identity (platform + subject) and a
-- credential reference. Infrastructure tier alongside providers — outside
-- the entity/delta machinery. No secrets: credential_ref points into HACM.
--
-- provider_runs gains account_id (RFC-012 §4.4): '' = provider-level run
-- (account-less providers, and all historical rows); otherwise the
-- provider_accounts.id the run executed. The single-flight partial unique
-- index becomes per (provider, account) so one wedged account cannot block
-- another account's run. NOTE: the '' sentinel (not NULL) is deliberate —
-- NULLs are distinct in SQLite unique indexes and would defeat the lock.
--
-- Note: fresh databases initialized from core.sql seed at version
-- 20261114 with the table and column already present.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261114', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011), recurrences, providers (scope policies RFC-011 §4.8; accounts RFC-012), and exclusive scope activation (RFC-003 v5)', updated_at = datetime('now')
WHERE key = 'description';

-- Provider accounts (RFC-012 §4.1): one row per (provider, upstream identity).
CREATE TABLE IF NOT EXISTS provider_accounts (
    id TEXT PRIMARY KEY,                      -- uuid
    provider_name TEXT NOT NULL,              -- registry name this account feeds
    platform TEXT NOT NULL,                   -- 'google' | 'github' | ...
    subject TEXT NOT NULL,                    -- verified upstream identity (email/username)
    label TEXT,                               -- operator nickname (CLI display)
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'needs_reauth', 'revoked', 'disabled')),
    credential_ref TEXT NOT NULL,             -- HACM credential id ({platform}:{subject})
    config TEXT NOT NULL DEFAULT '{}',        -- JSON; per-account provider config (overrides provider config)
    capabilities TEXT NOT NULL DEFAULT '[]',  -- JSON; capability grants on this identity
    error TEXT,                               -- last failure summary (CLI display)
    interval INTEGER,                         -- NULL = inherit provider interval
    next_due_at TEXT,                         -- per-account due time (NULL = due now)
    last_run_id TEXT,                         -- links into provider_runs
    last_run_at TEXT,
    last_status TEXT,
    last_verified_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (provider_name, subject)
);
CREATE INDEX IF NOT EXISTS idx_provider_accounts_due
    ON provider_accounts(provider_name, status, next_due_at);

-- Per-account run attribution (RFC-012 §4.4)
ALTER TABLE provider_runs ADD COLUMN account_id TEXT NOT NULL DEFAULT '';

-- Single-flight becomes per (provider, account)
DROP INDEX IF EXISTS idx_provider_runs_single_flight;
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_runs_single_flight
    ON provider_runs(provider_name, account_id) WHERE status = 'running';

-- Commit migration
COMMIT;
