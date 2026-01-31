-- Migration: 20251223 -> 20251229
-- Description: Add users and api_keys tables
--
-- Usage: Apply this migration to existing databases created with schema version 20251223
--
-- This migration adds:
--   - users table (for human users and device clients)
--   - api_keys table (for agents and programmatic access)
--   - Required indexes for performance

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20251229', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Initial schema with entity registry, transactions, users, and API keys', updated_at = datetime('now')
WHERE key = 'description';

-- Users table (humans, device clients)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,              -- UUID4
    username TEXT UNIQUE NOT NULL,    -- 'kureshii' (case-insensitive, stored lowercase)
    password_hash TEXT NOT NULL,      -- bcrypt hash
    is_admin INTEGER NOT NULL DEFAULT 0,  -- 0 = regular user, 1 = admin
    created_at TEXT NOT NULL,         -- ISO 8601 UTC
    FOREIGN KEY (id) REFERENCES entity(id) ON DELETE CASCADE
);

-- Index for username lookups
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- API Keys table (agents, scripts, programmatic clients)
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,              -- UUID4
    user_id TEXT NOT NULL,            -- References users.id
    name TEXT NOT NULL,               -- 'claude-code', 'custom-script'
    key_hash TEXT NOT NULL,           -- hashed API key (bcrypt)
    key_prefix TEXT NOT NULL,         -- 'mg_sk_agent_' (for display)
    expires_at TEXT,                  -- ISO 8601 UTC or NULL (no expiry)
    created_at TEXT NOT NULL,         -- ISO 8601 UTC
    last_seen TEXT,                   -- ISO 8601 UTC or NULL
    revoked_at TEXT,                  -- ISO 8601 UTC or NULL (active if NULL)
    FOREIGN KEY (id) REFERENCES entity(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Indexes for API key performance
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(revoked_at) WHERE revoked_at IS NULL;

-- Commit migration
COMMIT;
