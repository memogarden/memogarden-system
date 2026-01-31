-- Migration: 20251229 -> 20251230
-- Description: Add recurrences table for recurring transaction templates
--
-- Usage: Apply this migration to existing databases created with schema version 20251229
--
-- This migration adds:
--   - recurrences table (for recurring transaction templates)
--   - recurrences_view (convenient view joining recurrences + entity)
--   - Required indexes for performance

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20251230', updated_at = datetime('now')
WHERE key = 'version';

UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys, and recurrences', updated_at = datetime('now')
WHERE key = 'description';

-- Recurrences table (recurring transaction templates)
CREATE TABLE IF NOT EXISTS recurrences (
    id TEXT PRIMARY KEY,           -- References entity(id)
    rrule TEXT NOT NULL,           -- iCal rrule string (e.g., "FREQ=MONTHLY;BYDAY=2FR")
    entities TEXT NOT NULL,        -- JSON: transaction templates
    valid_from TEXT NOT NULL,      -- ISO 8601 datetime (inclusive start of recurrence window)
    valid_until TEXT,              -- ISO 8601 datetime (exclusive end of recurrence window, NULL = forever)

    FOREIGN KEY (id) REFERENCES entity(id) ON DELETE CASCADE
);

-- Index for recurrence validity queries
CREATE INDEX IF NOT EXISTS idx_recurrences_valid_from ON recurrences(valid_from);
CREATE INDEX IF NOT EXISTS idx_recurrences_valid_until ON recurrences(valid_until) WHERE valid_until IS NOT NULL;

-- Convenient view for querying recurrences with metadata
CREATE VIEW IF NOT EXISTS recurrences_view AS
SELECT
    r.*,
    e.created_at,
    e.updated_at,
    e.superseded_by,
    e.superseded_at,
    e.group_id,
    e.derived_from
FROM recurrences r
JOIN entity e ON r.id = e.id;

-- Commit migration
COMMIT;
