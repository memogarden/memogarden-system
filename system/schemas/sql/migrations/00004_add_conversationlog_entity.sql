-- Migration: Add ConversationLog entity support
-- Version: 00004
-- Date: 2026-02-11
-- Description: Add ConversationLog entity for Project Studio conversation management

-- ConversationLog entity will be stored in entity table like all other entities
-- No additional columns needed - all data is in entity.data JSON field

-- Update entity_types list metadata to include ConversationLog
INSERT OR REPLACE INTO _schema_metadata (key, value) VALUES
    ('entity_types',
     '["Artifact", "ContextFrame", "ConversationLog", "Label", "Scope", "Transaction", "User", "View", "ViewMerge"]
    datetime('now')
    );
