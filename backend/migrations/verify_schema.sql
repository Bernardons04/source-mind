-- ==============================================================================
-- ManualMind: Post-Migration Verification Queries
-- Run manually in the Supabase SQL Editor after applying migration 002.
-- These queries are informational only — they do not alter the schema.
-- ==============================================================================

-- 1. All tables in the public schema (document_versions must be absent)
SELECT table_name 
FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;

-- 2. Columns in documents (must include upload_status, storage_path, processing_status, etc.)
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'documents'
ORDER BY ordinal_position;

-- 3. Columns in document_chunks (must include document_id, must NOT include document_version_id)
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'document_chunks'
ORDER BY ordinal_position;

-- 4. Indexes on migrated tables
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE schemaname = 'public' AND tablename IN ('documents', 'document_chunks', 'document_outbox', 'assistants');

