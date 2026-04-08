#!/bin/bash
set -euo pipefail

# Agent Genesis - Sync Claude Code conversations and trigger remote indexing
# Rsyncs local Claude Code conversation JSONL files to the remote server,
# then triggers the indexing API so new conversations become searchable.
#
# Usage: ./sync-and-index.sh
# Required environment variables:
#   GENESIS_REMOTE_HOST      SSH target, e.g. user@example-host
# Optional environment variables:
#   GENESIS_REMOTE_DATA_DIR  Remote conversation directory
#   GENESIS_LOCAL_PROJECTS   Local Claude Code projects directory
#   GENESIS_REMOTE_API_URL   API URL reachable from the remote host
#   GENESIS_POST_SYNC_COMMAND Optional command to run on the remote host after indexing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${GENESIS_LOG_DIR:-$SCRIPT_DIR/../logs}"
LOG_FILE="$LOG_DIR/sync-and-index.log"
REMOTE_HOST="${GENESIS_REMOTE_HOST:-}"
REMOTE_DATA_DIR="${GENESIS_REMOTE_DATA_DIR:-/opt/infra/data/agent-genesis/claude-projects/}"
LOCAL_PROJECTS_DIR="${GENESIS_LOCAL_PROJECTS:-$HOME/.claude/projects/}"
REMOTE_API_URL="${GENESIS_REMOTE_API_URL:-http://127.0.0.1:8080}"
POST_SYNC_COMMAND="${GENESIS_POST_SYNC_COMMAND:-}"

mkdir -p "$LOG_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

if [[ -z "$REMOTE_HOST" ]]; then
  echo "Set GENESIS_REMOTE_HOST to the SSH target for your Agent Genesis server." >&2
  echo "Example: GENESIS_REMOTE_HOST=user@example-host ./scripts/sync-and-index.sh" >&2
  exit 1
fi

if [[ ! -d "$LOCAL_PROJECTS_DIR" ]]; then
  echo "Local Claude Code projects directory not found: $LOCAL_PROJECTS_DIR" >&2
  exit 1
fi

log "=== Starting Agent Genesis sync ==="

rsync -az --delete --exclude='__pycache__/' --exclude='*.pyc' "$LOCAL_PROJECTS_DIR" "$REMOTE_HOST:$REMOTE_DATA_DIR" \
  || { log "ERROR: rsync sync failed"; exit 1; }
log "Rsync completed"

# Trigger indexing from the remote host so the API can stay bound to localhost.
log "Triggering indexing via SSH"
TRIGGER_RESULT=$(ssh -o ConnectTimeout=5 "$REMOTE_HOST" \
  "curl -s -X POST $REMOTE_API_URL/index/trigger -H 'Content-Type: application/json' -d '{\"full_reindex\": false}'" 2>&1) \
  || TRIGGER_RESULT="ERROR: SSH/curl failed"
log "Index trigger result: $TRIGGER_RESULT"

if [[ -n "$POST_SYNC_COMMAND" ]]; then
  log "Running optional post-sync command"
  POST_SYNC_RESULT=$(ssh -o ConnectTimeout=5 "$REMOTE_HOST" "$POST_SYNC_COMMAND" 2>&1) \
    || POST_SYNC_RESULT="ERROR: post-sync command failed"
  log "Post-sync result: $POST_SYNC_RESULT"
fi

log "=== Sync complete ==="
