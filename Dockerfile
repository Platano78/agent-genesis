# Agent Genesis - Multi-Phase Docker Image
# Supports Phase 1 (daemon) and Phase 2 (API server) modes

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    libleveldb-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 300 -r requirements.txt

# Install plyvel-ci for Python 3.11+ compatibility
RUN pip install --no-cache-dir --timeout 300 plyvel-ci

# Copy application code
COPY daemon/ ./daemon/

# Create data directories
RUN mkdir -p /app/data /app/knowledge /app/config

# Environment variables
ENV PYTHONPATH=/app
ENV APP_MODE=phase2
# Fix: chromadb_rust_bindings.abi3.so tokio background threads segfault under WSL2
# with large HNSW indices (>1M docs). Limiting tokio to 1 worker thread eliminates
# the thread race condition. See: github.com/chroma-core/chroma issues.
ENV TOKIO_WORKER_THREADS=1
ENV RAYON_NUM_THREADS=1

# Expose API port
EXPOSE 8080

# Health check - validates query capability, not just HTTP response
HEALTHCHECK --interval=30s --timeout=15s --start-period=90s --retries=3 \
  CMD curl -sf http://localhost:8080/ready | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='OK' else 1)" || exit 1

# Entry point with mode switching
CMD if [ "$APP_MODE" = "phase2" ]; then \
      echo "Starting Phase 2 API Server..."; \
      python -m daemon.api_server; \
    else \
      echo "Starting Phase 1 Daemon..."; \
      python -m daemon.main; \
    fi
