# SourceMind Backend — Render Deployment Guide

This guide details how the SourceMind backend runs all four asynchronous components inside a single Docker container on **Render (Web Service)**.

---

## 1. Unified Container Architecture

To run within Render's Free Tier without losing any asynchronous RAG capabilities, the container utilizes a lightweight Python Process Supervisor (`run_supervisor.py`).

The supervisor coordinates four independent processes:
1. **FastAPI Web API:** `python run.py` (Uvicorn HTTP server listening on Render's dynamic `$PORT`).
2. **Dramatiq Worker:** `python -m dramatiq --processes 1 --threads 2 app.workers.document_tasks` (Worker actor parsing PDFs, Word documents, generating vector embeddings, and indexing).
3. **Outbox Dispatcher:** `python run_dispatcher.py` (Polls PostgreSQL `document_outbox` with `FOR UPDATE SKIP LOCKED` and publishes to RabbitMQ via Publisher Confirms).
4. **Reconciliation Service:** `python run_reconciler.py` (Periodic cleaner waking every 30 minutes to audit stuck uploads and perform compensatory storage deletions).

### Supervisor Lifecycle & Resilience
- **Real-Time Logging:** Streams output from all child processes to container stdout/stderr, tagged with `[api]`, `[worker]`, `[dispatcher]`, and `[reconciler]`.
- **Decoupled Signal Handling:** Signal handlers for `SIGTERM` and `SIGINT` asynchronously record the shutdown intent. The main supervisor loop executes the graceful shutdown cleanly, avoiding reentrant signal issues.
- **Fault Detection:** If any child process unexpectedly exits or crashes (even with exit code 0, as all four processes are intended to be standing daemons), the supervisor initiates a graceful shutdown of the remaining processes and exits with a non-zero exit code. This alerts Render's container orchestrator to restart the container cleanly.
- **Configurable Graceful Shutdown:** Controlled by `SUPERVISOR_GRACEFUL_TIMEOUT` (defaults to **25 seconds**). Because Render waits 30 seconds between sending `SIGTERM` and `SIGKILL`, the 25s default provides a 5-second safety buffer for child processes to finish active work (e.g. database commits or AMQP acknowledgements) before forced termination.

---

## 2. Health Endpoints & Monitoring

- **Application Health Endpoint:**
  - `GET /health` (and legacy alias `GET /api/health`)
  - Lightweight JSON response: `{"status": "healthy", "service": "sourcemind-backend"}`
  - Operates independently of database, RabbitMQ, or external API connectivity.
  - Requires no authentication.
- **Docker HEALTHCHECK vs. Render Health Check Path:**
  - **Docker `HEALTHCHECK`:** Defined in `Dockerfile` for local and container runtime orchestration (`curl -f http://127.0.0.1:${PORT:-8000}/health`).
  - **Render Dashboard Health Check Path:** Configured separately in Render's dashboard under Settings -> Health Check Path (`/health`). Render probes this HTTP endpoint from outside the container to determine if a deploy succeeded and whether traffic should be routed to the instance.
- **Uptime Monitoring:**
  - On Render Free, Web Services spin down after 15 minutes of HTTP inactivity.
  - To keep the instance responsive, configure **UptimeRobot Free** with an HTTP monitor pinging `https://[YOUR_APP].onrender.com/health`.
  - UptimeRobot's Free plan offers monitoring intervals down to **5 minutes** (well within Render's 15-minute sleep threshold).

---

## 3. Local Docker Build & Test Commands

### Build Image
```bash
cd backend
docker build -t sourcemind-backend:latest .
```

### Run Locally (Passing Environment Variables)
```bash
docker run --rm -it \
  -p 8000:8000 \
  -e PORT=8000 \
  -e DATABASE_URL="postgresql+asyncpg://..." \
  -e DIRECT_DATABASE_URL="postgresql+asyncpg://..." \
  -e RABBITMQ_URL="amqps://..." \
  -e SUPABASE_URL="https://..." \
  -e SUPABASE_PUBLISHABLE_KEY="..." \
  -e SUPABASE_SECRET_KEY="..." \
  -e SUPABASE_STORAGE_BUCKET="documents" \
  -e ENCRYPTION_KEY="..." \
  -e CORS_ORIGINS="http://localhost:3000" \
  sourcemind-backend:latest
```

---

## 4. Render Dashboard Settings

When creating the **Web Service** in Render:

| Field | Value |
| :--- | :--- |
| **Environment** | `Docker` |
| **Region** | Choose the region closest to your `SourceMind - PROD` Supabase project and CloudAMQP instance to minimize database and message broker latency |
| **Branch** | `main` |
| **Root Directory** | `backend` |
| **DockerfilePath** | `Dockerfile` |
| **Plan** | Free (0.1 CPU, 512 MB RAM) |
| **Health Check Path** | `/health` |

---

## 5. Required Production Environment Variables (Render Dashboard)

> **Security Requirement:** None of these variables or keys are baked into the Docker image or committed to Git. All must be configured directly in the Render Web Service Environment settings.

| Variable Name | Example / Notes |
| :--- | :--- |
| `DATABASE_URL` | `postgresql+asyncpg://postgres.[REF]:[PASS]@aws-0-[REGION].pooler.supabase.com:6543/postgres` (Supavisor Transaction Mode, IPv4) |
| `DIRECT_DATABASE_URL` | `postgresql+asyncpg://postgres.[REF]:[PASS]@aws-0-[REGION].pooler.supabase.com:5432/postgres` (Supavisor Session Mode on Port 5432 for advisory locks, IPv4) |
| `RABBITMQ_URL` | `amqps://[USER]:[PASS]@[HOST]/[VHOST]` (CloudAMQP Little Lemur URL) |
| `SUPABASE_URL` | `https://[PROJECT_REF].supabase.co` |
| `SUPABASE_PUBLISHABLE_KEY` | `sbp_...` or Anon Key |
| `SUPABASE_SECRET_KEY` | `sb_secret_...` or Service Role Key (used exclusively by backend for storage) |
| `SUPABASE_STORAGE_BUCKET`| `documents` |
| `ENCRYPTION_KEY` | URL-safe base64 32-byte Fernet key (generated once) |
| `CORS_ORIGINS` | `https://[YOUR_APP].vercel.app` (comma-separated for local development if needed) |
| `BACKEND_HOST` | `0.0.0.0` |
| `SUPERVISOR_GRACEFUL_TIMEOUT` | `25` (Optional, defaults to 25 seconds) |

---

## 6. Resource Limits & Memory Considerations

- **Render Free Tier Specifications:** **0.1 CPU and 512 MB RAM**.
- **Worker Concurrency Tuning:** To minimize memory consumption and CPU contention, Dramatiq is started with `--processes 1 --threads 2`.
- **Memory Consumption Profile:**
  - *Measured baseline:* A single idle FastAPI instance with loaded dependencies consumes ~50 MB RAM (measured via `tracemalloc`).
  - *Estimated operational footprint:* Four Python processes (API, Dramatiq worker, Dispatcher, and Reconciler) are estimated to consume between 200 MB and 300 MB under moderate idle/light load.
  - *Workload consideration:* Ingesting dense multi-page PDFs or Word documents temporarily increases memory usage due to PyPDF layout extraction, tiktoken tokenization, and vector payload buffers. Running multiple large uploads simultaneously may approach the 512 MB threshold; users should avoid submitting multiple large batches concurrently on the free tier.
