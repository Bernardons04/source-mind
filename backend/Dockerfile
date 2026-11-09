# Production Dockerfile for SourceMind Backend (Render deployment)
FROM python:3.13-slim

# Set environment defaults for production
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8000 \
    HOST=0.0.0.0

WORKDIR /app

# Install system dependencies if required (clean cache immediately)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency specifications first to leverage Docker layer caching
COPY requirements.txt /app/requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy backend application source code
COPY . /app

# Expose default port (Render dynamically sets $PORT at runtime)
EXPOSE 8000

# Healthcheck targeting the lightweight health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:${PORT:-8000}/health || exit 1

# Start the unified process supervisor (runs FastAPI, Dramatiq, Dispatcher, Reconciler)
CMD ["python", "run_supervisor.py"]

