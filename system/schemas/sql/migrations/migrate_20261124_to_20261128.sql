-- Migration 20261124 -> 20261128 (provider-run tagging, operator directive
-- 2026-11-28): provider_runs gains the invoking principal and an external
-- app's self-supplied id.
--
--   triggered_by_key   api_keys.name of the authenticating key — the
--                      heartbeat lane tags itself; tada pushes tag as the
--                      tada-trigger key; agent keys tag as their agent's key.
--   external_app_id    verbatim value of the X-External-App-Id header on the
--                      trigger request, when the caller presents one. The
--                      app self-describes; we don't presume how it manages
--                      its automations. triggered_by vocabulary gains
--                      'external-app' for header-bearing triggers.
--
-- Additive columns only (NULL for existing rows — history stays as it was);
-- fresh seeds carry them directly at 20261128 (root schemas e64d2ae).
-- See core.sql §provider_runs.

BEGIN IMMEDIATE;

ALTER TABLE provider_runs ADD COLUMN triggered_by_key TEXT;
ALTER TABLE provider_runs ADD COLUMN external_app_id TEXT;

-- Stamp the new schema version
UPDATE _schema_metadata
SET value = '20261128', updated_at = datetime('now')
WHERE key = 'version';

COMMIT;
