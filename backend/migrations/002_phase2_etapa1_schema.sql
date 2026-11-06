-- ==============================================================================
-- ManualMind: Phase 2 - Etapa 1 Database Migration Script
-- Purpose: Unify document model (eliminate document_versions), re-point document_chunks,
--          add document_outbox table, add deletion_status to assistants, and update FTS to Portuguese.
--
-- Executed by: backend/app/migrations/runner.py (asyncpg)
-- Transaction control: managed by the runner (do NOT add BEGIN/COMMIT here).
-- Verification: see backend/migrations/verify_schema.sql
-- ==============================================================================


-- ==============================================================================
-- SECTION 1: VALIDATION BLOCK
-- Verifies that pre-requisite tables exist before making changes.
-- ==============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'documents') THEN
        RAISE EXCEPTION 'Table public.documents is missing. Cannot apply Etapa 1 migration.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'document_chunks') THEN
        RAISE EXCEPTION 'Table public.document_chunks is missing. Cannot apply Etapa 1 migration.';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'assistants') THEN
        RAISE EXCEPTION 'Table public.assistants is missing. Cannot apply Etapa 1 migration.';
    END IF;
END $$;


-- ==============================================================================
-- SECTION 2: SCHEMA MIGRATION
-- Compatible with asyncpg runner (transaction managed externally by runner.py)
-- ==============================================================================

-- 1. Discard test/ephemeral data across citation and document tables
DO $$
BEGIN
    DELETE FROM message_citations;
    DELETE FROM document_chunks;
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'document_versions') THEN
        EXECUTE 'DELETE FROM public.document_versions;';
    END IF;
    DELETE FROM documents;
END $$;

-- 2. Adjust document_chunks foreign key and columns
ALTER TABLE document_chunks DROP CONSTRAINT IF EXISTS document_chunks_document_version_id_fkey;
DROP INDEX IF EXISTS idx_doc_chunks_version_idx;
DROP INDEX IF EXISTS idx_doc_chunks_fts;
DROP INDEX IF EXISTS idx_doc_chunks_embedding;

-- Drop obsolete column and point directly to documents(id)
ALTER TABLE document_chunks DROP COLUMN IF EXISTS document_version_id;
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE;

-- 3. Safely drop obsolete document_versions table (no CASCADE needed, chunks already re-pointed)
DROP TABLE IF EXISTS document_versions;

-- 4. Recreate document_chunks indexes
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc_idx ON document_chunks(document_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_fts ON document_chunks USING gin (to_tsvector('portuguese', content));
CREATE INDEX IF NOT EXISTS idx_doc_chunks_embedding ON document_chunks USING hnsw (embedding vector_cosine_ops);

-- 5. Refactor documents table: remove versioning, add direct metadata & statuses
ALTER TABLE documents DROP COLUMN IF EXISTS current_version_number;

ALTER TABLE documents 
    ADD COLUMN IF NOT EXISTS upload_status VARCHAR(20) NOT NULL DEFAULT 'uploading',
    ADD COLUMN IF NOT EXISTS storage_path VARCHAR(500) NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS mime_type VARCHAR(100) NOT NULL DEFAULT 'application/octet-stream',
    ADD COLUMN IF NOT EXISTS file_size BIGINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64) NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000',
    ADD COLUMN IF NOT EXISTS extracted_text TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS processing_status VARCHAR(50) NOT NULL DEFAULT 'pending_upload',
    ADD COLUMN IF NOT EXISTS processing_error TEXT;

-- Remove temporary defaults used for safe column addition (data was wiped)
ALTER TABLE documents ALTER COLUMN storage_path DROP DEFAULT;
ALTER TABLE documents ALTER COLUMN mime_type DROP DEFAULT;
ALTER TABLE documents ALTER COLUMN file_size DROP DEFAULT;
ALTER TABLE documents ALTER COLUMN content_hash DROP DEFAULT;

-- Add strict CHECK constraints idempotently via DO $$ block
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.documents'::regclass AND conname = 'ck_documents_upload_status') THEN
        ALTER TABLE documents ADD CONSTRAINT ck_documents_upload_status 
            CHECK (upload_status IN ('uploading', 'uploaded', 'failed'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.documents'::regclass AND conname = 'ck_documents_processing_status') THEN
        ALTER TABLE documents ADD CONSTRAINT ck_documents_processing_status 
            CHECK (processing_status IN ('pending_upload', 'pending', 'processing', 'indexed', 'failed'));
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.documents'::regclass AND conname = 'ck_documents_storage_path_not_empty') THEN
        ALTER TABLE documents ADD CONSTRAINT ck_documents_storage_path_not_empty 
            CHECK (length(storage_path) > 0);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.documents'::regclass AND conname = 'ck_documents_mime_type_not_empty') THEN
        ALTER TABLE documents ADD CONSTRAINT ck_documents_mime_type_not_empty 
            CHECK (length(mime_type) > 0);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.documents'::regclass AND conname = 'ck_documents_file_size_non_negative') THEN
        ALTER TABLE documents ADD CONSTRAINT ck_documents_file_size_non_negative 
            CHECK (file_size >= 0);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.documents'::regclass AND conname = 'ck_documents_content_hash_len') THEN
        ALTER TABLE documents ADD CONSTRAINT ck_documents_content_hash_len 
            CHECK (length(content_hash) = 64);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_documents_processing_status ON documents(processing_status);
CREATE INDEX IF NOT EXISTS idx_documents_upload_status ON documents(upload_status);

-- 6. Add deletion_status to assistants idempotently
ALTER TABLE assistants 
    ADD COLUMN IF NOT EXISTS deletion_status VARCHAR(20) NOT NULL DEFAULT 'active';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid = 'public.assistants'::regclass AND conname = 'ck_assistants_deletion_status') THEN
        ALTER TABLE assistants ADD CONSTRAINT ck_assistants_deletion_status 
            CHECK (deletion_status IN ('active', 'deleting'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_assistants_deletion_status ON assistants(deletion_status);

-- 7. Create transactional outbox table
CREATE TABLE IF NOT EXISTS document_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    retry_count INT NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    dispatched_at TIMESTAMPTZ,
    CONSTRAINT ck_document_outbox_status CHECK (status IN ('pending', 'processing', 'dispatched', 'failed')),
    CONSTRAINT ck_document_outbox_retry_count_non_negative CHECK (retry_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_document_outbox_pending_retry ON document_outbox(status, next_retry_at);

-- 8. Protect document_outbox with Row Level Security (internal worker table only)
ALTER TABLE document_outbox ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON document_outbox FROM anon, authenticated;
