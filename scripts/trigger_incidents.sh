#!/bin/bash
#
# Aether Guard — Incident Trigger Script
#
# This script triggers various failure scenarios to generate test incidents.
# Each scenario sends alerts directly to Alertmanager.

set -e

ALERTMANAGER_URL="http://localhost:9093/api/v1/alerts"
TARGET_SERVICE="target-service:8080"

echo "🚀 Triggering test incidents in Aether Guard..."
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Scenario 1: OOM Kill (Memory exhaustion)
# ─────────────────────────────────────────────────────────────────────────────
echo "📊 Scenario 1: OOM Kill (Memory Exhaustion)"
echo "   Expected: Agent should detect OOM pattern and recommend RESTART"

curl -s -X POST "$ALERTMANAGER_URL" \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "HighMemoryUsage",
      "severity": "critical",
      "service": "api-gateway",
      "instance": "api-gateway-pod-1",
      "container": "api-gateway"
    },
    "annotations": {
      "summary": "Memory usage above 90% for api-gateway",
      "description": "Container api-gateway on api-gateway-pod-1 is using 92% memory"
    },
    "startsAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "endsAt": "'"$(date -u -v+1H +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'

echo "   ✅ OOM alert sent"
echo ""
sleep 2

# ─────────────────────────────────────────────────────────────────────────────
# Scenario 2: Rate Limit / 503 Errors
# ─────────────────────────────────────────────────────────────────────────────
echo "📊 Scenario 2: Rate Limit (HTTP 503 errors)"
echo "   Expected: Agent should detect rate limit pattern and recommend SCALE"

curl -s -X POST "$ALERTMANAGER_URL" \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "HighErrorRate",
      "severity": "warning",
      "service": "payment-service",
      "instance": "payment-service-1",
      "status_code": "503"
    },
    "annotations": {
      "summary": "High rate of 503 errors from payment-service",
      "description": "Payment service returning 503 Service Unavailable - 45% error rate over last 5m"
    },
    "startsAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "endsAt": "'"$(date -u -v+1H +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'

echo "   ✅ Rate limit alert sent"
echo ""
sleep 2

# ─────────────────────────────────────────────────────────────────────────────
# Scenario 3: Database Connection Pool Exhaustion
# ─────────────────────────────────────────────────────────────────────────────
echo "📊 Scenario 3: Database Connection Pool Exhaustion"
echo "   Expected: Agent should detect connection pool issue and recommend RESTART"

curl -s -X POST "$ALERTMANAGER_URL" \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "DatabaseConnectionPoolExhausted",
      "severity": "critical",
      "service": "user-service",
      "instance": "user-service-2",
      "database": "postgres"
    },
    "annotations": {
      "summary": "Database connection pool exhausted",
      "description": "user-service cannot acquire database connections - pool size 100/100, wait queue: 45"
    },
    "startsAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "endsAt": "'"$(date -u -v+1H +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'

echo "   ✅ Connection pool alert sent"
echo ""
sleep 2

# ─────────────────────────────────────────────────────────────────────────────
# Scenario 4: Disk Space Critical
# ─────────────────────────────────────────────────────────────────────────────
echo "📊 Scenario 4: Disk Space Critical"
echo "   Expected: Agent should detect disk space issue"

curl -s -X POST "$ALERTMANAGER_URL" \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "DiskSpaceCritical",
      "severity": "critical",
      "service": "log-aggregator",
      "instance": "log-aggregator-1",
      "mountpoint": "/var/log"
    },
    "annotations": {
      "summary": "Disk space critical on log-aggregator",
      "description": "Disk usage on /var/log is at 96% - only 2GB remaining"
    },
    "startsAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "endsAt": "'"$(date -u -v+1H +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'

echo "   ✅ Disk space alert sent"
echo ""
sleep 2

# ─────────────────────────────────────────────────────────────────────────────
# Scenario 5: Goroutine Leak (for V2 testing)
# ─────────────────────────────────────────────────────────────────────────────
echo "📊 Scenario 5: Goroutine Leak (V2 Pattern)"
echo "   Expected: Agent should detect goroutine leak if V2 is enabled"

curl -s -X POST "$ALERTMANAGER_URL" \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {
      "alertname": "HighGoroutineCount",
      "severity": "warning",
      "service": "websocket-server",
      "instance": "websocket-server-3",
      "job": "websocket-server"
    },
    "annotations": {
      "summary": "Goroutine count continuously rising",
      "description": "Goroutine count has grown from 150 to 450 over the last 10 minutes"
    },
    "startsAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'",
    "endsAt": "'"$(date -u -v+1H +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'

echo "   ✅ Goroutine leak alert sent"
echo ""

echo ""
echo "✨ All incidents triggered!"
echo ""
echo "📍 Next steps:"
echo "   1. Check Alertmanager: http://localhost:9093"
echo "   2. Watch agent logs: docker compose -f infra/docker-compose.yml logs -f agent"
echo "   3. Query incidents: curl http://localhost:8082/incidents"
echo "   4. View in Grafana: http://localhost:3001"
echo ""
echo "⏱️  The agent polls every 10 seconds, so incidents should appear within 10-20s"
