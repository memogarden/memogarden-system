-- Migration 20261128 -> 20261129 (api#105): api_keys.description — the
-- human "what is this key for, where does it live" field (name stays the
-- short identifier used in logs and audit records). Additive column;
-- existing rows are NULL. Fresh seeds carry it directly at 20261129
-- (root schemas be191b7). See core.sql §api_keys.

BEGIN IMMEDIATE;

ALTER TABLE api_keys ADD COLUMN description TEXT;

UPDATE _schema_metadata
SET value = '20261129', updated_at = datetime('now')
WHERE key = 'version';

COMMIT;
