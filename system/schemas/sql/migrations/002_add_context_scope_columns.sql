-- Migration 002: Add RFC-003 scope activation columns to context_frame table
-- Date: 2026-02-08
-- Description: Adds active_scopes and primary_scope columns for context verbs

-- Add active_scopes column (JSON array of scope UUIDs)
ALTER TABLE context_frame ADD COLUMN active_scopes JSON NOT NULL DEFAULT '[]';

-- Add primary_scope column (currently focused scope, NULL = no primary)
ALTER TABLE context_frame ADD COLUMN primary_scope TEXT;

-- Create index for primary_scope lookups
CREATE INDEX IF NOT EXISTS idx_context_frame_primary_scope ON context_frame(primary_scope);
