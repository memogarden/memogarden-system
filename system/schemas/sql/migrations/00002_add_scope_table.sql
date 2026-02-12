-- Migration: Add Scope entity support
-- Version: 00002
-- Date: 2026-02-11
-- Description: Add Scope entity as first-class work container (RFC-003, RFC-009)

-- Scope entity will be stored in the entity table like all other entities
-- No additional tables needed - Scope data will be in entity.data JSON field

-- Update schema metadata
INSERT OR REPLACE INTO _schema_metadata (key, value, updated_at) VALUES
    ('entity_types', '["Artifact", "ContextFrame", "Label", "Scope", "Transaction", "User", "View", "ViewMerge"]', datetime('now'));

-- Note: The context_frame table already has a project_uuid column which can be used
-- for scope references. No schema changes needed to context_frame.

-- Migration complete marker
-- This file can be run via: sqlite3 core.db < migrations/00002_add_scope_table.sql
