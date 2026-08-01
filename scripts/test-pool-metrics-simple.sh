#!/bin/bash
# Verify DB pool metrics are collected and show correct max

set -e

echo "=== DB Pool Metrics Verification ==="
echo ""

# Start Postgres
docker rm -f test-pg-metrics 2>/dev/null || true
docker run --name test-pg-metrics -e POSTGRES_PASSWORD=test -p 5439:5432 -d postgres:15-alpine >/dev/null
sleep 2

# Start service with explicit pool config
cd /Users/nizamijussupov/Desktop/AI/Aether\ Guard/services/target-service
POSTGRES_URL="postgresql://postgres:test@localhost:5439/postgres?sslmode=disable" \
POSTGRES_POOL_MAX_CONNS=15 \
PORT=8894 \
go run ./cmd/server > /tmp/pool-metrics.log 2>&1 &
PID=$!
sleep 3

echo "Waiting for background collector to emit metrics (runs every 5s)..."
sleep 6

echo ""
echo "DB pool metrics:"
METRICS=$(curl -s http://localhost:8894/metrics | grep "^aether_guard_db_connections")
echo "$METRICS"
echo ""

MAX=$(echo "$METRICS" | grep "_max" | awk '{print $2}')
IN_USE=$(echo "$METRICS" | grep "_in_use" | awk '{print $2}')
IDLE=$(echo "$METRICS" | grep "_idle" | awk '{print $2}')

if [ -z "$MAX" ] || [ -z "$IN_USE" ] || [ -z "$IDLE" ]; then
  echo "❌ FAIL: Not all pool metrics present"
  echo "   max=$MAX in_use=$IN_USE idle=$IDLE"
  kill $PID
  docker rm -f test-pg-metrics >/dev/null
  exit 1
fi

if [ "$MAX" != "15" ]; then
  echo "❌ FAIL: max should be 15 (from POSTGRES_POOL_MAX_CONNS), got $MAX"
  kill $PID
  docker rm -f test-pg-metrics >/dev/null
  exit 1
fi

# Basic sanity: in_use + idle should not exceed max
TOTAL=$((IN_USE + IDLE))
if [ $TOTAL -gt $MAX ]; then
  echo "❌ FAIL: in_use($IN_USE) + idle($IDLE) = $TOTAL exceeds max($MAX)"
  kill $PID
  docker rm -f test-pg-metrics >/dev/null
  exit 1
fi

echo "✅ Pool metrics verified:"
echo "   - max: $MAX (correct, from env var)"
echo "   - in_use: $IN_USE"
echo "   - idle: $IDLE"
echo "   - total connections: $TOTAL (≤ max)"
echo ""
echo "   SDK integration working, metrics updated by background collector"

# Cleanup
kill $PID
docker stop test-pg-metrics >/dev/null
docker rm test-pg-metrics >/dev/null
