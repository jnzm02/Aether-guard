#!/bin/bash
# Test circuit breaker opens at EXACTLY the 5th failure (threshold=5 from breaker.go:38)

set -e

echo "=== Circuit Breaker Threshold Test ==="
echo "Expected: state=0 for failures 1-4, state=2 at failure 5"
echo ""

# Start Postgres
docker rm -f test-pg-threshold 2>/dev/null || true
docker run --name test-pg-threshold -e POSTGRES_PASSWORD=test -p 5436:5432 -d postgres:15-alpine >/dev/null
sleep 2

# Start service
cd /Users/nizamijussupov/Desktop/AI/Aether\ Guard/services/target-service
POSTGRES_URL="postgresql://postgres:test@localhost:5436/postgres?sslmode=disable" PORT=8891 go run ./cmd/server > /tmp/threshold-test.log 2>&1 &
PID=$!
sleep 3

# Verify service healthy
echo "Initial health check (Postgres UP):"
curl -s http://localhost:8891/health | jq -c '{postgres: .dependencies.postgres}'
curl -s http://localhost:8891/metrics | grep "^aether_guard_circuit_breaker_state"
echo ""

# Stop Postgres to trigger failures
docker stop test-pg-threshold >/dev/null
echo "Postgres stopped. Triggering failures..."
echo ""

# Trigger 5 health checks one by one, checking state after each
for i in {1..5}; do
  echo "--- Failure $i ---"
  curl -s http://localhost:8891/health >/dev/null
  STATE=$(curl -s http://localhost:8891/metrics | grep "^aether_guard_circuit_breaker_state" | awk '{print $2}')
  echo "Circuit breaker state: $STATE"

  if [ "$i" -lt 5 ] && [ "$STATE" != "0" ]; then
    echo "❌ FAIL: Breaker should be closed (0) at failure $i, got $STATE"
    kill $PID
    docker stop test-pg-threshold >/dev/null
    docker rm test-pg-threshold >/dev/null
    exit 1
  fi

  if [ "$i" -eq 5 ] && [ "$STATE" != "2" ]; then
    echo "❌ FAIL: Breaker should be open (2) at failure 5, got $STATE"
    kill $PID
    docker stop test-pg-threshold >/dev/null
    docker rm test-pg-threshold >/dev/null
    exit 1
  fi

  sleep 0.5
done

echo ""
echo "✅ Breaker trips at EXACTLY failure 5 (threshold=5)"

# Cleanup
kill $PID
docker stop test-pg-threshold >/dev/null
docker rm test-pg-threshold >/dev/null
