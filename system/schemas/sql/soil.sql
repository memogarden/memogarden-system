-- MemoGarden Soil Database Schema
-- Schema Version: 20260130
-- Description: Base Soil tables only (no type-specific extensions)
--
-- DESIGN:
-- - item: Polymorphic timeline with JSON data field
-- - system_relation: Immutable structural facts
-- - Type-specific schemas in /schemas/types/items/*.schema.json

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
    ('base_schema', 'memogarden-soil-base', datetime('now')),
    ('description', 'Soil base tables only; type schemas in JSON', datetime('now')),
    ('epoch_date', '2020-01-01', datetime('now'));

-- ============================================================================
-- ITEM TABLE (Polymorphic timeline with JSON data)
-- ============================================================================

CREATE TABLE IF NOT EXISTS item (
    uuid TEXT PRIMARY KEY,              -- soil_ prefix
    _type TEXT NOT NULL,                -- 'Note', 'Message', 'Email', etc.
    realized_at TEXT NOT NULL,          -- ISO 8601 (system time)
    canonical_at TEXT NOT NULL,         -- ISO 8601 (user time)
    integrity_hash TEXT,                -- SHA256 of content
    fidelity TEXT NOT NULL DEFAULT 'full',
    superseded_by TEXT,
    superseded_at TEXT,
    
    -- Type-specific data (validated by /schemas/types/items/{_type}.schema.json)
    data JSON NOT NULL,
    
    -- Provider-specific metadata (not validated)
    metadata JSON
);

CREATE INDEX IF NOT EXISTS idx_item_type ON item(_type);
CREATE INDEX IF NOT EXISTS idx_item_realized ON item(realized_at);
CREATE INDEX IF NOT EXISTS idx_item_canonical ON item(canonical_at);
CREATE INDEX IF NOT EXISTS idx_item_fidelity ON item(fidelity);

-- ============================================================================
-- SYSTEM RELATION TABLE (Immutable structural facts)
-- ============================================================================

CREATE TABLE IF NOT EXISTS system_relation (
    uuid TEXT PRIMARY KEY,              -- soil_ prefix
    kind TEXT NOT NULL,                 -- 'triggers', 'cites', 'replies_to', etc.
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,          -- 'item' | 'entity'
    target TEXT NOT NULL,
    target_type TEXT NOT NULL,
    created_at INTEGER NOT NULL,        -- Days since epoch
    evidence JSON,
    metadata JSON,
    
    UNIQUE(kind, source, target)
);

CREATE INDEX IF NOT EXISTS idx_sysrel_source ON system_relation(source);
CREATE INDEX IF NOT EXISTS idx_sysrel_target ON system_relation(target);
CREATE INDEX IF NOT EXISTS idx_sysrel_kind ON system_relation(kind);

-- ============================================================================
-- COMMON QUERIES
-- ============================================================================

-- Q1: Get items by type
-- SELECT * FROM item WHERE _type = 'Email' ORDER BY canonical_at DESC;

-- Q2: Get timeline in date range
-- SELECT * FROM item 
-- WHERE canonical_at BETWEEN '2025-01-01' AND '2025-01-31'
-- ORDER BY canonical_at;

-- Q3: Find relations involving an item
-- SELECT * FROM system_relation WHERE source = ? OR target = ?;
