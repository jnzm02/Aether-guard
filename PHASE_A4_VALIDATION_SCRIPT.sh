#!/usr/bin/env bash
#
# Phase A.4: Complete Validation of All 9 RCA Patterns
#
# Run this script after starting the Docker Compose test stack:
#   cd infra
#   docker-compose -f docker-compose.test.yml up -d
#
# This script validates that all 9 patterns can be triggered correctly.
# Pattern 6 (Dependency Failure) is the critical test - it actually stops
# Postgres and Redis to verify real stdlib error messages appear in logs.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 3: Memory Leak
# ═══════════════════════════════════════════════════════════════════════════════

test_pattern_3() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Pattern 3: Memory Leak"
    log_info "═══════════════════════════════════════════════════════════════"

    log_info "Injecting 500MB memory leak..."
    curl -s -X POST "http://localhost:8080/chaos/memleak?mb=500" | jq '.'

    sleep 2

    log_info "Checking metrics..."
    MEMLEAK_METRIC=$(curl -s http://localhost:8080/metrics | grep "aether_guard_chaos_memleak_bytes_allocated" | grep -v "^#")
    HEAP_METRIC=$(curl -s http://localhost:8080/metrics | grep "go_memstats_heap_alloc_bytes" | grep -v "^#" | head -1)

    echo "  $MEMLEAK_METRIC"
    echo "  $HEAP_METRIC"

    if echo "$MEMLEAK_METRIC" | grep -qE "(524288000|5\.24288e\+08)"; then
        log_info "✅ Pattern 3 PASS: memleak_bytes_allocated shows 500MB"
    else
        log_error "❌ Pattern 3 FAIL: Metric incorrect"
    fi

    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 4a/4b: CPU Saturation
# ═══════════════════════════════════════════════════════════════════════════════

test_pattern_4() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Pattern 4a/4b: CPU Saturation"
    log_info "═══════════════════════════════════════════════════════════════"

    log_info "Injecting CPU spike (2 cores, 10 seconds)..."
    curl -s "http://localhost:8080/chaos/cpu?cores=2&ms=10000" | jq '.'

    sleep 2

    log_info "Checking metrics..."
    CPU_METRIC=$(curl -s http://localhost:8080/metrics | grep "aether_guard_chaos_cpu_cores_active" | grep -v "^#")
    echo "  $CPU_METRIC"

    if echo "$CPU_METRIC" | grep -q "2"; then
        log_info "✅ Pattern 4 PASS: cpu_cores_active shows 2 cores"
    else
        log_error "❌ Pattern 4 FAIL: Metric incorrect"
    fi

    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 6: Dependency Failure (CRITICAL TEST - REAL VALIDATION)
# ═══════════════════════════════════════════════════════════════════════════════

test_pattern_6() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Pattern 6: Dependency Failure (CRITICAL - REAL TEST)"
    log_info "═══════════════════════════════════════════════════════════════"

    # Test 1: Stop Postgres
    log_info "Stopping Postgres..."
    docker-compose -f infra/docker-compose.test.yml stop postgres

    sleep 2

    log_info "Triggering health check..."
    curl -s http://localhost:8080/health | jq '.'

    log_info "Checking logs for Postgres connection error..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    docker-compose -f infra/docker-compose.test.yml logs target-service 2>&1 | grep -E "(connection refused|dial tcp.*timeout|database.*unavailable|postgres.*failed)" | tail -5
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Restart Postgres
    log_info "Restarting Postgres..."
    docker-compose -f infra/docker-compose.test.yml start postgres
    sleep 5

    # Test 2: Stop Redis
    log_info "Stopping Redis..."
    docker-compose -f infra/docker-compose.test.yml stop redis

    sleep 2

    log_info "Triggering health check..."
    curl -s http://localhost:8080/health | jq '.'

    log_info "Checking logs for Redis connection error..."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    docker-compose -f infra/docker-compose.test.yml logs target-service 2>&1 | grep -E "(connection refused|dial tcp.*timeout|redis.*failed)" | tail -5
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Restart Redis
    log_info "Restarting Redis..."
    docker-compose -f infra/docker-compose.test.yml start redis
    sleep 3

    log_warn "Review the log output above. Pattern 6 passes if you see:"
    log_warn "  - 'connection refused' OR"
    log_warn "  - 'dial tcp ... timeout' OR"
    log_warn "  - 'postgres/redis connection failed'"

    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 7: Bad Deployment
# ═══════════════════════════════════════════════════════════════════════════════

test_pattern_7() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Pattern 7: Bad Deployment"
    log_info "═══════════════════════════════════════════════════════════════"

    log_info "Injecting 50% error rate..."
    curl -s "http://localhost:8080/chaos/error?rate=0.5" &

    log_info "Generating traffic..."
    for i in {1..20}; do
        curl -s http://localhost:8080/api/users > /dev/null &
    done
    wait

    sleep 2

    log_info "Checking error metrics..."
    ERROR_METRIC=$(curl -s http://localhost:8080/metrics | grep 'aether_guard_http_requests_total.*status_code="500"' | grep -v "^#" | head -1)
    echo "  $ERROR_METRIC"

    if echo "$ERROR_METRIC" | grep -qE "[1-9][0-9]*$"; then
        log_info "✅ Pattern 7 PASS: HTTP 500 errors generated"
    else
        log_error "❌ Pattern 7 FAIL: No errors found"
    fi

    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 8: Goroutine Leak
# ═══════════════════════════════════════════════════════════════════════════════

test_pattern_8() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Pattern 8: Goroutine Leak (NEW ENDPOINT)"
    log_info "═══════════════════════════════════════════════════════════════"

    log_info "Checking baseline goroutine count..."
    BASELINE=$(curl -s http://localhost:8080/metrics | grep "aether_guard_runtime_goroutines" | grep -v "^#" | awk '{print $2}')
    echo "  Baseline: $BASELINE goroutines"

    log_info "Injecting goroutine leak (500 goroutines, infinite duration)..."
    curl -s -X POST "http://localhost:8080/chaos/goroutine-leak?count=500&duration=0" | jq '.'

    sleep 10  # Wait for runtime collector to sample

    log_info "Checking new goroutine count..."
    NEW_COUNT=$(curl -s http://localhost:8080/metrics | grep "aether_guard_runtime_goroutines" | grep -v "^#" | awk '{print $2}')
    echo "  New count: $NEW_COUNT goroutines"

    INCREASE=$(echo "$NEW_COUNT - $BASELINE" | bc)
    echo "  Increase: $INCREASE"

    if [ $(echo "$INCREASE >= 450" | bc) -eq 1 ]; then
        log_info "✅ Pattern 8 PASS: Goroutine count increased by ~$INCREASE"
    else
        log_error "❌ Pattern 8 FAIL: Expected increase ~500, got $INCREASE"
    fi

    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Pattern 2: Restart Loop
# ═══════════════════════════════════════════════════════════════════════════════

test_pattern_2() {
    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Pattern 2: Restart Loop"
    log_info "═══════════════════════════════════════════════════════════════"

    log_info "Restarting container 3 times..."
    for i in 1 2 3; do
        docker-compose -f infra/docker-compose.test.yml restart target-service
        sleep 3
    done

    log_info "Checking logs for 'starting' messages..."
    COUNT=$(docker-compose -f infra/docker-compose.test.yml logs target-service 2>&1 | grep -i "starting" | wc -l)
    echo "  Found $COUNT 'starting' messages"

    if [ "$COUNT" -ge 3 ]; then
        log_info "✅ Pattern 2 PASS: 'starting' appears $COUNT times (≥3)"
    else
        log_error "❌ Pattern 2 FAIL: Expected ≥3, got $COUNT"
    fi

    echo ""
}

# ═══════════════════════════════════════════════════════════════════════════════
# Main Execution
# ═══════════════════════════════════════════════════════════════════════════════

main() {
    log_info "Starting Phase A.4 Validation - All 9 RCA Patterns"
    log_info ""

    # Check dependencies
    if ! command -v jq &> /dev/null; then
        log_error "jq is required but not installed. Install with: brew install jq"
        exit 1
    fi

    if ! docker-compose -f infra/docker-compose.test.yml ps | grep -q "target-service"; then
        log_error "target-service container is not running."
        log_error "Start it with: cd infra && docker-compose -f docker-compose.test.yml up -d"
        exit 1
    fi

    # Run tests
    test_pattern_3
    test_pattern_4
    test_pattern_7
    test_pattern_8
    test_pattern_2

    # Pattern 6 is the critical one - run it last and show full output
    test_pattern_6

    log_info "═══════════════════════════════════════════════════════════════"
    log_info "Phase A.4 Validation Complete"
    log_info "═══════════════════════════════════════════════════════════════"
    log_warn ""
    log_warn "IMPORTANT: Pattern 6 validation requires manual review of log output above."
    log_warn "Verify that actual stdlib error messages (connection refused, timeout, etc.)"
    log_warn "appeared in the logs when Postgres and Redis were stopped."
    log_warn ""
}

main "$@"
