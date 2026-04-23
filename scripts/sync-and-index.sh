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

# Sync ~/.claude/docs/ (handoffs, plan docs) to QMD's docs collection on ai-utility.
# Phase 4's 5:30am nightly-synthesis.py reindexes QMD, so these files become
# searchable on that run. Safe no-op if the local dir doesn't exist.
CLAUDE_DOCS_LOCAL="${GENESIS_CLAUDE_DOCS:-$HOME/.claude/docs/}"
CLAUDE_DOCS_REMOTE="${GENESIS_CLAUDE_DOCS_REMOTE:-/opt/infra/data/qmd/claude-docs/}"
if [[ -d "$CLAUDE_DOCS_LOCAL" ]]; then
  log "Rsyncing $CLAUDE_DOCS_LOCAL -> $REMOTE_HOST:$CLAUDE_DOCS_REMOTE"
  rsync -az --delete "$CLAUDE_DOCS_LOCAL" "$REMOTE_HOST:$CLAUDE_DOCS_REMOTE" \
    || log "WARNING: claude-docs rsync failed (non-fatal)"
fi

# --- Phase 3: Faulkner relationship extractor ---------------------------------
# Poll /index/status until complete (indexing is async since v1.2.3), then run
# the extractor incrementally so Faulkner stays fresh for Phase 4 synthesis.
# Run-gate: skip if extraction_state.json was updated < 20h ago.

FAULKNER_REPO="${FAULKNER_REPO:-$HOME/project/faulkner-db}"
# Base URL for OpenAI-compatible LLM endpoint; extractor appends /models and /chat/completions
FAULKNER_LLM_ENDPOINT="${FAULKNER_LLM_ENDPOINT:-http://localhost:8084/v1}"
# FalkorDB connection. Defaults target a local dev setup; override via env in production.
FALKORDB_HOST="${FALKORDB_HOST:-localhost}"
FALKORDB_PORT="${FALKORDB_PORT:-6380}"
FALKORDB_PASSWORD="${FALKORDB_PASSWORD:-changeme}"
FALKORDB_GRAPH="${FALKORDB_GRAPH:-knowledge_graph}"
EXTRACTOR_STATE="$FAULKNER_REPO/reports/extraction_state.json"
RUN_GATE_HOURS=20
INDEX_POLL_MAX=900   # 15 min ceiling

poll_index_status() {
  local elapsed=0 state="unknown" raw
  while [ "$elapsed" -lt "$INDEX_POLL_MAX" ]; do
    raw=$(ssh -o ConnectTimeout=5 "$REMOTE_HOST" "curl -s $REMOTE_API_URL/index/status" 2>/dev/null || echo '{"status":"ssh_error"}')
    state=$(echo "$raw" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "parse_error")
    case "$state" in
      complete|failed|no_job|ssh_error|parse_error) echo "$state"; return 0 ;;
    esac
    sleep 15
    elapsed=$((elapsed + 15))
  done
  echo "timeout"
}

log "Polling /index/status for completion (timeout ${INDEX_POLL_MAX}s)"
INDEX_STATE=$(poll_index_status)
log "Indexing finished state: $INDEX_STATE"

if [[ "$INDEX_STATE" != "complete" && "$INDEX_STATE" != "no_job" ]]; then
  log "Skipping Faulkner extractor (indexing did not reach complete/no_job)"
else
  SKIP_EXTRACT=0
  if [ -f "$EXTRACTOR_STATE" ]; then
    age_s=$(( $(date +%s) - $(stat -c %Y "$EXTRACTOR_STATE") ))
    age_h=$(( age_s / 3600 ))
    if [ "$age_h" -lt "$RUN_GATE_HOURS" ]; then
      log "Extractor skipped — run-gate active (last run ${age_h}h ago, threshold ${RUN_GATE_HOURS}h)"
      SKIP_EXTRACT=1
    fi
  fi

  if [ "$SKIP_EXTRACT" = "0" ]; then
    if [ ! -f "$FAULKNER_REPO/venv/bin/activate" ]; then
      log "ERROR: Faulkner venv not found at $FAULKNER_REPO/venv — skipping extractor"
    else
      log "Running Faulkner relationship extractor (incremental, LLM endpoint $FAULKNER_LLM_ENDPOINT)"
      set +e
      (
        cd "$FAULKNER_REPO"
        # shellcheck disable=SC1091
        source venv/bin/activate
        FAULKNER_LLM_ENDPOINT="$FAULKNER_LLM_ENDPOINT" \
        FALKORDB_HOST="$FALKORDB_HOST" \
        FALKORDB_PORT="$FALKORDB_PORT" \
        FALKORDB_PASSWORD="$FALKORDB_PASSWORD" \
        FALKORDB_GRAPH="$FALKORDB_GRAPH" \
          python3 ingestion/relationship_extractor.py --incremental 2>&1
      ) | tee -a "$LOG_FILE"
      EXTRACT_RC=${PIPESTATUS[0]}
      set -e
      log "Extractor exit code: $EXTRACT_RC"
    fi
  fi
fi

log "=== Sync complete ==="
