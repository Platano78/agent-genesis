#!/bin/bash
# Agent Genesis ChromaDB Backup Script
# Run daily via cron: 0 2 * * * /path/to/agent-genesis/scripts/backup-chromadb.sh

set -euo pipefail

# Configuration - uses script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

BACKUP_DIR="${PROJECT_DIR}/backups"
LOG_FILE="/tmp/agent-genesis-backup.log"
RETENTION_COUNT=7

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

log "Starting ChromaDB backup..."

# Create backup using alpine container to access Docker volume directly
BACKUP_FILE="chromadb-$(date +%Y%m%d_%H%M%S).tar.gz"

if docker run --rm \
    -v agent-genesis_genesis-knowledge:/source:ro \
    -v "$BACKUP_DIR":/backup \
    alpine tar czf "/backup/$BACKUP_FILE" -C /source . 2>&1 | tee -a "$LOG_FILE"; then
    log "Backup created: $BACKUP_DIR/$BACKUP_FILE"
    log "Backup size: $(ls -lh "$BACKUP_DIR/$BACKUP_FILE" | awk '{print $5}')"
else
    log "ERROR: Backup failed!"
    exit 1
fi

# Rotate old backups - keep only last N backups
log "Rotating old backups (keeping last $RETENTION_COUNT)..."
BACKUP_COUNT=$(ls -t "$BACKUP_DIR"/chromadb-*.tar.gz 2>/dev/null | wc -l)
if [ "$BACKUP_COUNT" -gt "$RETENTION_COUNT" ]; then
    DELETED=$(ls -t "$BACKUP_DIR"/chromadb-*.tar.gz | tail -n +$((RETENTION_COUNT + 1)) | xargs -r rm -v)
    log "Deleted old backups: $DELETED"
else
    log "No old backups to delete ($BACKUP_COUNT backups exist)"
fi

log "Backup completed successfully!"
