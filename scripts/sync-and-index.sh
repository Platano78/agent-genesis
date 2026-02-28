#!/bin/bash
set -euo pipefail

# Agent Genesis - Sync Claude Code conversations and trigger remote indexing
# Rsyncs local Claude Code conversation JSONL files to the remote server,
# then triggers the indexing API so new conversations become searchable.
#
# Usage: ./sync-and-index.sh
# Configure via environment variables or edit defaults below.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${GENESIS_LOG_DIR:-$SCRIPT_DIR/../logs}"
LOG_FILE="$LOG_DIR/sync-and-index.log"
REMOTE_HOST="${GENESIS_REMOTE_HOST:-platano@ai-utility}"
REMOTE_DATA_DIR="${GENESIS_REMOTE_DATA_DIR:-/opt/infra/data/agent-genesis/claude-projects/}"
LOCAL_PROJECTS_DIR="${GENESIS_LOCAL_PROJECTS:-$HOME/.claude/projects/}"
API_URL="${AGENT_GENESIS_API_URL:-http://localhost:8080}"

mkdir -p "$LOG_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

log "=== Starting Agent Genesis sync ==="

rsync -az --delete --exclude='__pycache__/' --exclude='*.pyc' "$LOCAL_PROJECTS_DIR" "$REMOTE_HOST:$REMOTE_DATA_DIR" \
  || { log "ERROR: rsync sync failed"; exit 1; }
log "Rsync completed"

# Fire-and-forget: endpoint is synchronous and blocks for 10+ min
# Server continues indexing after curl disconnects; next run skips already-indexed
# FTS5 segment map auto-refreshes on each search (code fix in knowledge_db_dual.py)
log "Triggering indexing (fire-and-forget)"
curl -s -X POST "$API_URL/index/trigger" \
  -H "Content-Type: application/json" -d '{"full_reindex": false}' \
  --connect-timeout 5 --max-time 10 >/dev/null 2>&1 &
log "Index trigger sent, server will process in background"

log "=== Sync complete ==="
