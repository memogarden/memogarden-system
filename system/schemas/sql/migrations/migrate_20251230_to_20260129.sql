-- Migration: 20251230 -> 20260129
-- Description: PRD v6 alignment - hash-based entity change tracking
--
-- This migration adds:
--   - hash, previous_hash, version columns to entity table
--   - Renames entity.id to entity.uuid
--   - Adds user_relation table (engagement signals)
--   - Adds context_frame table (attention tracking)
--   - Updates all foreign keys and views
--
-- Note: This is a BREAKING migration. Existing data will be preserved but
-- the API contract will change (hash/previous_hash/version added to responses).

-- Begin transaction for atomic migration
BEGIN;

-- ============================================================================
-- STEP 1: Update schema metadata
-- ============================================================================

UPDATE _schema_metadata
SET value = '20260129', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'PRD v6 aligned schema with hash-based entity change tracking', updated_at = datetime('now')
WHERE key = 'description';

UPDATE _schema_metadata
SET value = 'memogarden-core-v2', updated_at = datetime('now')
WHERE key = 'base_schema';

-- ============================================================================
-- STEP 2: Migrate entity table (rename id -> uuid, add hash chain columns)
-- ============================================================================

-- Create new entity table with updated schema
CREATE TABLE entity_new (
    uuid TEXT PRIMARY KEY,            -- Renamed from id
    type TEXT NOT NULL,
    hash TEXT NOT NULL,               -- NEW: SHA256 of current state
    previous_hash TEXT,               -- NEW: Previous state hash
    version INTEGER NOT NULL DEFAULT 1, -- NEW: Version number
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    group_id TEXT,
    superseded_by TEXT,
    superseded_at TEXT,
    derived_from TEXT,

    FOREIGN KEY (group_id) REFERENCES entity_new(uuid),
    FOREIGN KEY (superseded_by) REFERENCES entity_new(uuid),
    FOREIGN KEY (derived_from) REFERENCES entity_new(uuid)
);

-- Migrate data from old entity table to new entity table
-- Compute initial hash for existing entities
INSERT INTO entity_new (
    uuid,
    type,
    hash,
    previous_hash,
    version,
    created_at,
    updated_at,
    group_id,
    superseded_by,
    superseded_at,
    derived_from
)
SELECT
    id AS uuid,
    type,
    -- Compute initial hash from all fields (consistent with new hash computation)
    lower(hex(sha256(
        type || '|' ||
        created_at || '|' ||
        updated_at || '|' ||
        coalesce(group_id, '') || '|' ||
        coalesce(derived_from, '')
    ))) AS hash,
    NULL AS previous_hash,            -- NULL for initial entities
    1 AS version,                     -- Version starts at 1
    created_at,
    updated_at,
    group_id,
    superseded_by,
    superseded_at,
    derived_from
FROM entity;

-- Drop old entity table and rename new one
DROP TABLE entity;
ALTER TABLE entity_new RENAME TO entity;

-- Recreate indexes for entity table
CREATE INDEX idx_entity_type ON entity(type);
CREATE INDEX idx_entity_created ON entity(created_at);
CREATE INDEX idx_entity_superseded ON entity(superseded_by);
CREATE INDEX idx_entity_group ON entity(group_id);
CREATE INDEX idx_entity_hash ON entity(hash);
CREATE INDEX idx_entity_previous_hash ON entity(previous_hash) WHERE previous_hash IS NOT NULL;

-- ============================================================================
-- STEP 3: Update foreign keys in child tables (id -> entity.uuid)
-- ============================================================================

-- Transactions: update foreign key to reference entity(uuid)
CREATE TABLE transactions_new (
    id TEXT PRIMARY KEY,
    amount REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'SGD',
    transaction_date TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    account TEXT NOT NULL,
    category TEXT,
    author TEXT NOT NULL DEFAULT 'system',
    recurrence_id TEXT,
    notes TEXT,

    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE,
    FOREIGN KEY (recurrence_id) REFERENCES entity(uuid)
);

INSERT INTO transactions_new SELECT * FROM transactions;
DROP TABLE transactions;
ALTER TABLE transactions_new RENAME TO transactions;

-- Recreate indexes
CREATE INDEX idx_transactions_date ON transactions(transaction_date);
CREATE INDEX idx_transactions_account ON transactions(account);
CREATE INDEX idx_transactions_category ON transactions(category) WHERE category IS NOT NULL;

-- Users: update foreign key
CREATE TABLE users_new (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,

    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE
);

INSERT INTO users_new SELECT * FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

CREATE INDEX idx_users_username ON users(username);

-- API keys: update foreign keys
CREATE TABLE api_keys_new (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    key_prefix TEXT NOT NULL,
    expires_at TEXT,
    created_at TEXT NOT NULL,
    last_seen TEXT,
    revoked_at TEXT,

    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

INSERT INTO api_keys_new SELECT * FROM api_keys;
DROP TABLE api_keys;
ALTER TABLE api_keys_new RENAME TO api_keys;

CREATE INDEX idx_api_keys_user_id ON api_keys(user_id);
CREATE INDEX idx_api_keys_active ON api_keys(revoked_at) WHERE revoked_at IS NULL;

-- Recurrences: update foreign key
CREATE TABLE recurrences_new (
    id TEXT PRIMARY KEY,
    rrule TEXT NOT NULL,
    entities TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_until TEXT,

    FOREIGN KEY (id) REFERENCES entity(uuid) ON DELETE CASCADE
);

INSERT INTO recurrences_new SELECT * FROM recurrences;
DROP TABLE recurrences;
ALTER TABLE recurrences_new RENAME TO recurrences;

CREATE INDEX idx_recurrences_valid_from ON recurrences(valid_from);
CREATE INDEX idx_recurrences_valid_until ON recurrences(valid_until) WHERE valid_until IS NOT NULL;

-- ============================================================================
-- STEP 4: Drop and recreate views with new schema
-- ============================================================================

-- Drop old views (if they exist)
DROP VIEW IF EXISTS transactions_view;
DROP VIEW IF EXISTS recurrences_view;
DROP VIEW IF EXISTS users_view;
DROP VIEW IF EXISTS api_keys_view;

-- Create new views with uuid as primary identifier
CREATE VIEW transactions_view AS
SELECT
    t.id AS uuid,
    t.*,
    e.type,
    e.hash,
    e.previous_hash,
    e.version,
    e.created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM transactions t
JOIN entity e ON t.id = e.uuid;

CREATE VIEW recurrences_view AS
SELECT
    r.id AS uuid,
    r.*,
    e.type,
    e.hash,
    e.previous_hash,
    e.version,
    e.created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM recurrences r
JOIN entity e ON r.id = e.uuid;

CREATE VIEW users_view AS
SELECT
    u.id AS uuid,
    u.*,
    e.type,
    e.hash,
    e.previous_hash,
    e.version,
    e.created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM users u
JOIN entity e ON u.id = e.uuid;

CREATE VIEW api_keys_view AS
SELECT
    a.id AS uuid,
    a.*,
    e.type,
    e.hash,
    e.previous_hash,
    e.version,
    e.created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM api_keys a
JOIN entity e ON a.id = e.uuid;

-- ============================================================================
-- STEP 5: Add new PRD v6 tables
-- ============================================================================

-- User relations table (engagement signals)
CREATE TABLE IF NOT EXISTS user_relation (
    uuid TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    time_horizon INTEGER NOT NULL,
    last_access_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    evidence TEXT,
    metadata TEXT,

    UNIQUE(kind, source, target)
);

CREATE INDEX IF NOT EXISTS idx_userrel_source ON user_relation(source);
CREATE INDEX IF NOT EXISTS idx_userrel_target ON user_relation(target);
CREATE INDEX IF NOT EXISTS idx_userrel_horizon ON user_relation(time_horizon);

CREATE VIEW IF NOT EXISTS user_relations_view AS
SELECT
    r.*,
    e.type,
    e.hash,
    e.previous_hash,
    e.version,
    e.created_at AS entity_created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM user_relation r
JOIN entity e ON r.uuid = e.uuid;

-- Context frame table (attention tracking)
CREATE TABLE IF NOT EXISTS context_frame (
    uuid TEXT PRIMARY KEY,
    project_uuid TEXT NOT NULL,
    participant TEXT NOT NULL,
    containers TEXT NOT NULL,
    created_at TEXT NOT NULL,
    parent_frame_uuid TEXT
);

CREATE INDEX IF NOT EXISTS idx_context_frame_project ON context_frame(project_uuid);
CREATE INDEX IF NOT EXISTS idx_context_frame_participant ON context_frame(participant);

CREATE VIEW IF NOT EXISTS context_frames_view AS
SELECT
    c.*,
    e.type,
    e.hash,
    e.previous_hash,
    e.version,
    e.created_at AS entity_created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM context_frame c
JOIN entity e ON c.uuid = e.uuid;

-- ============================================================================
-- Commit migration
-- ============================================================================

COMMIT;
