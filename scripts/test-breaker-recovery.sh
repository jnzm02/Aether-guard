#!/bin/bash
# Test circuit breaker recovery: open → half_open → closed
# resetTimeout=30s (breaker.go:38), requires 2 successes in half_open (breaker.go:106)

set -e

echo "=== Circuit Breaker Recovery Test ==="
echo "Expected: open(2) → [wait 30s] → half_open(1) → [2 successes] → closed(0)"
echo ""

# Start Postgres
docker rm -f test-pg-recovery 2>/dev/null || true
docker run --name test-pg-recovery -e POSTGRES_PASSWORD=test -p 5437:5432 -d postgres:15-alpine >/dev/null
sleep 2

# Start service
cd /Users/nizamijussupov/Desktop/AI/Aether\ Guard/services/target-service
POSTGRES_URL="postgresql://postgres:test@localhost:5437/postgres?sslmode=disable" PORT=8892 go run ./cmd/server > /tmp/recovery-test.log 2>&1 &
PID=$!
sleep 3

echo "1. Trigger breaker to open..."
docker stop test-pg-recovery >/dev/null
for i in {1..5}; do
  curl -s http://localhost:8892/health >/dev/null
  sleep 0.2
done
STATE=$(curl -s http://localhost:8892/metrics | grep "^aether_guard_circuit_breaker_state" | awk '{print $2}')
echo "   Breaker state: $STATE (should be 2=open)"
if [ "$STATE" != "2" ]; then
  echo "❌ FAIL: Breaker should be open (2), got $STATE"
  kill $PID
  docker rm -f test-pg-recovery >/dev/null
  exit 1
fi
echo ""

echo "2. Restart Postgres..."
docker start test-pg-recovery >/dev/null
sleep 2
echo "   Postgres is back up"
echo ""

echo "3. Attempt health check while breaker still open (before 30s timeout)..."
curl -s http://localhost:8892/health >/dev/null
STATE=$(curl -s http://localhost:8892/metrics | grep "^aether_guard_circuit_breaker_state" | awk '{print $2}')
echo "   Breaker state: $STATE (should still be 2=open, request failed fast)"
if [ "$STATE" != "2" ]; then
  echo "❌ FAIL: Breaker should still be open (2), got $STATE"
  kill $PID
  docker rm -f test-pg-recovery >/dev/null
  exit 1
fi
echo ""

echo "4. Wait for reset timeout (30 seconds)..."
echo "   (This proves the timeout is enforced, not just immediate recovery)"
sleep 31
echo "   30 seconds elapsed"
echo ""

echo "5. First health check after timeout (should transition to half_open=1)..."
curl -s http://localhost:8892/health >/dev/null
sleep 0.5
STATE=$(curl -s http://localhost:8892/metrics | grep "^aether_guard_circuit_breaker_state" | awk '{print $2}')
echo "   Breaker state: $STATE (should be 1=half_open)"
if [ "$STATE" != "1" ]; then
  echo "❌ FAIL: Breaker should be half_open (1), got $STATE"
  kill $PID
  docker rm -f test-pg-recovery >/dev/null
  exit 1
fi
echo ""

echo "6. Second successful health check (should close breaker)..."
curl -s http://localhost:8892/health >/dev/null
sleep 0.5
STATE=$(curl -s http://localhost:8892/metrics | grep "^aether_guard_circuit_breaker_state" | awk '{print $2}')
echo "   Breaker state: $STATE (should be 0=closed, recovered!)"
if [ "$STATE" != "0" ]; then
  echo "❌ FAIL: Breaker should be closed (0), got $STATE"
  kill $PID
  docker rm -f test-pg-recovery >/dev/null
  exit 1
fi
echo ""

echo "✅ Full recovery cycle verified: open(2) → half_open(1) → closed(0)"

# Cleanup
kill $PID
docker stop test-pg-recovery >/dev/null
docker rm test-pg-recovery >/dev/null
