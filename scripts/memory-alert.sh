#!/bin/bash
# Agent Genesis Memory Alert Script
# Standalone memory monitoring - can be run independently or via cron
# Usage: ./memory-alert.sh [threshold_percent]
#
# Add to cron for proactive monitoring:
# */10 * * * * /home/platano/project/agent-genesis/scripts/memory-alert.sh 80

set -euo pipefail

CONTAINER_NAME="agent-genesis"
LOG_FILE="/tmp/agent-genesis-memory.log"
THRESHOLD=${1:-80}  # Default 80% threshold

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Check if container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    log "ERROR: Container $CONTAINER_NAME is not running"
    exit 1
fi

# Get memory stats
MEM_STATS=$(docker stats --no-stream --format "{{.MemUsage}}|{{.MemPerc}}" "$CONTAINER_NAME" 2>/dev/null)
MEM_USAGE=$(echo "$MEM_STATS" | cut -d'|' -f1)
MEM_PERC=$(echo "$MEM_STATS" | cut -d'|' -f2 | tr -d '%' | cut -d'.' -f1)

if [ -z "$MEM_PERC" ]; then
    log "ERROR: Could not get memory stats for $CONTAINER_NAME"
    exit 1
fi

# Log current status
log "Memory: $MEM_USAGE ($MEM_PERC%)"

# Check threshold
if [ "$MEM_PERC" -ge "$THRESHOLD" ]; then
    log "ALERT: Memory usage ($MEM_PERC%) exceeds threshold ($THRESHOLD%)!"
    log "Container details:"
    docker inspect "$CONTAINER_NAME" --format 'Memory Limit: {{.HostConfig.Memory}}' >> "$LOG_FILE"
    
    # Optional: Could add notification here (email, webhook, etc.)
    # curl -X POST "https://your-webhook-url" -d "message=Agent Genesis memory alert: $MEM_PERC%"
    
    exit 2  # Exit code 2 indicates threshold exceeded
fi

log "OK: Memory within threshold"
exit 0
