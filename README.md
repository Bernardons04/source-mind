# SourceMind — Enterprise AI Knowledge Platform

SourceMind is an AI-powered knowledge platform designed for businesses to create private, specialized AI assistants backed by organizational documentation. The system allows companies to ingest internal manuals, policies, and operating procedures, enabling assistants to answer questions using grounded document retrieval, contextual reasoning, and traceable citations. When internal documentation does not provide sufficient evidence to answer an organization-specific question, SourceMind communicates what could not be found rather than generating unsupported claims.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Product Experience & Screenshots](#product-experience--screenshots)
- [System Architecture](#system-architecture)
  - [High-Level Architecture Diagram](#high-level-architecture-diagram)
  - [Document Ingestion & Processing Pipeline](#document-ingestion--processing-pipeline)
  - [Agentic RAG & Hybrid Retrieval Flow](#agentic-rag--hybrid-retrieval-flow)
- [Tech Stack](#tech-stack)
- [Engineering Decisions](#engineering-decisions)
- [Repository Structure](#repository-structure)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Environment Variables](#environment-variables)
  - [Database & Storage Setup](#database--storage-setup)
  - [Running the Services](#running-the-services)
- [Testing](#testing)
- [Security & Reliability Architecture](#security--reliability-architecture)

---

## Overview

Modern companies maintain valuable institutional knowledge distributed across PDFs, operational guides, employee handbooks, and internal documentation. Off-the-shelf conversational LLMs lack access to private organizational context and can produce plausible but unverified statements when queried about company procedures.

**SourceMind addresses this challenge with a grounded, evidence-backed knowledge workflow:**
- **Flexible Contextual Reasoning:** The agent can converse naturally and address general queries, while autonomously identifying when internal documentation must be searched to resolve company-specific questions.
- **Traceable Document Grounding:** When the knowledge base is searched, the interface exposes retrieved document passages as traceable citations, including source metadata and excerpts.
- **Explicit Uncertainty & Abstention:** If a query requires company-specific context but the retrieved documentation does not provide adequate evidence, the assistant explicitly reports the absence of information.
- **User and Assistant Ownership:** Access is scoped to the authenticated user, with resource ownership enforced by the backend across assistants, chat histories, uploaded documents, and vector embeddings.

---

## Key Features

### 1. Multi-Assistant Workspace
- Configure independent assistants tailored to specific operational domains (such as HR policies, technical runbooks, or internal operations).
- Per-assistant custom behavioral guidelines, dynamic system prompts, and individual OpenRouter API keys encrypted at rest using Fernet (AES-128-CBC).
- Slug-based URL routing for isolated assistant workspaces (`/assistants/[slug]`).

### 2. Multi-Format Document Ingestion
- Document ingestion support for **PDF** (`.pdf`), Microsoft Word (**DOCX** — `.docx`), and plain text (**TXT** — `.txt`).
- Structural document parsing with heading hierarchy detection (numbered section markers and structural headings).
- Layout-aware extraction preserving table row structures, bullet list formatting, and joining artificial line breaks.
- Token-aware chunking based on `cl100k_base` token budgets with overlap windows and contextual metadata (page numbers and section headers).

### 3. Asynchronous Ingestion with Transactional Outbox
- **Transactional Outbox Pattern:** Document uploads follow a two-step flow: the document intention is first pre-registered in PostgreSQL with `upload_status='uploading'`. Once the binary file is successfully uploaded to Supabase Storage, the backend updates the status to `upload_status='uploaded'` and inserts the outbox event in PostgreSQL within the same database transaction, decoupling upload requests from message broker delivery.
- **Reliable Worker Dispatch:** Dedicated background dispatcher polls pending outbox entries (`FOR UPDATE SKIP LOCKED`) and publishes jobs to RabbitMQ using Publisher Confirms and persistent messages (`delivery_mode=2`).
- **Distributed Concurrency Control:** PostgreSQL 64-bit session advisory locks (`hashtextextended`) prevent duplicate concurrent workers from processing the same document.
- **Reconciliation Engine:** A self-healing background worker identifies abandoned uploads (> 2 hours) and performs compensatory cleanup in storage and database states.

### 4. Hybrid Retrieval Engine (Semantic + Lexical + RRF)
- **Semantic Vector Search:** Dense vector similarity using PostgreSQL's `pgvector` extension with cosine distance indexing (`vector(1536)`).
- **Lexical Full-Text Search:** Native PostgreSQL full-text search (`tsvector` and `tsquery`) with Portuguese text configuration and `ts_rank_cd` scoring.
- **Reciprocal Rank Fusion (RRF):** Merges semantic and lexical result sets ($RRF = \sum \frac{1}{k + rank}$) with constant $k=60$.
- **Graceful Search Degradation:** If either semantic or lexical search fails due to provider or query errors, the system continues with the operational retrieval channel, raising an error only if both fail.

### 5. Multi-Turn Agentic RAG Workflow
- Dynamic system prompts combining assistant identity, user guidelines, available knowledge base indexes, and live temporal references (date, weekday, timestamp, timezone).
- Autonomous tool execution (`search_knowledge_base`) within a controlled loop (configurable up to 3 iterations and 3 total tool calls).
- Structured response generation using strict JSON Schema validation through Pydantic models.
- Step-by-step execution event logging capturing agent cycles, tool invocations, and synthesis stages displayed in the interface.

### 6. Design System, Internationalization & Themes
- Built-in internationalization supporting **English (`en`)**, **Portuguese (`pt`)**, and **Spanish (`es`)**.
- Runtime theme toggle between light and dark modes driven by CSS custom properties.
- Interactive landing page interface with GSAP and ScrollTrigger animations.

---

## Product Experience & Screenshots

| Workspace & Assistants Grid | Chat Experience & Grounded Answers |
| :---: | :---: |
| ![Assistants Overview](frontend/public/images/landing/hero-assistants-grid.png) | ![Chat Interface](frontend/public/images/landing/hero-chat-preview.png) |
| *Manage multiple assistants and configure specific custom guidelines.* | *Conversational chat with execution event inspection and real-time streaming.* |

| Knowledge Base Ingestion | Verifiable Document Citations |
| :---: | :---: |
| ![Knowledge Base](frontend/public/images/landing/hero-knowledge-base.png) | ![Citation Drawer](frontend/public/images/landing/hero-citations-detail.png) |
| *Document uploads with asynchronous status tracking and chunk metrics.* | *Inspect exact source passages, page metadata, and chunk evidence.* |

---

## System Architecture

### High-Level Architecture Diagram

```mermaid
flowchart TD
    User([User / Browser])

    subgraph Frontend ["Frontend (Next.js 14 App Router)"]
        UI["React Server & Client Components"]
        I18N["i18n (EN / PT / ES) & Theme Engine"]
        SupabaseClient["Supabase Auth Client (OAuth Google)"]
    end

    subgraph Backend ["Backend (FastAPI & Python 3.13)"]
        AuthLayer["JWT Verification (Supabase Public JWKS)"]
        API["FastAPI REST Endpoints\n(/assistants, /documents, /chats, /preferences)"]
        AgentLoop["Agent Workflow & Tool Loop (Multi-Turn RAG)"]
        HybridRetriever["Hybrid Retriever (Semantic + Lexical + RRF)"]
        CryptoService["Fernet Symmetric Encryption / Decryption"]
    end

    subgraph AsyncPipeline ["Asynchronous Processing Subsystem"]
        Outbox["document_outbox Table (PostgreSQL)"]
        Dispatcher["Outbox Dispatcher (FOR UPDATE SKIP LOCKED)"]
        RabbitMQ[("RabbitMQ Broker (AMQP 5672)")]
        Worker["Dramatiq Worker (Document Ingestion Engine)"]
        Reconciler["Reconciler (Recovers Abandoned Uploads)"]
    end

    subgraph DataStorage ["Persistence & External Services"]
        SupabaseDB[("PostgreSQL with pgvector\n(Direct asyncpg connection)")]
        Storage[("Supabase Private Storage Bucket")]
        OpenRouter["OpenRouter API Gateway\n(Embeddings & Chat Completions)"]
    end

    User -->|Interacts| UI
    UI -->|Google OAuth Login| SupabaseClient
    UI -->|API Requests + Bearer JWT| AuthLayer
    AuthLayer --> API
    API -->|Async ORM / Direct Connection| SupabaseDB
    API -->|Upload File Binary| Storage
    API -->|Write Outbox Record| Outbox
    API --> AgentLoop

    Outbox -->|Poll & Claim Batch| Dispatcher
    Dispatcher -->|Publisher Confirms| RabbitMQ
    RabbitMQ -->|Deliver Ingestion Job| Worker
    Worker -->|Session Advisory Lock| SupabaseDB
    Worker -->|Download Binary File| Storage
    Worker -->|Generate Vector Embeddings| OpenRouter
    Worker -->|Insert Chunks & Vectors| SupabaseDB
    Reconciler -.->|Audit Stuck Uploads| SupabaseDB
    Reconciler -.->|Compensatory Deletion| Storage

    AgentLoop -->|Execute Tool: search_knowledge_base| HybridRetriever
    HybridRetriever -->|pgvector Cosine Search| SupabaseDB
    HybridRetriever -->|PostgreSQL Full-Text Search| SupabaseDB
    HybridRetriever -->|Generate Query Embedding| OpenRouter
    AgentLoop -->|Structured Answer Synthesis| OpenRouter
```

---

### Document Ingestion & Processing Pipeline

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Frontend as Next.js Client
    participant API as FastAPI Backend
    participant Storage as Supabase Storage
    participant DB as PostgreSQL (Outbox)
    participant Dispatcher as Outbox Dispatcher
    participant RMQ as RabbitMQ
    participant Worker as Dramatiq Worker
    participant AI as OpenRouter API

    User->>Frontend: Upload Document (PDF / DOCX / TXT)
    Frontend->>API: POST /api/assistants/:slug/documents
    API->>DB: Pre-register Document (upload_status='uploading')
    API->>Storage: Upload raw binary to private bucket
    API->>DB: Update (upload_status='uploaded') + INSERT document_outbox
    API-->>Frontend: 202 Accepted (Document metadata)

    loop Outbox Dispatching
        Dispatcher->>DB: SELECT * FROM document_outbox FOR UPDATE SKIP LOCKED
        Dispatcher->>RMQ: Publish message with Publisher Confirms (delivery_mode=2)
        Dispatcher->>DB: Mark outbox event as 'dispatched'
    end

    RMQ->>Worker: Deliver message to process_document_task
    Worker->>DB: Acquire 64-bit session advisory lock
    Worker->>Storage: Download raw document bytes
    Worker->>Worker: Extract text, normalize layout & chunk sections
    Worker->>AI: Generate vector embeddings
    Worker->>DB: INSERT document_chunks & UPDATE document (processing_status='indexed')
    Worker->>DB: Release session advisory lock
```

---

### Agentic RAG & Hybrid Retrieval Flow

```mermaid
flowchart TD
    Start([User sends chat message]) --> LoadHistory[Load conversation history & assistant instructions]
    LoadHistory --> PrepPrompt[Build dynamic prompt with temporal and document context]
    PrepPrompt --> LLMCall[LLM Call with Tool Definition: search_knowledge_base]

    LLMCall --> Decision{Did LLM decide to call retrieval tool?}

    Decision -- Yes --> ToolExec[Execute search_knowledge_base]
    ToolExec --> EmbedQ[Embed Query via OpenRouter]
    ToolExec --> ParseLex[Generate Portuguese tsquery]

    subgraph HybridSearchFlow [Hybrid Search Subsystem]
        direction TB
        EmbedQ --> SemSearch[Semantic Search: pgvector cosine distance]
        ParseLex --> LexSearch[Lexical Search: tsvector @@ tsquery]
        SemSearch & LexSearch --> RRF[Reciprocal Rank Fusion RRF Scoring]
    end

    RRF --> FeedToolResult[Append tool result payload to message history]
    FeedToolResult --> IterCheck{More iterations needed within limit?}
    IterCheck -- Yes --> LLMCall
    IterCheck -- Limit reached / finished --> Synthesis

    Decision -- No --> Synthesis[Phase 2: Strict JSON Schema Synthesis]
    Synthesis --> OutputValidation{Pydantic Schema Validation Passed?}
    OutputValidation -- Yes --> Persist[Persist Assistant Message, Citations & Execution Events]
    OutputValidation -- No --> RaiseErr[Raise Synthesis Validation Error]
    Persist --> End([Return assistant response with available citations and execution trace])
```

---

## Tech Stack

| Domain | Technology | Description |
| :--- | :--- | :--- |
| **Frontend Framework** | Next.js 14.2 (App Router) | React 18, React Server Components, TypeScript, Client state |
| **Frontend Styling** | Vanilla CSS Tokens + GSAP 3.15 | Custom CSS variable design tokens, responsive breakpoints, ScrollTrigger |
| **Backend Framework** | FastAPI (Python 3.13) | Asynchronous REST API, Pydantic v2 settings & validation, Uvicorn |
| **Database & ORM** | PostgreSQL 15+ via Supabase | SQLAlchemy 2.0 (asyncio) with `asyncpg` driver |
| **Vector Search** | `pgvector` | Cosine distance similarity indexing (`vector(1536)`) |
| **Task Queue & Broker** | Dramatiq + RabbitMQ 4 | Asynchronous background tasks, persistent messages, Pika client |
| **Model Gateway** | OpenRouter API Gateway | Unified gateway for chat completions and embedding generation |
| **Document Processing** | PyPDF, python-docx, tiktoken | Text extraction, layout parsing, and `cl100k_base` token accounting |
| **Authentication & Crypto** | Supabase Auth + Cryptography (Fernet) | Asymmetric JWKS JWT verification, AES-128-CBC key encryption at rest |
| **Testing** | Pytest 8.0+ / AnyIO | Automated unit and integration test suite (110 passing tests) |

---

## Engineering Decisions

### 1. Hybrid Retrieval + Reciprocal Rank Fusion (RRF)
- **Problem:** Pure vector search can miss precise keyword queries (such as product codes, acronyms, or specific section identifiers), while pure lexical full-text search struggles with paraphrased questions, synonyms, and semantic concepts.
- **Decision:** Combine `pgvector` cosine similarity with PostgreSQL native full-text search (`tsvector` / `tsquery`), fusing rank lists via Reciprocal Rank Fusion ($k=60$). This provides complementary retrieval capabilities and enables graceful fallback if one search method encounters an issue.

### 2. Transactional Outbox Pattern
- **Problem:** Uploading a document requires updating database records and notifying asynchronous workers. Directly publishing messages to RabbitMQ inside an HTTP request handler creates dual-write risks: if the message broker is unreachable or the database commit fails, database state and message state diverge.
- **Decision:** Uploads follow a two-step flow: the document is first pre-registered in PostgreSQL with `upload_status='uploading'`. Once the binary file is successfully uploaded to Supabase Storage, the backend updates the document status to `uploaded` and records an outbox event in PostgreSQL within the same database transaction. A separate background dispatcher claims pending outbox batches (`FOR UPDATE SKIP LOCKED`) and publishes them to RabbitMQ with Publisher Confirms, decoupling client HTTP requests from message broker delivery.

### 3. Background Asynchronous Document Processing
- **Problem:** Parsing multi-page PDFs and Word documents, cleaning structural layouts, generating chunk embeddings, and persisting vector records are compute- and I/O-intensive operations that would cause HTTP request timeouts if executed synchronously.
- **Decision:** Offload text extraction, chunking, and embedding generation to dedicated Dramatiq worker processes. Workers acquire PostgreSQL session advisory locks per document to prevent duplicate concurrent processing, keeping the API responsive and providing client status polling via HTTP 202 Accepted.

### 4. Observable Execution Traces & Structured Citations
- **Problem:** In enterprise knowledge tools, opaque answers without provenance reduce user trust and make debugging multi-turn agent decisions difficult.
- **Decision:** Capture structured execution events (`agent_iteration`, `tool_call`, `synthesis_complete`) and store individual citations referencing underlying `document_chunks`. This allows users to inspect retrieved source excerpts and inspect the agent's execution flow directly in the interface.

---

## Repository Structure

```
source-mind/
├── backend/
│   ├── app/
│   │   ├── api/                  # FastAPI router endpoints
│   │   │   ├── assistants.py     # Assistant management & API key encryption
│   │   │   ├── chats.py          # Chat threads, messages & citations
│   │   │   ├── documents.py      # Uploads, transactional outbox & status
│   │   │   └── preferences.py    # User theme & language preferences
│   │   ├── auth/                 # Authentication & security
│   │   │   ├── dependencies.py   # FastAPI user resolution dependency
│   │   │   └── jwt.py            # Supabase JWKS asymmetric signature verification
│   │   ├── migrations/           # Database schema migrations
│   │   ├── models/               # SQLAlchemy ORM models
│   │   │   └── all_models.py     # Assistant, Document, DocumentChunk, Outbox, Chat, etc.
│   │   ├── schemas/              # Pydantic validation schemas
│   │   │   ├── agent.py          # Agent input/output schemas
│   │   │   ├── all_schemas.py    # API request & response schemas
│   │   │   └── retrieval.py      # Retrieval diagnostic schemas
│   │   ├── services/             # Core business & algorithmic logic
│   │   │   ├── agent_tools.py    # Tool closure binding & execution schemas
│   │   │   ├── crypto.py         # Fernet symmetric encryption at rest
│   │   │   ├── document_parser.py# PDF, DOCX, TXT parsing & structural chunking
│   │   │   ├── openrouter.py     # OpenRouter client (chat completion & embeddings)
│   │   │   ├── rag_workflow.py   # Agent execution loop & structured synthesis
│   │   │   ├── retrieval.py      # Hybrid retriever (pgvector + FTS + RRF)
│   │   │   └── storage.py        # Supabase Storage client
│   │   ├── workers/              # Asynchronous queue workers
│   │   │   ├── broker.py         # Dramatiq RabbitMQ broker configuration
│   │   │   ├── dispatcher.py     # Transactional outbox batch dispatcher
│   │   │   ├── document_tasks.py # Async ingestion, chunking & vector persistence
│   │   │   └── reconciliation.py # Background cleaner for abandoned uploads
│   │   ├── config.py             # Pydantic BaseSettings configuration
│   │   ├── database.py           # SQLAlchemy async session & connection pool
│   │   ├── init_db.py            # Database schema initial bootstrap
│   │   └── main.py               # Application factory & CORS middleware
│   ├── tests/                    # Unit and integration test suite (110 tests)
│   ├── requirements.txt          # Backend Python dependencies
│   ├── run.py                    # Local web server runner
│   ├── run_dispatcher.py         # Outbox dispatcher entry point
│   └── run_reconciler.py         # Periodic reconciliation worker entry point
│
├── frontend/
│   ├── public/                   # Static assets & landing page screenshots
│   │   └── images/landing/       # Application interface preview images
│   ├── src/
│   │   ├── app/                  # Next.js 14 App Router routes
│   │   │   ├── assistants/       # Workspace & assistant pages
│   │   │   ├── auth/callback/    # OAuth callback handler
│   │   │   ├── login/            # Authentication view
│   │   │   ├── layout.tsx        # Root layout with context providers
│   │   │   └── page.tsx          # Marketing landing page
│   │   ├── components/           # UI components
│   │   │   ├── ChatInterface.tsx # Chat dialog, execution stream & citation links
│   │   │   ├── CitationsList.tsx # Detailed citation drawer & chunk inspector
│   │   │   ├── ExecutionEvents.tsx # Expandable RAG step trace visualization
│   │   │   ├── KnowledgeBaseView.tsx # Document management & progress cards
│   │   │   ├── Sidebar.tsx       # Assistant switcher & navigation
│   │   │   └── Topbar.tsx        # Profile, theme switch & language dropdown
│   │   ├── lib/                  # Utilities, Supabase client & context
│   │   │   ├── i18n/             # Translations (en.json, pt.json, es.json)
│   │   │   └── supabase/         # SSR and Client Supabase helpers
│   │   └── styles/               # CSS styles and theme variable definitions
│   └── package.json              # Frontend npm dependencies
│
├── docker-compose.yml            # Local RabbitMQ service definition
├── .env.example                  # Environment configuration template
└── README.md                     # Project documentation
```

---

## Getting Started

### Prerequisites

- **Python:** 3.12 or 3.13
- **Node.js:** 18.x or 20.x with npm
- **Docker & Docker Compose:** Required to run RabbitMQ locally
- **Supabase Project:** A Supabase project with PostgreSQL, Auth, and Storage enabled

---

### Environment Variables

Copy `.env.example` to `.env` in the root directory:

**macOS / Linux:**
```bash
cp .env.example .env
```

**Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
```

Configure the required variables in `.env`:

```ini
# PostgreSQL (Supabase Direct Connection with pgvector)
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres
DIRECT_DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@db.YOUR_PROJECT_REF.supabase.co:5432/postgres

# RabbitMQ Broker (AMQP 5672)
RABBITMQ_URL=amqp://YOUR_RABBITMQ_USER:YOUR_RABBITMQ_PASS@localhost:5672/
RABBITMQ_DEFAULT_USER=YOUR_RABBITMQ_USER
RABBITMQ_DEFAULT_PASS=YOUR_RABBITMQ_PASS

# Supabase Auth, Storage & JWT Configuration
SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
SUPABASE_SECRET_KEY=YOUR_SUPABASE_SECRET_KEY
SUPABASE_STORAGE_BUCKET=documents

# Symmetric Encryption Key for OpenRouter API Keys (Fernet URL-safe base64 32-byte key)
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
ENCRYPTION_KEY=YOUR_FERNET_ENCRYPTION_KEY

# Backend Server Configuration
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
CORS_ORIGINS=http://localhost:3000

# Frontend Public Variables
NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT_REF.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=YOUR_SUPABASE_PUBLISHABLE_KEY
NEXT_PUBLIC_API_URL=http://localhost:8000/api
```

> **Note on OpenRouter API Keys:** OpenRouter API keys are not supplied as global server environment variables. Instead, each assistant is configured with its own OpenRouter API key upon creation in the UI, which the backend encrypts at rest.

---

### Database & Storage Setup

1. **Enable Extensions in Supabase SQL Editor:**
   ```sql
   CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
   CREATE EXTENSION IF NOT EXISTS "vector";
   ```

2. **Initialize Database Tables:**
   ```bash
   cd backend
   # Ensure virtual environment is activated
   python -m app.init_db
   ```

3. **Configure Private Storage Bucket:**
   - In Supabase Dashboard, create a bucket named `documents`.
   - Ensure the bucket is set to **Private** (public access disabled).

4. **Enable Google OAuth Provider:**
   - In Supabase Dashboard under **Authentication > Providers**, enable Google OAuth.
   - Under **URL Configuration**, set Site URL to `http://localhost:3000` and add `http://localhost:3000/auth/callback` to Redirect URLs.

---

### Running the Services

#### 1. Start RabbitMQ Broker
```bash
docker compose up -d
```
Access the RabbitMQ Management UI at `http://localhost:15672` (using the credentials configured in `.env`).

#### 2. Start the Backend API
In a new terminal:
```bash
cd backend

# Activate virtual environment
# Windows PowerShell:
venv\Scripts\Activate.ps1
# Linux / macOS:
# source venv/bin/activate

pip install -r requirements.txt
python run.py
```
Backend API will be accessible at `http://localhost:8000` with Swagger documentation at `http://localhost:8000/docs`.

#### 3. Start Asynchronous Workers (Separate Terminals)

**Terminal A — Document Processing Worker (Dramatiq):**
```bash
cd backend
# Activate virtual environment
# Windows: venv\Scripts\Activate.ps1 | Linux/macOS: source venv/bin/activate
dramatiq app.workers.document_tasks
```

**Terminal B — Outbox Dispatcher:**
```bash
cd backend
# Activate virtual environment
# Windows: venv\Scripts\Activate.ps1 | Linux/macOS: source venv/bin/activate
python run_dispatcher.py
```

**Terminal C — Reconciler (Optional for local development):**
The reconciler is responsible for auditing stuck uploads (> 2h). For routine local development, running this worker is optional:
```bash
cd backend
# Activate virtual environment
# Windows: venv\Scripts\Activate.ps1 | Linux/macOS: source venv/bin/activate
python run_reconciler.py
```

#### 4. Start the Frontend Application
In a separate terminal:
```bash
cd frontend
npm install
npm run dev
```
The application will be live at `http://localhost:3000`.

---

## Testing

SourceMind includes an automated unit and integration test suite (110 passing tests) verifying core models, JWT authentication, migration execution, text normalization, transactional outbox publishing, retrieval ranking, and the agent loop:

```bash
cd backend
# With virtual environment activated:
pytest tests/
```

**Test Suite Coverage:**
- `test_etapa1_models_schemas.py`: Schema constraints, unique slugs, model relationships, and validations.
- `test_etapa2_async_outbox.py`: Dual-write outbox transactions, publisher confirms, idempotent delivery, and retry backoffs.
- `test_etapa5_agent_integration.py`: Agent execution loop, tool limits, schema enforcement, and citation payload generation.
- `test_jwt_auth.py`: Cryptographic JWKS verification, expiration checks, and audience validation.
- `test_migration_runner.py`: Schema migrations and versioning execution.
- `test_pdf_normalization_regression.py`: Section unwrapping, table integrity, and structural boundary preservation.
- `test_retrieval_service.py`: Hybrid search RRF score calculations, transaction savepoint isolation, and multi-assistant scoping.

---

## Security & Reliability Architecture

- **User and Assistant Isolation:** Access is scoped to the authenticated user, with resource ownership enforced by the backend across assistants, chat threads, documents, and vector embeddings.
- **Asymmetric JWT Verification:** User authentication tokens are cryptographically validated against Supabase's public JWKS endpoint (`/auth/v1/.well-known/jwks.json`), checking signatures, audience (`authenticated`), and expiration with leeway tolerance.
- **Encryption at Rest:** OpenRouter API keys provided for each assistant are encrypted using Fernet (AES-128-CBC) with a server-side encryption key before being stored in PostgreSQL, preventing plaintext exposure in database records.
- **Savepoint Isolation in Retrieval:** Database queries in `HybridRetriever` run within dedicated nested transactions (`begin_nested()`), ensuring that search errors do not invalidate the caller's outer database transaction.
- **Idempotency with Advisory Locks:** Document processing workers acquire a 64-bit PostgreSQL session advisory lock per document UUID (`hashtextextended`), preventing duplicate concurrent worker tasks from processing the same document.
