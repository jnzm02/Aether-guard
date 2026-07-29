#!/usr/bin/env bash
#
# Redis & Postgres Breach Evidence Check
# Run this BEFORE deploying the security fixes to check for unauthorized access
#

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}   BREACH EVIDENCE CHECK - Redis & Postgres           ${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "This script checks for signs of unauthorized access BEFORE"
echo "deploying the security fix."
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Redis Breach Evidence
# ─────────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}1. REDIS BREACH EVIDENCE CHECK${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

echo -e "${BLUE}[1/5] Checking Redis connection statistics...${NC}"
docker exec redis redis-cli INFO stats | grep -E "total_connections_received|rejected_connections|expired_keys|evicted_keys" || echo "Failed to get stats"
echo ""

echo -e "${BLUE}[2/5] Checking all Redis keys (first 50)...${NC}"
echo "Expected keys: incident:report:* (from our agent)"
echo "Suspicious: Any keys you don't recognize (e.g., webshells, backdoors, crypto miners)"
echo ""
docker exec redis redis-cli --scan --count 50 || echo "Failed to scan keys"
echo ""
echo "Total keys in database:"
docker exec redis redis-cli DBSIZE || echo "Failed to get DB size"
echo ""

echo -e "${BLUE}[3/5] Checking Redis command statistics...${NC}"
echo "Suspicious: High counts of FLUSHDB, FLUSHALL, CONFIG, SCRIPT, EVAL"
echo ""
docker exec redis redis-cli INFO commandstats | grep -E "cmdstat_(flushdb|flushall|config|script|eval|auth)" || echo "No suspicious commands found"
echo ""

echo -e "${BLUE}[4/5] Checking Redis logs for authentication failures...${NC}"
echo "Suspicious: Multiple AUTH failures, CONFIG attempts from unknown IPs"
echo ""
docker logs redis --tail 200 2>&1 | grep -i -E "warning|error|denied|auth|config|accepted" || echo "No warnings/errors in logs"
echo ""

echo -e "${BLUE}[5/5] Checking Redis client connections...${NC}"
echo "Expected: Only connections from agent container (172.x.x.x)"
echo "Suspicious: External IP addresses"
echo ""
docker exec redis redis-cli CLIENT LIST || echo "Failed to get client list"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Postgres Breach Evidence
# ─────────────────────────────────────────────────────────────────────────────

echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}2. POSTGRES BREACH EVIDENCE CHECK${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

echo -e "${BLUE}[1/4] Checking active Postgres connections...${NC}"
echo "Expected: Only agent container connections"
echo "Suspicious: Connections from external IPs, unknown applications"
echo ""
docker exec postgres psql -U aether_guard -d aether_guard -c "
    SELECT
        client_addr,
        application_name,
        state,
        query_start,
        left(query, 80) as query
    FROM pg_stat_activity
    WHERE datname = 'aether_guard'
    ORDER BY query_start DESC;" || echo "Failed to check connections"
echo ""

echo -e "${BLUE}[2/4] Checking Postgres authentication logs...${NC}"
echo "Suspicious: Failed authentication, connections from unexpected IPs"
echo ""
docker logs postgres --tail 200 2>&1 | grep -i -E "fatal|error|authentication|connection.*from" || echo "No authentication errors"
echo ""

echo -e "${BLUE}[3/4] Checking for suspicious database activity...${NC}"
echo "Checking for unexpected tables, users, or databases"
echo ""
docker exec postgres psql -U aether_guard -d aether_guard -c "\dt" || echo "Failed to list tables"
echo ""
docker exec postgres psql -U aether_guard -d aether_guard -c "\du" || echo "Failed to list users"
echo ""

echo -e "${BLUE}[4/4] Checking incident_reports table integrity...${NC}"
echo "Checking row count and recent activity"
echo ""
docker exec postgres psql -U aether_guard -d aether_guard -c "
    SELECT
        COUNT(*) as total_incidents,
        MAX(detected_at) as latest_incident,
        COUNT(CASE WHEN detected_at > NOW() - INTERVAL '24 hours' THEN 1 END) as last_24h
    FROM incident_reports;" 2>/dev/null || echo "Table doesn't exist yet (OK if fresh install)"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}   BREACH CHECK COMPLETE                               ${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════${NC}"
echo ""
echo "Review the output above for:"
echo "  ${RED}✗${NC} Unfamiliar Redis keys (e.g., crypto miner configs)"
echo "  ${RED}✗${NC} External IP addresses in client/connection lists"
echo "  ${RED}✗${NC} High counts of dangerous commands (FLUSHDB, CONFIG, EVAL)"
echo "  ${RED}✗${NC} Authentication failures from unknown sources"
echo "  ${RED}✗${NC} Suspicious database tables or users you didn't create"
echo "  ${RED}✗${NC} Gaps in incident_reports (deleted data)"
echo ""
echo "If you see any suspicious activity, investigate before proceeding."
echo "Otherwise, proceed with security fix deployment."
echo ""
