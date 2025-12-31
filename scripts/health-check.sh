#!/bin/bash
# Agent Genesis Health Check Script
# Run every 5 minutes via cron: */5 * * * * /home/platano/project/agent-genesis/scripts/health-check.sh

set -euo pipefail

# Configuration - uses script directory for relative paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

LOG_FILE="/tmp/agent-genesis-health.log"
HEALTH_URL="http://localhost:8080/health"
CONTAINER_NAME="agent-genesis"
BACKUP_DIR="${PROJECT_DIR}/backups"
BACKUP_SCRIPT="${SCRIPT_DIR}/backup-chromadb.sh"
MAX_RESTARTS=3
MEMORY_THRESHOLD=80  # Alert if memory usage exceeds this percentage

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

# Check container memory usage
check_memory() {
    MEM_USAGE=$(docker stats --no-stream --format "{{.MemPerc}}" "$CONTAINER_NAME" 2>/dev/null | tr -d '%' | cut -d'.' -f1)
    MEM_USED=$(docker stats --no-stream --format "{{.MemUsage}}" "$CONTAINER_NAME" 2>/dev/null | cut -d'/' -f1)
    MEM_LIMIT=$(docker stats --no-stream --format "{{.MemUsage}}" "$CONTAINER_NAME" 2>/dev/null | cut -d'/' -f2)
    
    if [ -n "$MEM_USAGE" ]; then
        log "Memory: ${MEM_USED}/${MEM_LIMIT} (${MEM_USAGE}%)"
        if [ "$MEM_USAGE" -ge "$MEMORY_THRESHOLD" ]; then
            log "WARNING: Memory usage at ${MEM_USAGE}% - exceeds ${MEMORY_THRESHOLD}% threshold!"
            log "ACTION: Consider increasing container memory limit or investigating memory leak"
        fi
    fi
}

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "WARNING: Container $CONTAINER_NAME is not running. Attempting to start..."
    docker start "$CONTAINER_NAME" 2>&1 >> "$LOG_FILE" || {
        log "ERROR: Failed to start container $CONTAINER_NAME"
        exit 1
    }
    log "Container started successfully"
    sleep 10  # Give it time to initialize
fi

# Check health endpoint
if ! curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    log "ALERT: Agent Genesis health check failed!"
    
    # Count recent restarts to avoid restart loops
    RECENT_RESTARTS=$(grep -c "Restarting container" "$LOG_FILE" 2>/dev/null | tail -100 || echo 0)
    if [ "$RECENT_RESTARTS" -ge "$MAX_RESTARTS" ]; then
        log "ERROR: Too many restarts ($RECENT_RESTARTS) - manual intervention required"
        exit 1
    fi
    
    # Restart the container
    log "Restarting container $CONTAINER_NAME..."
    if docker restart "$CONTAINER_NAME" 2>&1 >> "$LOG_FILE"; then
        log "Container restarted successfully"
    else
        log "ERROR: Failed to restart container"
        exit 1
    fi
fi

# Check memory usage
check_memory

# Check if today's backup exists - if not, create one
TODAY=$(date +%Y%m%d)
if ! ls "$BACKUP_DIR"/chromadb-${TODAY}*.tar.gz >/dev/null 2>&1; then
    log "No backup found for today ($TODAY). Triggering backup..."
    if [ -x "$BACKUP_SCRIPT" ]; then
        "$BACKUP_SCRIPT" >> "$LOG_FILE" 2>&1 && log "Catch-up backup completed" || log "WARNING: Catch-up backup failed"
    else
        log "WARNING: Backup script not found or not executable"
    fi
fi

exit 0
