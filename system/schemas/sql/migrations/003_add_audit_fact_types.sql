-- Migration: Add Audit Fact Types (Action, ActionResult)
-- Version: 20260208
-- Description: Adds new Item types for Semantic API audit logging (RFC-005 v7 Section 7)
--
-- This migration doesn't change the database schema (Item table is polymorphic).
-- It documents the addition of two new Item types:
-- 1. Action - Records Semantic API operation invocation
-- 2. ActionResult - Records Semantic API operation completion
--
-- New System Relation Kind:
-- - result_of: Links ActionResult → Action
--
-- See Also:
-- - /schemas/types/items/action.schema.json - Action fact schema
-- - /schemas/types/items/actionresult.schema.json - ActionResult fact schema
-- - RFC-005 v7 Section 7 - Audit Facts specification
--
-- Usage:
-- These Item types are automatically created by the with_audit() decorator
-- in /api/handlers/decorators.py for all Semantic API operations.
--
-- Fossilization Policy:
-- - High-frequency operations (search): +7d retention
-- - Mutations (edits, adds): +30d retention
-- - Security events (permission denials): +1y retention

-- No schema changes needed - Item table already supports these types via _type field
-- This migration file exists for documentation purposes only

-- Update schema metadata to reflect new version
INSERT OR REPLACE INTO _schema_metadata (key, value, updated_at)
VALUES ('audit_facts_version', '20260208', datetime('now'));

-- Verify that result_of relation kind is documented
-- Note: system_relation.kind is TEXT, no ALTER TABLE needed
