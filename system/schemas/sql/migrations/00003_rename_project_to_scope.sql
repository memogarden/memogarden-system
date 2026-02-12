-- Migration: Rename project_uuid to scope_uuid in context_frame
-- Version: 00003
-- Date: 2026-02-11
-- Description: Align context_frame schema with RFC-003 terminology (Scope, not Project)

-- Create new column
ALTER TABLE context_frame ADD COLUMN scope_uuid TEXT;

-- Migrate data from project_uuid to scope_uuid
UPDATE context_frame SET scope_uuid = project_uuid WHERE project_uuid IS NOT NULL;

-- Mark old column for deletion (drop after verification)
-- Note: SQLite doesn't support DROP COLUMN in ALTER TABLE, so we leave it
-- The column should be ignored in favor of scope_uuid going forward

-- Migration complete marker
