#!/bin/bash
# Agent Genesis Health Monitor
# Run via cron: */5 * * * * /path/to/monitor-health.sh

HEALTH_URL="http://localhost:8080/health"
LOG_FILE="/home/platano/project/agent-genesis/logs/health-monitor.log"
ALERT_FILE="/home/platano/project/agent-genesis/logs/alerts.log"

mkdir -p "$(dirname "$LOG_FILE")"

# Get health status
RESPONSE=$(curl -s -w "\n%{http_code}" "$HEALTH_URL" 2>/dev/null)
HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -n -1)

TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

# Log the check
echo "[$TIMESTAMP] HTTP: $HTTP_CODE" >> "$LOG_FILE"

# Check for issues
if [ "$HTTP_CODE" != "200" ]; then
    echo "[$TIMESTAMP] ALERT: Health check failed with HTTP $HTTP_CODE" >> "$ALERT_FILE"
    echo "[$TIMESTAMP] ALERT: Health check failed with HTTP $HTTP_CODE"
    exit 1
fi

# Check for warnings in response
if echo "$BODY" | grep -q '"warnings"'; then
    WARNINGS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('warnings', []))" 2>/dev/null)
    echo "[$TIMESTAMP] WARNING: $WARNINGS" >> "$ALERT_FILE"
    echo "[$TIMESTAMP] WARNING: $WARNINGS"
fi

# Check status
STATUS=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status', 'UNKNOWN'))" 2>/dev/null)
if [ "$STATUS" = "UNHEALTHY" ] || [ "$STATUS" = "DEGRADED" ]; then
    echo "[$TIMESTAMP] ALERT: Status is $STATUS" >> "$ALERT_FILE"
    echo "[$TIMESTAMP] ALERT: Status is $STATUS"

    # Get disk info
    DISK_MB=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('disk', {}).get('total_mb', 0))" 2>/dev/null)
    HNSW_MB=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('disk', {}).get('hnsw_mb', 0))" 2>/dev/null)
    echo "[$TIMESTAMP] Disk: ${DISK_MB}MB, HNSW: ${HNSW_MB}MB" >> "$ALERT_FILE"
    exit 1
fi

# Trim log files if too large (>10MB)
for f in "$LOG_FILE" "$ALERT_FILE"; do
    if [ -f "$f" ] && [ $(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null) -gt 10485760 ]; then
        tail -1000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"
    fi
done

echo "[$TIMESTAMP] OK - Status: $STATUS"
