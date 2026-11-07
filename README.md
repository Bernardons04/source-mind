# SourceMind — Private AI Assistant Platform

SourceMind is a web application of grounded AI assistants that answer questions based strictly on users' private documents. Every response is anchored in real evidence retrieved through hybrid search (semantic similarity + lexical search), with verifiable citations, zero-hallucination abstention policies, document versioning, and observable LangGraph execution traces.

---

## 1. Architecture Overview

### Frontend
- **Framework:** Next.js 14 (App Router) with TypeScript.
- **Styling:** Vanilla CSS design system matching the exact visual identity of the concept prototype (`manualmind-prototype-v2.html`), using custom design tokens, dark & light theme modes, and responsive breakpoints.
- **Components:** React Server Components by default; Client Components used specifically for interactive state (Chat, Knowledge Base uploads, Assistant picker, and Theme switcher).
- **i18n:** Built-in internationalization supporting **English (`en`)**, **Portuguese (`pt`)**, and **Spanish (`es`)**.
- **Auth:** Supabase Auth with Google OAuth provider.

### Backend
- **Framework:** FastAPI with Python 3.13.
- **RAG & Orchestration:** LangGraph state graph with query analysis, pgvector + PostgreSQL full-text hybrid search (Reciprocal Rank Fusion), evidence evaluation, response generation, and grounded validation.
- **Database:** Supabase PostgreSQL accessed **exclusively via direct asynchronous PostgreSQL connection (`asyncpg` + async SQLAlchemy)**.
- **Vector Search:** `pgvector` with HNSW cosine similarity indexing (`vector(1536)`).
- **Security:**
  - **No `service_role` key is used anywhere in the application.**
  - Supabase JWT access tokens are cryptographically verified on every private request (`user_id` is never trusted from the client).
  - OpenRouter API keys are encrypted at rest with Fernet (AES-128-CBC) using an encryption key stored exclusively in backend environment variables.
- **Storage:** Supabase Storage private bucket (authenticated via user's bearer token).

---

## 2. Environment Variables

Create `.env` in the project root (or separate `.env` files in `backend/` and `frontend/`):

```bash
# Direct PostgreSQL connection string with pgvector extension enabled
# (Direct Supabase connection, NOT PostgREST)
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_DB_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres

# Supabase Auth & Storage configuration
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_ANON_KEY=YOUR_SUPABASE_ANON_KEY
# Tokens are verified via public JWKS ({SUPABASE_URL}/auth/v1/.well-known/jwks.json).
# SUPABASE_JWT_SECRET is optional and only needed for legacy projects using symmetric HS256 tokens.
# SUPABASE_JWT_SECRET=optional_legacy_only
SUPABASE_STORAGE_BUCKET=documents

# Symmetric encryption key for OpenRouter API keys (Fernet base64 32-byte key)
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=YOUR_FERNET_ENCRYPTION_KEY

# Backend server configuration
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
CORS_ORIGINS=http://localhost:3000

# Frontend public variables
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=YOUR_SUPABASE_ANON_KEY
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

---

## 3. Supabase Configuration

### 1. Database & Extensions
In your Supabase project SQL Editor, run the migration script:
```sql
-- Located in backend/migrations/001_initial_schema.sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
```
Or execute the automated schema initialization script from the backend:
```bash
cd backend
venv\Scripts\python -m app.init_db
```

### 2. Google OAuth
1. In the Supabase Dashboard, go to **Authentication** > **Providers** > **Google**.
2. Enable Google and enter your Google OAuth **Client ID** and **Client Secret** (from Google Cloud Console).
3. In **URL Configuration**, set the **Site URL** to `http://localhost:3000` and add `http://localhost:3000/auth/callback` to **Redirect URLs**.

### 3. Private Storage Bucket
1. In the Supabase Dashboard, go to **Storage** > **New bucket**.
2. Name the bucket `documents`.
3. Set the bucket to **Private** (Disable "Public bucket").

---

## 4. Running the Application Locally

### Running Backend (FastAPI)
```bash
# 1. From the project root, enter backend directory
cd backend

# 2. Activate virtual environment
# Windows PowerShell:
venv\Scripts\Activate.ps1
# Linux / macOS:
# source venv/bin/activate

# 3. Apply database tables (if not already applied)
python -m app.init_db

# 4. Start the FastAPI development server
python run.py
# Backend runs at http://localhost:8000
```

### Running Frontend (Next.js)
```bash
# 1. From the project root, enter frontend directory
cd frontend

# 2. Start Next.js development server
npm run dev
# Frontend runs at http://localhost:3000
```

---

## 5. Key Verified Features

1. **Grounded RAG with LangGraph:**
   - Query keyword extraction & analysis.
   - Hybrid search combining pgvector semantic similarity (`vector_cosine_ops`) and PostgreSQL Full-Text Search via Reciprocal Rank Fusion (`RRF`).
   - Grounded LLM generation with explicit `[[SOURCE_X]]` anchors.
   - Automatic abstention when knowledge base evidence is insufficient.
   - Contradiction detection between conflicting sources.
2. **Verifiable Citations & Execution Inspection:**
   - Every citation references actual `document_chunks` with page number and excerpt.
   - Expandable execution traces allow inspecting each LangGraph step and payload.
3. **Document Ingestion & Versioning:**
   - Supports PDF, DOCX, and TXT files.
   - Version history is preserved when replacing documents (`current_version_number`).
   - Reprocessing feature allows re-extracting and re-indexing without re-uploading original files.
4. **Security & Data Isolation:**
   - Data strictly isolated by authenticated `user_id`.
   - Never exposes OpenRouter keys to frontend.
   - No `service_role` key used.
5. **Aesthetics & UX:**
   - Faithfully reproduces `manualmind-prototype-v2.html` design system, typography, colors, layout, and micro-interactions.
   - Real-time dark / light theme switcher.
   - Multilingual interface supporting English, Portuguese, and Spanish.
