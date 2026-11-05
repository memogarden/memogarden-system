-- Migration: 20260130 -> 20261105
-- Description: Add provider registry and provider run history (RFC-004 §12.2)
--
-- Usage: Apply this migration to existing databases created with schema version 20260130
--
-- This migration adds:
--   - providers table (server-side registry of data providers executing
--     in-process in the server; infrastructure table alongside api_keys,
--     NOT delta-tracked)
--   - provider_runs table (run history; partial unique index enforces
--     single-flight execution per provider across server workers)

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261105', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys, recurrences, and providers', updated_at = datetime('now')
WHERE key = 'description';

-- Provider registry (RFC-004 §12.2)
CREATE TABLE IF NOT EXISTS providers (
    name TEXT PRIMARY KEY,                    -- slug ^[a-z][a-z0-9_-]{0,63}$
    module TEXT NOT NULL,                     -- importable Python module
    class TEXT NOT NULL,                      -- Provider Protocol class
    interval INTEGER NOT NULL DEFAULT 3600 CHECK (interval >= 60),
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    config TEXT NOT NULL DEFAULT '{}',        -- JSON; provider-opaque
    capabilities TEXT NOT NULL DEFAULT '[]',  -- JSON; derived at registration
    registered_at TEXT NOT NULL,
    last_run_id TEXT,                         -- links into provider_runs
    last_run_at TEXT,
    last_status TEXT,                         -- 'success' | 'failed'
    next_due_at TEXT                          -- NULL = due now
);
CREATE INDEX IF NOT EXISTS idx_providers_due ON providers(enabled, next_due_at);

-- Provider run history; partial unique index = single-flight lock
CREATE TABLE IF NOT EXISTS provider_runs (
    id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    triggered_by TEXT,                        -- 'heartbeat' | 'manual'
    detail TEXT                               -- JSON: counts or error
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_runs_single_flight
    ON provider_runs(provider_name) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_provider_runs_provider
    ON provider_runs(provider_name, started_at DESC);

-- Commit migration
COMMIT;
