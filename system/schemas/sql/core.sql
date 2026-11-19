-- MemoGarden Core Database Schema
-- Schema Version: 20260130
-- Description: Base Core tables only (no type-specific extensions)
--
-- DESIGN:
-- - entity: Global registry with JSON data field
--   - Entity types: Artifact, ContextFrame, ConversationLog, Label, Scope, Transaction, User, View, ViewMerge
-- - user_relation: Engagement signals with time horizon
-- - context_frame: Attention tracking (LRU containers)
-- - Type-specific schemas in /schemas/types/entities/*.schema.json

-- ============================================================================
-- SCHEMA METADATA
-- ============================================================================

CREATE TABLE IF NOT EXISTS _schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO _schema_metadata VALUES
    ('version', '20261119', datetime('now')),
    ('base_schema', 'memogarden-core-base', datetime('now')),
    ('description', 'Core base tables only; type schemas in JSON', datetime('now')),
    ('epoch_date', '2020-01-01', datetime('now'));

-- ============================================================================
-- ENTITY TABLE (Global registry with hash chain)
-- ============================================================================

CREATE TABLE IF NOT EXISTS entity (
    uuid TEXT PRIMARY KEY,            -- core_ prefix
    type TEXT NOT NULL,               -- 'Artifact', 'Transaction', 'Label', etc.
    hash TEXT NOT NULL,               -- SHA256(data + previous_hash)
    previous_hash TEXT,               -- Previous state hash (NULL for initial)
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,         -- ISO 8601
    updated_at TEXT NOT NULL,         -- ISO 8601

    -- Lifecycle metadata
    group_id TEXT,
    superseded_by TEXT,
    superseded_at TEXT,
    derived_from TEXT,

    -- Type-specific data (validated by /schemas/types/entities/{type}.schema.json)
    data JSON NOT NULL,

    FOREIGN KEY (group_id) REFERENCES entity(uuid),
    FOREIGN KEY (superseded_by) REFERENCES entity(uuid),
    FOREIGN KEY (derived_from) REFERENCES entity(uuid)
);

CREATE INDEX IF NOT EXISTS idx_entity_type ON entity(type);
CREATE INDEX IF NOT EXISTS idx_entity_hash ON entity(hash);
CREATE INDEX IF NOT EXISTS idx_entity_previous_hash ON entity(previous_hash);
CREATE INDEX IF NOT EXISTS idx_entity_updated ON entity(updated_at);

-- ============================================================================
-- USER RELATION TABLE (Engagement signals with time horizon)
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_relation (
    uuid TEXT PRIMARY KEY,            -- core_ prefix (becomes soil_ on fossilization)
    kind TEXT NOT NULL,               -- 'explicit_link', etc.
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,        -- 'item' | 'entity'
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    
    -- Time horizon mechanism (RFC-002)
    time_horizon INTEGER NOT NULL,    -- Days since epoch
    last_access_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    
    evidence JSON,
    metadata JSON,
    
    UNIQUE(kind, source, target)
);

CREATE INDEX IF NOT EXISTS idx_userrel_source ON user_relation(source);
CREATE INDEX IF NOT EXISTS idx_userrel_target ON user_relation(target);
CREATE INDEX IF NOT EXISTS idx_userrel_horizon ON user_relation(time_horizon);

-- ============================================================================
-- CONTEXT FRAME TABLE (Attention tracking)
-- ============================================================================

CREATE TABLE IF NOT EXISTS context_frame (
    uuid TEXT PRIMARY KEY,            -- core_ prefix
    project_uuid TEXT,
    participant TEXT NOT NULL,
    containers JSON NOT NULL,         -- Array of container UUIDs (LRU ordered)
    created_at TEXT NOT NULL,
    parent_frame_uuid TEXT,

    -- RFC-003 v5 / RFC-011: exclusive activation (INV-11a) — the single
    -- active scope; NULL = unscoped operation (global space)
    active_scope TEXT,

    FOREIGN KEY (parent_frame_uuid) REFERENCES context_frame(uuid)
);

CREATE INDEX IF NOT EXISTS idx_context_frame_participant ON context_frame(participant);
CREATE INDEX IF NOT EXISTS idx_context_frame_project ON context_frame(project_uuid);
CREATE INDEX IF NOT EXISTS idx_context_frame_active_scope ON context_frame(active_scope);

-- ============================================================================
-- AUTHENTICATION TABLES
-- ============================================================================

-- Users table for authentication (RFC-004)
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- API keys table for token-based authentication (RFC-004)
-- agent_entity_id (RFC-010): nullable link to the Agent entity this key
-- is attributed to. Unbound keys attribute to the owning username.
-- grants (RFC-011 §4.3): operator-issued scope entry permissions,
-- [{scope_uuid, granted_at, expires_at|null}]; [] = unscoped-only.
-- capabilities (E3, RFC-013 ingestion register): restricted key classes —
-- non-empty list = key may ONLY call endpoints covered by those
-- capabilities; [] = unrestricted (all legacy keys unrestricted).
CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    agent_entity_id TEXT REFERENCES entity(uuid),
    grants JSON NOT NULL DEFAULT '[]',
    capabilities JSON NOT NULL DEFAULT '[]',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT,
    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_active ON api_keys(revoked_at) WHERE revoked_at IS NULL;

-- Provider registry (RFC-004 §12.2): server-side registry of data providers
-- executing in-process in the server. Infrastructure table alongside
-- api_keys — deliberately OUTSIDE the entity/delta machinery (deployment
-- config, not user state). Provider credentials stay in HACM; the config
-- JSON holds references only, never secrets.
CREATE TABLE IF NOT EXISTS providers (
    name TEXT PRIMARY KEY,                    -- slug ^[a-z][a-z0-9_-]{0,63}$
    module TEXT NOT NULL,                     -- importable Python module
    class TEXT NOT NULL,                      -- Provider Protocol class
    interval INTEGER NOT NULL DEFAULT 3600 CHECK (interval >= 60),
    enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT,
    config TEXT NOT NULL DEFAULT '{}',        -- JSON; provider-opaque
    capabilities TEXT NOT NULL DEFAULT '[]',  -- JSON; derived at registration
    scope_policy TEXT,                        -- JSON; RFC-011 §4.8 operator
                                              -- policy: {rules: [{match, scope}],
                                              -- default: uuid|alias|null}.
                                              -- NULL = ratified default
                                              -- (catch-all → triage)
    registered_at TEXT NOT NULL,
    last_run_id TEXT,                         -- links into provider_runs
    last_run_at TEXT,
    last_status TEXT,                         -- 'success' | 'failed'
    next_due_at TEXT                          -- NULL = due now
);
CREATE INDEX IF NOT EXISTS idx_providers_due ON providers(enabled, next_due_at);

-- Provider run history; account_id (RFC-012 §4.4): '' = provider-level run
-- (account-less providers and all pre-RFC-012 rows), otherwise the
-- provider_accounts.id the run executed. The '' sentinel (not NULL) is
-- deliberate: NULLs are distinct in SQLite unique indexes.
-- The partial unique index enforces single-flight — one running run per
-- (provider, account) — across all server workers: the INSERT that claims
-- a run fails if another run is still marked 'running'.
CREATE TABLE IF NOT EXISTS provider_runs (
    id TEXT PRIMARY KEY,
    provider_name TEXT NOT NULL,
    account_id TEXT NOT NULL DEFAULT '',      -- RFC-012: '' = provider-level
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'success', 'failed')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    triggered_by TEXT,                        -- 'heartbeat' | 'manual'
    detail TEXT                               -- JSON: counts or error
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_runs_single_flight
    ON provider_runs(provider_name, account_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS idx_provider_runs_provider
    ON provider_runs(provider_name, started_at DESC);

-- Provider accounts (RFC-012): one row per (provider, upstream identity).
-- An account binds the provider to a verified subject (email/username) and
-- a credential REFERENCE — tokens stay in HACM, never in this table.
-- Accounts are not principals: they hold no grants and gate nothing
-- (RFC-012 §3); they are the subject of attribution and labeling.
CREATE TABLE IF NOT EXISTS provider_accounts (
    id TEXT PRIMARY KEY,                      -- uuid
    provider_name TEXT NOT NULL,              -- registry name this account feeds
    platform TEXT NOT NULL,                   -- 'google' | 'github' | ...
    subject TEXT NOT NULL,                    -- verified upstream identity (email/username)
    label TEXT,                               -- operator nickname (CLI display)
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'needs_reauth', 'revoked', 'disabled')),
    credential_ref TEXT NOT NULL,             -- HACM credential id ({platform}:{subject})
    config TEXT NOT NULL DEFAULT '{}',        -- JSON; per-account config (overrides provider config)
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

-- ============================================================================
-- TRIGGERS
-- ============================================================================

CREATE TRIGGER IF NOT EXISTS entity_update_timestamp
AFTER UPDATE ON entity
BEGIN
    UPDATE entity SET updated_at = datetime('now')
    WHERE uuid = NEW.uuid;
END;

-- ============================================================================
-- COMMON QUERIES
-- ============================================================================

-- Q1: Get entity with current state
-- SELECT * FROM entity WHERE uuid = ?;

-- Q2: Get all active user relations for an entity
-- SELECT * FROM user_relation 
-- WHERE (source = ? OR target = ?)
-- AND time_horizon >= (julianday('now') - julianday('2020-01-01'));

-- Q3: Get participant's current focus
-- SELECT json_extract(containers, '$[0]') as current_focus
-- FROM context_frame
-- WHERE participant = ?;