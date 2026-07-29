# URGENT: Security Fix Deployment Runbook

**Status:** 🔴 CRITICAL VULNERABILITY ACTIVE
**Affected:** Redis (6379), Postgres (5432) publicly exposed without proper security
**Server:** 116.202.19.79 (Hetzner)
**Reported By:** German Federal Office for Information Security (BSI)

---

## What's Fixed

✅ **Redis Security:**
- Authentication required (`--requirepass`)
- Localhost binding in dev (127.0.0.1:6379)
- No port mapping in production
- Client connections updated with password

✅ **Postgres Security:**
- Localhost binding in dev (127.0.0.1:5432)
- No port mapping in production

✅ **Documentation:**
- `/docs/REDIS_SECURITY.md` - Redis hardening guide
- `/docs/PORT_EXPOSURE_AUDIT.md` - Full port audit report

---

## STEP 1: Check for Breach Evidence (5 minutes)

**IMPORTANT:** Run this BEFORE deploying the fix to check if the server was compromised.

### SSH to Server

```bash
ssh root@116.202.19.79
# Or: ssh your-username@116.202.19.79
```

### Run Breach Check Script

```bash
cd /opt/aether-guard

# Copy the breach check script
cat > scripts/check-breach.sh << 'EOFSCRIPT'
#!/usr/bin/env bash
# Breach evidence check script (full content from check-breach.sh)
# ... (see scripts/check-breach.sh in the repo)
EOFSCRIPT

chmod +x scripts/check-breach.sh
./scripts/check-breach.sh | tee breach-check-$(date +%Y%m%d-%H%M%S).log
```

### OR Run Manual Checks

```bash
# Redis checks
docker exec redis redis-cli INFO stats | grep -E "total_connections|rejected"
docker exec redis redis-cli KEYS "*" | head -20
docker exec redis redis-cli CLIENT LIST
docker logs redis --tail 100 | grep -i -E "auth|error|warning"

# Postgres checks
docker exec postgres psql -U aether_guard -d aether_guard -c "SELECT client_addr, application_name, state FROM pg_stat_activity WHERE datname = 'aether_guard';"
docker logs postgres --tail 100 | grep -i -E "fatal|error|authentication"
```

### What to Look For

🔴 **SUSPICIOUS (investigate before proceeding):**
- Unfamiliar Redis keys (e.g., crypto miner configs, webshells)
- External IP addresses in CLIENT LIST or pg_stat_activity
- High counts of FLUSHDB, CONFIG, EVAL commands
- Authentication failures from unknown IPs
- Unexpected Postgres tables/users
- Gaps in incident_reports data

✅ **EXPECTED (OK to proceed):**
- Redis keys: `incident:report:*`
- Client IPs: 172.x.x.x (Docker network)
- Low connection counts from agent container only

---

## STEP 2: Deploy Security Fix (10 minutes)

### 2.1 Generate Secure Redis Password

```bash
cd /opt/aether-guard

# Generate strong password
export REDIS_PASSWORD=$(openssl rand -base64 32)
echo "Generated password: $REDIS_PASSWORD"

# Add to .env file
echo "" >> .env
echo "# Security: Redis authentication (added $(date +%Y-%m-%d))" >> .env
echo "REDIS_PASSWORD=$REDIS_PASSWORD" >> .env

# Verify it was added
tail -3 .env
```

### 2.2 Pull Latest Security Fixes

```bash
cd /opt/aether-guard

# Backup current state
./scripts/deploy.sh backup || {
    mkdir -p backups
    cp .env backups/.env.$(date +%Y%m%d-%H%M%S)
    cp infra/docker-compose.yml backups/
    cp infra/docker-compose.prod.yml backups/
}

# Pull security fixes from main branch
git fetch origin main
git pull origin main

# Verify fixes are present
echo "Checking Redis security config..."
grep -A 5 "requirepass" infra/docker-compose.yml
grep "ports: \[\]" infra/docker-compose.prod.yml | grep -E "redis|postgres"
```

### 2.3 Redeploy with Security Fixes

```bash
cd /opt/aether-guard/infra

# Stop all services
docker compose -f docker-compose.yml -f docker-compose.prod.yml down

# Start with new security configuration
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Wait for services to stabilize
sleep 30
```

### 2.4 Check Service Status

```bash
cd /opt/aether-guard/infra

# Check all containers are running
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# Check logs for errors
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=50 redis
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=50 postgres
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail=50 agent
```

Expected output:
- Redis: "Ready to accept connections", "Server initialized"
- Postgres: "database system is ready to accept connections"
- Agent: "Redis connected: redis://:***@redis:6379/0"

---

## STEP 3: VERIFICATION (CRITICAL - Must Pass All Tests)

### Test 1: Redis Authentication Required (Should FAIL)

```bash
docker exec redis redis-cli ping
```

**Expected output:**
```
(error) NOAUTH Authentication required.
```

**If you see `PONG`:** ❌ FAIL - Redis auth not working, rollback immediately!

### Test 2: Redis Authentication Works (Should SUCCEED)

```bash
docker exec redis redis-cli -a "$REDIS_PASSWORD" ping
```

**Expected output:**
```
Warning: Using a password with '-a' or '-u' option on the command line interface may not be safe.
PONG
```

**If you see error:** ❌ FAIL - Password mismatch, check .env and redeploy

### Test 3: External Access Blocked (Should TIMEOUT/REFUSE)

**Run this from your LOCAL machine (NOT the server):**

```bash
nc -zv 116.202.19.79 6379
nc -zv 116.202.19.79 5432
```

**Expected output:**
```
nc: connect to 116.202.19.79 port 6379 (tcp) failed: Connection refused
# OR
nc: connect to 116.202.19.79 port 6379 (tcp) failed: Connection timed out

# Same for port 5432
```

**If you see "succeeded":** ❌ FAIL - Ports still exposed, check docker-compose.prod.yml

### Test 4: Application Still Works

```bash
# Check agent can connect to Redis
docker logs agent --tail 20 | grep -i redis

# Trigger a test alert to verify end-to-end
curl -X POST http://localhost:8081/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [{
      "status": "firing",
      "labels": {"alertname": "security-test", "severity": "warning"},
      "annotations": {"summary": "Security fix verification test"}
    }]
  }'

# Check incident was stored
sleep 10
docker exec redis redis-cli -a "$REDIS_PASSWORD" KEYS "incident:*"
```

Expected: Should see incident keys created

---

## STEP 4: Post-Deployment Verification

### 4.1 Run Full Port Scan (from your local machine)

```bash
nmap -p 3001,6379,5432,8080,8081,8082,8083,9090,9093,3200,4317,4318 116.202.19.79
```

**Expected:**
- Port 3001: OPEN (Grafana - OK)
- Port 8080: OPEN (target-service - OK)
- Port 6379: CLOSED/FILTERED (Redis - GOOD)
- Port 5432: CLOSED/FILTERED (Postgres - GOOD)

### 4.2 Enable Firewall (Optional but Recommended)

```bash
# Back on server
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw allow 3001/tcp  # Grafana
sudo ufw allow 8080/tcp  # Target service

# Explicitly deny databases
sudo ufw deny 6379/tcp
sudo ufw deny 5432/tcp

sudo ufw enable
sudo ufw status numbered
```

---

## STEP 5: Report Results

### Copy and paste output of these commands:

```bash
echo "=== TEST 1: Redis NOAUTH Check ==="
docker exec redis redis-cli ping

echo ""
echo "=== TEST 2: Redis Auth Success ==="
docker exec redis redis-cli -a "$REDIS_PASSWORD" ping

echo ""
echo "=== TEST 3: External Port Check (run from local machine) ==="
# Run: nc -zv 116.202.19.79 6379
# Run: nc -zv 116.202.19.79 5432

echo ""
echo "=== Container Status ==="
docker compose -f /opt/aether-guard/infra/docker-compose.yml \
               -f /opt/aether-guard/infra/docker-compose.prod.yml ps

echo ""
echo "=== Agent Redis Connection ==="
docker logs agent --tail 20 | grep -i redis
```

---

## Rollback Procedure (If Tests Fail)

```bash
cd /opt/aether-guard

# Stop current deployment
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml down

# Restore backup .env
cp backups/.env.$(ls -t backups/ | grep .env | head -1) .env

# Restart with old config
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d

# Check status
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml ps
```

---

## Expected Timeline

| Step | Duration | Status |
|------|----------|--------|
| 1. Breach check | 5 min | ⏳ Pending |
| 2. Deploy fix | 10 min | ⏳ Pending |
| 3. Verification | 5 min | ⏳ Pending |
| 4. Port scan | 2 min | ⏳ Pending |
| **Total** | **~22 min** | |

---

## Success Criteria

All must pass:
- ✅ Test 1: Redis rejects unauthenticated access (NOAUTH error)
- ✅ Test 2: Redis accepts authenticated access (PONG)
- ✅ Test 3: External port scan shows 6379 & 5432 closed
- ✅ Agent logs show "Redis connected"
- ✅ No errors in container logs

---

## Support

If any test fails:
1. **DO NOT** proceed to next step
2. Capture full error output
3. Check logs: `docker compose logs redis postgres agent`
4. Contact support with logs

**This is a critical security fix. Do not skip verification steps.**
