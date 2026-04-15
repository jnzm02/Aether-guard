# CI/CD Architecture

Complete overview of the Continuous Integration and Continuous Deployment pipeline for Aether-Guard.

---

## 📊 Full CI/CD Pipeline

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DEVELOPMENT WORKFLOW                               │
└─────────────────────────────────────────────────────────────────────────────┘

    Developer                GitHub                    Actions Runner
        │                      │                            │
        │  1. git push         │                            │
        ├──────────────────────>│                            │
        │                      │                            │
        │                      │  2. Trigger CI             │
        │                      ├────────────────────────────>│
        │                      │                            │
        │                      │                            │  ┌──────────────┐
        │                      │                            │──┤ go-build     │
        │                      │                            │  │ python-lint  │
        │                      │                            │  │ python-test  │
        │                      │                            │  │ validate-cfg │
        │                      │                            │  └──────┬───────┘
        │                      │                            │         │
        │                      │                            │  ┌──────▼───────┐
        │                      │                            │──┤ docker-build │
        │                      │                            │  └──────┬───────┘
        │                      │                            │         │
        │                      │                            │  ┌──────▼───────┐
        │                      │                            │──┤ integration  │
        │                      │                            │  │ smoke test   │
        │                      │                            │  └──────┬───────┘
        │                      │                            │         │
        │                      │  3. CI Results (✓ or ✗)    │         │
        │                      │<────────────────────────────┼─────────┘
        │                      │                            │
        │  4. Manual: Run CD   │                            │
        ├──────────────────────>│                            │
        │  (Actions UI)        │                            │
        │                      │  5. Trigger CD             │
        │                      ├────────────────────────────>│
        │                      │                            │
        │                      │                            │  ┌──────────────┐
        │                      │                            │──┤ Build Images │
        │                      │                            │  │ • go build   │
        │                      │                            │  │ • docker     │
        │                      │                            │  └──────┬───────┘
        │                      │                            │         │
        │                      │                            │  ┌──────▼───────┐
        │                      │                            │──┤ Push Registry│
        │                      │                            │  │ :sha + :latest│
        │                      │                            │  └──────┬───────┘
        │                      │                            │         │
        │                      │                            │  ┌──────▼───────┐
        │                      │                            │──┤ SSH to Server│──┐
        │                      │                            │  └──────────────┘  │
        │                      │                            │                    │
        │                      │                            │                    │
┌───────┴──────────────────────┴────────────────────────────┴────────────────────┼───┐
│                       DIGITALOCEAN DROPLET                                     │   │
│                                                                                │   │
│   ┌───────────────────────────────────────────────────────────────────────┐   │   │
│   │ deploy.sh Script                                                      │   │   │
│   │                                                                       │   │   │
│   │  1. Backup current state                                             │   │   │
│   │     ├─ .env → backups/.env.TIMESTAMP                                 │<──┼───┘
│   │     └─ docker-compose.yml → backups/docker-compose.TIMESTAMP.yml     │   │
│   │                                                                       │   │
│   │  2. Pull new images from registry                                    │   │
│   │     ├─ target-service:SHA                                            │   │
│   │     ├─ listener:SHA                                                  │   │
│   │     └─ agent:SHA                                                     │   │
│   │                                                                       │   │
│   │  3. Rolling update (zero-downtime)                                   │   │
│   │     ├─ Update target-service → Health check ✓                        │   │
│   │     ├─ Update listener → Health check ✓                              │   │
│   │     ├─ Update agent → Health check ✓                                 │   │
│   │     └─ Update prometheus/alertmanager/grafana                        │   │
│   │                                                                       │   │
│   │  4. Verify deployment                                                │   │
│   │     ├─ curl http://localhost:8080/health                             │   │
│   │     ├─ curl http://localhost:8081/health                             │   │
│   │     ├─ curl http://localhost:8082/health                             │   │
│   │     └─ Check Prometheus targets                                      │   │
│   │                                                                       │   │
│   │  5. Cleanup old images                                               │   │
│   │     └─ docker system prune                                           │   │
│   │                                                                       │   │
│   └───────────────────────────────────────────────────────────────────────┘   │
│                                                                                │
│   ┌────────────────────────────────────────────────────────────────────┐      │
│   │ Running Services                                                   │      │
│   │                                                                    │      │
│   │  ┌─────────────────┐  ┌─────────────┐  ┌────────────────────┐    │      │
│   │  │ target-service  │  │ Prometheus  │  │ Alertmanager       │    │      │
│   │  │ :8080           │  │ :9090       │  │ :9093              │    │      │
│   │  └─────────────────┘  └─────────────┘  └────────────────────┘    │      │
│   │                                                                    │      │
│   │  ┌─────────────────┐  ┌─────────────┐  ┌────────────────────┐    │      │
│   │  │ listener        │  │ agent       │  │ Grafana            │    │      │
│   │  │ :8081           │  │ :8082       │  │ :3001              │    │      │
│   │  └─────────────────┘  └─────────────┘  └────────────────────┘    │      │
│   └────────────────────────────────────────────────────────────────────┘      │
│                                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🔄 CI Pipeline (.github/workflows/ci.yml)

Automatically runs on: `push` to `main` or `dev`, and all `pull_request` to `main`

```
┌─────────────────────────────────────────────────────────────────┐
│                         CI PIPELINE                             │
└─────────────────────────────────────────────────────────────────┘

Job 1: go-build (services/target-service)
├─ Setup Go 1.21
├─ Download modules
├─ go build ./...
├─ go vet ./...
└─ go test -race -count=1 ./...  (23 tests)

Job 2: python-lint
├─ Setup Python 3.11
├─ Install ruff
├─ ruff check services/agent/
├─ ruff check services/listener/
└─ ruff check scripts/

Job 3: python-test
├─ Setup Python 3.11
├─ Install dependencies
├─ pytest services/agent/tests/     (44 tests)
├─ pytest services/listener/tests/  (14 tests)
└─ Upload test reports (JUnit XML)

Job 4: validate-infra-config
├─ Install promtool + amtool
├─ promtool check config prometheus.yml
├─ promtool check rules slo_alerts.yml
└─ amtool check-config alertmanager.yml

Job 5: docker-build (requires jobs 1-4)
├─ Setup Docker Buildx
├─ docker build target-service:ci
├─ docker build listener:ci
└─ docker build agent:ci

Job 6: integration-smoke (requires job 5)
├─ Create .env with test values
├─ docker compose up -d
├─ Wait for services (health checks)
├─ curl /health endpoints
├─ curl Prometheus /api/v1/query
├─ curl Alertmanager /-/healthy
└─ docker compose down

Total: 81 tests (23 Go + 58 Python)
```

---

## 🚀 CD Pipeline (.github/workflows/cd.yml)

Trigger: **Manual only** (workflow_dispatch) with environment selection

```
┌─────────────────────────────────────────────────────────────────┐
│                         CD PIPELINE                             │
└─────────────────────────────────────────────────────────────────┘

Input: environment (production / staging)

Job 1: build-and-push
├─ Checkout code
├─ Setup Docker Buildx
├─ Login to private registry
├─ Build & Push target-service:$GITHUB_SHA
│  ├─ Tag: registry/aether-guard/target-service:$SHA
│  └─ Tag: registry/aether-guard/target-service:latest
├─ Build & Push listener:$GITHUB_SHA
│  ├─ Tag: registry/aether-guard/listener:$SHA
│  └─ Tag: registry/aether-guard/listener:latest
├─ Build & Push agent:$GITHUB_SHA
│  ├─ Tag: registry/aether-guard/agent:$SHA
│  └─ Tag: registry/aether-guard/agent:latest
└─ Output: image_tag = $GITHUB_SHA

Job 2: deploy (requires job 1, uses environment secrets)
├─ Checkout code
├─ Setup SSH key from secret
├─ scp deployment files to server
│  ├─ docker-compose.yml
│  ├─ prometheus/ alertmanager/ grafana/ configs
│  └─ scripts/deploy.sh
├─ scp .env.production (from GitHub secrets)
├─ ssh to server:
│  ├─ Login to registry
│  └─ Execute deploy.sh $GITHUB_SHA
│      ├─ Backup current state
│      ├─ Pull new images
│      ├─ Rolling update (one service at a time)
│      └─ Health checks
└─ Health check from GitHub Actions
   ├─ Wait 30s
   └─ curl all /health endpoints

Job 3: verify (requires job 2)
├─ Setup SSH key
├─ ssh to server:
│  ├─ docker ps (show running containers)
│  ├─ df -h (disk usage)
│  ├─ docker compose logs --tail=20
│  └─ curl Prometheus targets API
└─ Cleanup SSH key

Job 4: rollback (runs if deploy/verify fails)
├─ Setup SSH key
├─ ssh to server:
│  ├─ Restore .env.backup
│  ├─ docker compose down
│  └─ docker compose up -d
└─ Cleanup SSH key
```

---

## 🏗️ Infrastructure Components

### GitHub Actions Secrets

```
SSH_PRIVATE_KEY          → SSH authentication
SERVER_HOST              → Droplet IP address
SERVER_USER              → SSH username
ANTHROPIC_API_KEY        → Claude AI API key
DOCKER_REGISTRY          → Private registry URL
DOCKER_REGISTRY_USERNAME → Registry auth
DOCKER_REGISTRY_PASSWORD → Registry auth
```

### DigitalOcean Droplet Structure

```
/opt/aether-guard/
├── .env                     # Production environment config
├── .env.backup              # Previous .env (for rollback)
├── infra/
│   ├── docker-compose.yml   # Base configuration
│   ├── docker-compose.prod.yml  # Production overrides
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── rules/slo_alerts.yml
│   ├── alertmanager/
│   │   └── alertmanager.yml
│   └── grafana/
│       ├── provisioning/
│       └── dashboards/
├── scripts/
│   ├── deploy.sh            # Deployment script
│   └── setup-server.sh      # Initial setup
├── backups/
│   ├── .env.20260415-120000
│   ├── .env.20260415-130000
│   └── docker-compose.20260415-120000.yml
└── data/
    ├── prometheus/          # Docker volume data
    ├── agent/               # Analyses + post-mortems
    └── grafana/             # Grafana data
```

---

## 🔐 Security Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    SECURITY ARCHITECTURE                        │
└─────────────────────────────────────────────────────────────────┘

GitHub Actions Runner
├─ SSH Private Key (GitHub Secret)
│  └─ Used only for deployment, never committed
├─ Registry Credentials (GitHub Secret)
│  └─ Used to push/pull Docker images
└─ Anthropic API Key (GitHub Secret)
   └─ Injected into .env on server

Server Security
├─ SSH Key Authentication (no passwords)
├─ UFW Firewall
│  ├─ Allow: 22 (SSH), 80/443 (HTTP/HTTPS)
│  └─ Optional: 8080-8082, 9090-9093 (for debugging)
├─ Docker Socket Permissions
│  └─ Only deploy user in docker group
└─ .env File Permissions
   └─ chmod 600 (readable only by owner)

Network Security
├─ Private Docker Registry (authenticated)
├─ HTTPS for external endpoints (via reverse proxy)
└─ Internal docker network (aether-net)
```

---

## 📈 Deployment States

```
┌─────────────────────────────────────────────────────────────────┐
│                     DEPLOYMENT STATES                           │
└─────────────────────────────────────────────────────────────────┘

State 1: Pre-Deployment
├─ CI passes on main branch
├─ Developer reviews changes
└─ Ready for manual trigger

State 2: Building
├─ Docker images building
├─ Tests running
└─ Images pushing to registry

State 3: Deploying
├─ SSH connection established
├─ Files copied to server
├─ Backup created
└─ Services updating (one by one)

State 4: Verifying
├─ Health checks running
├─ Prometheus targets checked
└─ Logs inspected

State 5a: Success ✓
├─ All services healthy
├─ Old images cleaned up
└─ Deployment marked complete

State 5b: Failed → Rollback ✗
├─ Health checks failed
├─ .env.backup restored
├─ Previous containers restarted
└─ Alert developer
```

---

## 🔄 Rollback Scenarios

### Automatic Rollback (Built-in)

```
Trigger: Health check fails during deployment

1. Deploy.sh detects failure
   └─ Exit code != 0

2. Trap ERR handler executes
   └─ rollback() function called

3. Restore previous state
   ├─ cp .env.backup → .env
   └─ docker compose down && up

4. Verify rollback
   └─ Health checks pass ✓
```

### Manual Rollback (Emergency)

```
Trigger: Post-deployment issues discovered

1. SSH into server
   └─ ssh root@SERVER_IP

2. List available backups
   └─ ls -lh /opt/aether-guard/backups/

3. Choose backup timestamp
   └─ BACKUP_TS="20260415-120000"

4. Restore and restart
   ├─ cp backups/.env.$BACKUP_TS .env
   ├─ cd infra
   └─ docker compose down && up -d

5. Verify
   └─ curl http://localhost:8080/health
```

---

## 📊 Monitoring Points

```
┌─────────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT MONITORING                        │
└─────────────────────────────────────────────────────────────────┘

Pre-Deployment
├─ GitHub Actions: CI status badge
└─ Git log: Recent commits

During Deployment
├─ GitHub Actions: Live logs
├─ SSH Terminal: deploy.sh output
└─ Server: docker compose logs -f

Post-Deployment
├─ Health Endpoints:
│  ├─ http://SERVER:8080/health (target-service)
│  ├─ http://SERVER:8081/health (listener)
│  ├─ http://SERVER:8082/health (agent)
│  └─ http://SERVER:9090/-/healthy (prometheus)
├─ Prometheus: http://SERVER:9090/targets
├─ Grafana: http://SERVER:3001
└─ Agent Stats: http://SERVER:8082/stats

Continuous
├─ docker stats (resource usage)
├─ df -h (disk space)
└─ docker compose ps (container status)
```

---

## 🎯 Best Practices Implemented

### CI/CD
- [x] Separated CI and CD workflows
- [x] Manual approval for production deployments
- [x] Environment-specific configurations
- [x] Automated testing (81 tests)
- [x] Docker image caching
- [x] Deployment versioning (SHA tags)

### Deployment
- [x] Zero-downtime rolling updates
- [x] Health checks between updates
- [x] Automatic backups before deployment
- [x] Automatic rollback on failure
- [x] Deployment logging and history

### Security
- [x] SSH key authentication only
- [x] Secrets in GitHub Secrets (not in code)
- [x] Private Docker registry
- [x] Firewall configured
- [x] Minimal user permissions

### Operations
- [x] Log rotation configured
- [x] Resource limits defined
- [x] Monitoring dashboards
- [x] SLO-based alerting
- [x] Comprehensive documentation

---

## 📚 Related Documentation

- [CD Setup Guide](./CD-SETUP-GUIDE.md) - Quick start
- [Deployment Guide](./DEPLOYMENT.md) - Complete reference
- [Main README](../README.md) - Project overview
- [CI Configuration](../.github/workflows/ci.yml) - CI pipeline
- [CD Configuration](../.github/workflows/cd.yml) - CD pipeline

---

**Last Updated**: 2026-04-15
**Pipeline Status**: ✅ Operational
