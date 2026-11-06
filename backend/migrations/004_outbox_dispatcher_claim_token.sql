-- ==============================================================================
-- ManualMind: Migration 004 - Outbox Claim Token
-- Purpose:
--   Add claim_token column to document_outbox for fencing stale dispatcher workers.
--
--   The status constraint (pending, processing, dispatched, failed) was already
--   defined correctly in migration 002. This migration adds only the claim_token
--   column required by the dispatcher fencing mechanism.
--
-- Executed by: backend/app/migrations/runner.py (asyncpg)
-- Transaction control: managed by the runner (do NOT add BEGIN/COMMIT here).
-- ==============================================================================

ALTER TABLE public.document_outbox
    ADD COLUMN IF NOT EXISTS claim_token UUID;
