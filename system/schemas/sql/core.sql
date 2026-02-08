-- MemoGarden Core Database Schema
-- Schema Version: 20260130
-- Description: Base Core tables only (no type-specific extensions)
--
-- DESIGN:
-- - entity: Global registry with JSON data field
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
    ('version', '20260130', datetime('now')),
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

    -- RFC-003 v4: Scope activation (INV-11, INV-11a, INV-11b)
    active_scopes JSON NOT NULL DEFAULT '[]',  -- Array of active scope UUIDs
    primary_scope TEXT,                        -- Currently focused scope (NULL = no primary)

    FOREIGN KEY (parent_frame_uuid) REFERENCES context_frame(uuid)
);

CREATE INDEX IF NOT EXISTS idx_context_frame_participant ON context_frame(participant);
CREATE INDEX IF NOT EXISTS idx_context_frame_project ON context_frame(project_uuid);
CREATE INDEX IF NOT EXISTS idx_context_frame_primary_scope ON context_frame(primary_scope);

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