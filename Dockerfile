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
RUN pip install --no-cache-dir -r requirements.txt

# Install plyvel-ci for Python 3.11+ compatibility
RUN pip install --no-cache-dir plyvel-ci

# Copy application code
COPY daemon/ ./daemon/

# Create data directories
RUN mkdir -p /app/data /app/knowledge /app/config

# Environment variables
ENV PYTHONPATH=/app
ENV APP_MODE=phase2

# Expose API port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

# Entry point with mode switching
CMD if [ "$APP_MODE" = "phase2" ]; then \
      echo "Starting Phase 2 API Server..."; \
      python -m daemon.api_server; \
    else \
      echo "Starting Phase 1 Daemon..."; \
      python -m daemon.main; \
    fi
