-- ==============================================================================
-- ManualMind: Migration 003 - Disable Row Level Security on document_outbox
-- Purpose: Keep multi-tenant isolation and authorization strictly at the application layer.
--          The document_outbox table is an internal worker/backend table.
--          REVOKE ALL FROM anon, authenticated remains active for SQL privilege security.
--
-- Executed by: backend/app/migrations/runner.py (asyncpg)
-- Transaction control: managed by the runner (do NOT add BEGIN/COMMIT here).
-- ==============================================================================

ALTER TABLE document_outbox DISABLE ROW LEVEL SECURITY;

