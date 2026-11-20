-- Migration 20261120 -> 20261121 (RFC-008 v1.3, memogarden-system#6)
-- Adopt per-row hash integrity semantics:
--   1. Replace entity_update_timestamp with a writer-precedence guard.
--   2. Re-sign every entity once under the current hash format.
--
-- Why: entity hashes written before 2026-11 predate the current
-- compute_entity_hash field format, so they cannot be verified against
-- their rows (99.3% of live rows at migration time). Worse, the
-- unguarded entity_update_timestamp trigger stomped updated_at to
-- datetime('now') AFTER the writer chained its own isodatetime
-- timestamp into the hash — so updated rows carried hashes that never
-- matched their stored fields, invisible to the previous checker
-- (which tested previous_hash resolvability, an invariant no writer
-- path ever establishes: in-place edits chain the previous version's
-- hash into the current hash and store it in previous_hash, overwriting
-- the value it references; supersede never recorded the link at all).
-- The old hash values were unverifiable by construction, so re-signing
-- loses no detectable history.
--
-- Requires the sha256() SQL function (registered by the migration
-- runner), matching utils.hash_chain.compute_entity_hash's input
-- format with COALESCE for the `x or ''` None handling.

BEGIN;

-- Drop the auto-touch trigger BEFORE re-signing: the re-sign UPDATE
-- leaves updated_at unchanged, so even the guarded trigger would stomp
-- updated_at=datetime('now') and immediately desynchronize the fresh
-- hashes from the stored fields.
DROP TRIGGER IF EXISTS entity_update_timestamp;

-- Re-sign every entity from its stored fields
UPDATE entity SET hash = sha256(
    'type:' || type ||
    '|created_at:' || created_at ||
    '|updated_at:' || updated_at ||
    '|group_id:' || COALESCE(group_id, '') ||
    '|derived_from:' || COALESCE(derived_from, '') ||
    '|superseded_by:' || COALESCE(superseded_by, '') ||
    '|superseded_at:' || COALESCE(superseded_at, '') ||
    '|previous_hash:' || COALESCE(previous_hash, '')
);

-- Writer-precedence auto-touch: stomp updated_at only when the writer
-- did NOT set it. Sanctioned writers (update_hash / update_data /
-- supersede) set updated_at = isodatetime.now() and chain that exact
-- value into the hash; the guard keeps their hash and column
-- consistent while preserving auto-touch for other updates.
-- (Recreated AFTER the re-sign so it never sees it.)
CREATE TRIGGER IF NOT EXISTS entity_update_timestamp
AFTER UPDATE ON entity
    FOR EACH ROW
    WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE entity SET updated_at = datetime('now')
    WHERE uuid = NEW.uuid;
END;

-- Stamp the new schema version
UPDATE _schema_metadata
SET value = '20261121', updated_at = datetime('now')
WHERE key = 'version';

-- Update schema description
UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011; restricted capability classes per E3), recurrences, providers (scope policies RFC-011 §4.8), provider accounts (per-account scope_policy override RFC-012), exclusive scope activation (RFC-003 v5); entity hashes re-signed under per-row integrity semantics + guarded update timestamp trigger (RFC-008 v1.3, memogarden-system#6)', updated_at = datetime('now')
WHERE key = 'description';

COMMIT;
