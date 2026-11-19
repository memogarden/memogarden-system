-- Migration: 20261119 -> 20261120
-- Description: per-account scope policy — provider_accounts.scope_policy
--
-- RFC-011 §4.8 (item 4) policy affordance at the account level: an
-- account may carry its own {rules, default} scope policy that
-- REPLACES the provider-level scope_policy entirely when set (same
-- whole-object override semantics as provider_accounts.config, RFC-012
-- §4 — account config overrides provider config). NULL (the default
-- for all existing rows) = inherit the provider's policy — zero
-- behavior change for existing accounts.
--
-- Runs are strictly per-account (RFC-012 single-flight), so the
-- override resolves once per run and applies unconditionally — the
-- policy is a property of the ingesting account, not a rule matched
-- against item content (which the provider-level rules DSL remains
-- for).
--
-- Issuance/edit stays operator-gated: the account PATCH endpoint
-- requires a JWT when the PATCH includes scope_policy (mirrors
-- PUT /providers/{name}/scope-policy).
--
-- Note: fresh databases initialized from the core.sql seed (root-repo
-- schemas, pinned via the system/schemas/memogarden submodule) at
-- version 20261120 with the column already present.

-- Begin transaction for atomic migration
BEGIN;

-- Update schema version
UPDATE _schema_metadata
SET value = '20261120', updated_at = datetime('now')
WHERE key = 'version';

-- Update schema description
UPDATE _schema_metadata
SET value = 'Schema with entity registry, transactions, users, API keys (agent-bound RFC-010; grants RFC-011; restricted capability classes per E3), recurrences, providers (scope policies RFC-011 §4.8), provider accounts (per-account scope_policy override RFC-012), and exclusive scope activation (RFC-003 v5)', updated_at = datetime('now')
WHERE key = 'description';

ALTER TABLE provider_accounts ADD COLUMN scope_policy TEXT;

-- Commit migration
COMMIT;
