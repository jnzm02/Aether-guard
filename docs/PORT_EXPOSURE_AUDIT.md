# Port Exposure Security Audit

**Date:** 2026-07-29
**Trigger:** BSI CERT-Bund notification - Redis exposed without authentication
**Status:** 🔴 CRITICAL - Multiple database ports publicly exposed

## Executive Summary

**CRITICAL VULNERABILITIES FOUND:**
- ✅ **Redis (6379)** - FIXED: Now requires auth + localhost-only in dev, no port in prod
- ✅ **Postgres (5432)** - FIXED: Now localhost-only in dev, no port in prod
- ⚠️ **Multiple services** - Unnecessarily exposed to the Internet

## Current Port Exposure (docker-compose.yml)

| Service | Port | Exposed | Should Be Public? | Risk Level | Status |
|---------|------|---------|-------------------|------------|--------|
| target-service | 8080 | 0.0.0.0:8080 | ✅ YES (demo service) | LOW | OK |
| prometheus | 9090 | 0.0.0.0:9090 | ⚠️ NO (internal metrics) | MEDIUM | REVIEW |
| alertmanager | 9093 | 0.0.0.0:9093 | ⚠️ NO (internal alerts) | MEDIUM | REVIEW |
| listener | 8081 | 0.0.0.0:8081 | ❌ NO (internal webhook) | MEDIUM | REVIEW |
| **redis** | **6379** | **127.0.0.1:6379** | **❌ NO** | **FIXED** | ✅ |
| **postgres** | **5432** | **127.0.0.1:5432** | **❌ NO** | **FIXED** | ✅ |
| agent | 8082 | 0.0.0.0:8082 | ⚠️ MAYBE (webhook receiver) | MEDIUM | REVIEW |
| event-tracker | 8083 | 0.0.0.0:8083 | ⚠️ MAYBE (tracker UI) | LOW | REVIEW |
| tempo | 3200,4317,4318 | 0.0.0.0 | ❌ NO (internal tracing) | MEDIUM | REVIEW |
| grafana | 3001 | 0.0.0.0:3001 | ✅ YES (dashboard UI) | LOW | OK |

## Production (docker-compose.prod.yml)

**Fixed Services:**
- ✅ Redis: `ports: []` (no external exposure)
- ✅ Postgres: `ports: []` (no external exposure)

**Still Exposed:**
All other services inherit port mappings from base docker-compose.yml unless explicitly overridden.

## Architecture Analysis

Based on code review:

### Internal-Only Services (Should NOT be public)

1. **Listener (8081)** ❌ Currently public
   - Receives webhooks FROM Alertmanager
   - Alertmanager is in same Docker network → uses `http://listener:8081/webhook`
   - **No need for public exposure**

2. **Alertmanager (9093)** ❌ Currently public
   - Internal alert routing
   - Only needs to send webhooks to Listener (internal)
   - **No need for public exposure** (unless you want UI access from outside)

3. **Tempo (3200, 4317, 4318)** ❌ Currently public
   - Distributed tracing backend
   - Only accessed by services via internal network
   - **No need for public exposure**

4. **Agent (8082)** ❌ Currently public
   - Receives webhooks from Listener (internal network)
   - Alternative: Direct webhooks from Alertmanager (still internal)
   - **No need for public exposure**

### Potentially Public Services (Review needed)

1. **Prometheus (9090)** ⚠️
   - **Current:** Public
   - **Recommendation:**
     - Internal only (accessed via Grafana or VPN)
     - OR add authentication if public access needed
     - OR bind to localhost for SSH tunnel access

2. **Grafana (3001)** ✅
   - **Current:** Public
   - **Status:** OK - Dashboard UI should be accessible
   - **Note:** Already has authentication enabled

3. **Target-Service (8080)** ✅
   - **Current:** Public
   - **Status:** OK - Demo service for testing

4. **Event-Tracker (8083)** ⚠️
   - **Current:** Public
   - **Recommendation:** Check if external access needed

## Recommended Production Configuration

### Option 1: Maximum Security (Recommended)

Only expose services that MUST be public:

```yaml
# docker-compose.prod.yml
services:
  # Public services
  grafana:
    ports:
      - "3001:3000"  # Dashboard UI

  target-service:
    ports:
      - "8080:8080"  # Demo service

  # ALL internal services: remove port mappings
  prometheus:
    ports: []

  alertmanager:
    ports: []

  listener:
    ports: []

  agent:
    ports: []

  redis:
    ports: []  # Already fixed

  postgres:
    ports: []  # Already fixed

  tempo:
    ports: []

  event-tracker:
    ports: []  # Or keep if external access needed
```

Access internal services via:
- SSH tunnel: `ssh -L 9090:localhost:9090 user@server`
- Bastion host
- VPN

### Option 2: Development-Friendly

Localhost binding for development access:

```yaml
# docker-compose.yml (dev)
services:
  prometheus:
    ports:
      - "127.0.0.1:9090:9090"

  alertmanager:
    ports:
      - "127.0.0.1:9093:9093"

  # Keep public: grafana, target-service
  # Remove entirely in prod: listener, agent, tempo, redis, postgres
```

## Kubernetes Configuration

Current K8s config has similar issues:

### Already Correct
- Redis: ClusterIP (internal only) ✅

### Needs Review
- Agent: LoadBalancer with comment "Exposed publicly for listener webhook"
  - **Issue:** If listener is in-cluster, no need for LoadBalancer
  - **Fix:** Change to ClusterIP

## Immediate Actions Required

### 1. Deploy Current Fixes (URGENT)

```bash
# These are already fixed in code, need deployment:
# - Redis authentication + localhost binding
# - Postgres localhost binding

ssh user@116.202.19.79
cd /path/to/aether-guard

# Generate password
export REDIS_PASSWORD=$(openssl rand -base64 32)
echo "REDIS_PASSWORD=$REDIS_PASSWORD" >> .env

# Pull fixes
git pull origin main

# Redeploy
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml down
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d
```

### 2. Verify No Public Database Access

```bash
# From external machine
nc -zv 116.202.19.79 6379  # Should timeout/refuse
nc -zv 116.202.19.79 5432  # Should timeout/refuse
```

### 3. Additional Hardening (After immediate fix)

```bash
# Remove unnecessary port mappings in prod
# Edit docker-compose.prod.yml to add:
# - prometheus: ports: []
# - alertmanager: ports: []
# - listener: ports: []
# - agent: ports: []
# - tempo: ports: []

# Redeploy
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d --force-recreate
```

### 4. Firewall Hardening

Even with fixed port mappings, add firewall rules:

```bash
# Allow only essential ports
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP (if needed)
sudo ufw allow 443/tcp   # HTTPS (if needed)
sudo ufw allow 3001/tcp  # Grafana (if public)
sudo ufw allow 8080/tcp  # Target service (if public)

# Explicitly deny database ports
sudo ufw deny 6379/tcp   # Redis
sudo ufw deny 5432/tcp   # Postgres
sudo ufw deny 27017/tcp  # MongoDB (if any)

sudo ufw enable
sudo ufw status
```

## Port Scan Results (TODO)

After deployment, run port scan to verify:

```bash
# From external machine
nmap -p 3001,6379,5432,8080,8081,8082,8083,9090,9093,3200,4317,4318 116.202.19.79

# Expected results:
# 3001 - OPEN (Grafana) ✅
# 8080 - OPEN (target-service) ✅
# 6379 - CLOSED/FILTERED (Redis) ✅
# 5432 - CLOSED/FILTERED (Postgres) ✅
# All others - CLOSED/FILTERED ✅
```

## Monitoring Recommendations

1. **Set up port monitoring alerts**
   - Alert if unexpected ports become open
   - Use services like Shodan, SecurityScorecard

2. **Regular security scans**
   - Weekly nmap scans
   - Subscribe to breach notification services (like BSI CERT)

3. **Access logging**
   - Enable connection logging for all databases
   - Monitor for failed auth attempts

## References

- [BSI CERT-Bund Report](https://reports.cert-bund.de/en/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
- [Redis Security](https://redis.io/docs/management/security/)
- [PostgreSQL Security](https://www.postgresql.org/docs/current/security.html)

## Change Log

| Date | Change | Status |
|------|--------|--------|
| 2026-07-29 | Redis auth + localhost binding | ✅ Fixed |
| 2026-07-29 | Postgres localhost binding | ✅ Fixed |
| 2026-07-29 | Port exposure audit | 📋 Documented |
| Pending | Remove unnecessary service exposures | ⏳ Recommended |
