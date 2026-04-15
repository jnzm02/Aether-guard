# Continuous Deployment Setup Guide

Quick reference for setting up CD to your DigitalOcean Droplet.

---

## 🚀 Quick Start (5 Steps)

### 1. Prepare Your Server

SSH into your DigitalOcean Droplet and run:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/Aether-guard/main/scripts/setup-server.sh | sudo bash
```

Or manually:

```bash
# Update and install Docker
sudo apt-get update && sudo apt-get upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo apt-get install -y docker-compose-plugin

# Create deployment directory
sudo mkdir -p /opt/aether-guard/{infra,scripts,backups}
```

### 2. Generate SSH Key for GitHub Actions

On your **local machine**:

```bash
# Generate new SSH key pair
ssh-keygen -t ed25519 -C "github-actions-aether-guard" -f ~/.ssh/github_actions_aether

# Copy public key to clipboard (macOS)
cat ~/.ssh/github_actions_aether.pub | pbcopy

# Copy private key to clipboard (for GitHub Secret)
cat ~/.ssh/github_actions_aether | pbcopy
```

### 3. Add Public Key to Server

On your **server**:

```bash
# Create/edit authorized_keys
mkdir -p ~/.ssh
nano ~/.ssh/authorized_keys

# Paste the public key, save and exit
# Then set permissions:
chmod 600 ~/.ssh/authorized_keys
chmod 700 ~/.ssh
```

Test the connection:

```bash
ssh -i ~/.ssh/github_actions_aether root@YOUR_SERVER_IP
```

### 4. Configure GitHub Secrets

Go to: **GitHub Repository → Settings → Secrets and variables → Actions → New repository secret**

Add these secrets:

| Secret Name | Value | How to Get |
|-------------|-------|------------|
| `SSH_PRIVATE_KEY` | Your private key | `cat ~/.ssh/github_actions_aether` |
| `SERVER_HOST` | `YOUR.SERVER.IP.ADDRESS` | DigitalOcean dashboard or `curl ifconfig.me` |
| `SERVER_USER` | `root` or `deploy` | Username you SSH with |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` | https://console.anthropic.com/ |
| `DOCKER_REGISTRY` | `registry.digitalocean.com/your-registry` | Your Docker registry URL |
| `DOCKER_REGISTRY_USERNAME` | Your registry username | From registry provider |
| `DOCKER_REGISTRY_PASSWORD` | Your registry token/password | From registry provider |

### 5. Run First Deployment

1. Go to **GitHub → Actions → CD (Production Deployment)**
2. Click **Run workflow**
3. Select `production` environment
4. Click **Run workflow** button
5. Monitor the deployment progress

---

## 📋 GitHub Secrets Cheat Sheet

### Required Secrets

```bash
SSH_PRIVATE_KEY          # Private SSH key (keep the BEGIN/END lines)
SERVER_HOST              # e.g., 192.168.1.100
SERVER_USER              # e.g., root or deploy
ANTHROPIC_API_KEY        # Claude API key from console.anthropic.com
DOCKER_REGISTRY          # e.g., registry.digitalocean.com/yourname
DOCKER_REGISTRY_USERNAME # Registry username
DOCKER_REGISTRY_PASSWORD # Registry password/token
```

### Optional Variables (Repository Variables)

```bash
APP_URL                  # e.g., http://YOUR_SERVER_IP:8080
```

---

## 🔧 Docker Registry Setup

### Option 1: DigitalOcean Container Registry

```bash
# Install doctl CLI
brew install doctl  # macOS

# Authenticate
doctl auth init

# Create registry
doctl registry create aether-guard-registry

# Get credentials
doctl registry login

# Your registry URL will be:
# registry.digitalocean.com/aether-guard-registry
```

### Option 2: Docker Hub

```bash
# Login
docker login

# Your registry will be:
# docker.io/YOUR_USERNAME
# Or just: YOUR_USERNAME (Docker Hub is default)

# Use your Docker Hub username and password for secrets
```

### Option 3: GitHub Container Registry (ghcr.io)

```bash
# Create GitHub Personal Access Token
# Settings → Developer settings → Personal access tokens → Tokens (classic)
# Scopes: write:packages, read:packages

# Login
echo $GITHUB_TOKEN | docker login ghcr.io -u YOUR_USERNAME --password-stdin

# Your registry will be:
# ghcr.io/YOUR_USERNAME

# Use your GitHub username and PAT for secrets
```

---

## 🎯 Deployment Workflow Overview

```mermaid
graph LR
    A[Push to main] --> B[CI Tests Pass]
    B --> C[Manual: Run CD Workflow]
    C --> D[Build Docker Images]
    D --> E[Push to Registry]
    E --> F[SSH to Server]
    F --> G[Pull Images]
    G --> H[Rolling Update]
    H --> I[Health Checks]
    I --> J{Healthy?}
    J -->|Yes| K[Deployment Complete]
    J -->|No| L[Auto Rollback]
```

### Pipeline Stages

1. **Build & Push** (~5-10 min)
   - Builds 3 Docker images (target-service, listener, agent)
   - Pushes to private registry with commit SHA tag

2. **Deploy** (~2-3 min)
   - Copies deployment files to server
   - Pulls new images from registry
   - Performs rolling update (one service at a time)
   - Runs health checks

3. **Verify** (~1 min)
   - Checks container status
   - Tests Prometheus targets
   - Verifies application endpoints

4. **Rollback** (if needed, ~1 min)
   - Automatically triggered on failure
   - Restores previous .env and docker-compose state

---

## 🔍 Troubleshooting

### SSH Connection Failed

**Error:** `Permission denied (publickey)`

**Solution:**
```bash
# Verify public key is in server's authorized_keys
ssh root@YOUR_SERVER_IP cat ~/.ssh/authorized_keys

# Verify private key in GitHub Secret has correct format
# Must include: -----BEGIN OPENSSH PRIVATE KEY-----
```

### Docker Registry Authentication Failed

**Error:** `unauthorized: authentication required`

**Solution:**
```bash
# Test registry login on server
ssh root@YOUR_SERVER_IP
echo "YOUR_PASSWORD" | docker login YOUR_REGISTRY -u YOUR_USERNAME --password-stdin

# Verify secrets in GitHub are correct
```

### Health Check Failed

**Error:** `Health check failed for target-service`

**Solution:**
```bash
# SSH into server and check logs
ssh root@YOUR_SERVER_IP
cd /opt/aether-guard/infra
docker compose logs target-service

# Check if service is listening
curl -v http://localhost:8080/health

# Restart specific service
docker compose restart target-service
```

### Out of Disk Space

**Solution:**
```bash
# SSH into server
ssh root@YOUR_SERVER_IP

# Check disk usage
df -h

# Clean up old Docker images
docker system prune -a -f

# Check which images are taking space
docker images --format "{{.Repository}}:{{.Tag}}\t{{.Size}}"
```

---

## 📊 Monitoring Deployment

### View Logs in Real-Time

```bash
# SSH into server
ssh root@YOUR_SERVER_IP

# View all logs
cd /opt/aether-guard/infra
docker compose logs -f

# View specific service
docker compose logs -f agent

# View last 100 lines
docker compose logs --tail=100
```

### Check Service Status

```bash
# Container status
docker compose ps

# Resource usage
docker stats

# Health endpoints
curl http://localhost:8080/health  # target-service
curl http://localhost:8081/health  # listener
curl http://localhost:8082/health  # agent
```

### View Deployment History

```bash
# List backups
ls -lh /opt/aether-guard/backups/

# View specific backup
cat /opt/aether-guard/backups/.env.20260415-140530
```

---

## 🔐 Security Checklist

- [ ] SSH key is unique for GitHub Actions (not reused from personal key)
- [ ] Private key is only in GitHub Secrets (never committed to repo)
- [ ] Firewall (UFW) is enabled on server
- [ ] Only necessary ports are open (22, 80, 443)
- [ ] Application ports (8080-8082, 9090-9093) are blocked externally
- [ ] Docker registry uses authentication (private repos)
- [ ] Anthropic API key is stored in GitHub Secrets only
- [ ] Server user has minimal permissions (not root if possible)
- [ ] SSH password authentication is disabled
- [ ] Regular security updates enabled on server

---

## 🚨 Emergency Rollback

If deployment breaks production:

### Option 1: Via GitHub Actions

1. Go to **Actions → CD → Latest Run**
2. Check the **Rollback** job logs
3. If auto-rollback didn't trigger, manually re-run previous successful deployment

### Option 2: Manual SSH Rollback

```bash
ssh root@YOUR_SERVER_IP
cd /opt/aether-guard

# Find latest backup
ls -lh backups/

# Restore backup (replace timestamp)
BACKUP_TS="20260415-120000"
cp backups/.env.$BACKUP_TS .env

# Restart services
cd infra
docker compose down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Verify
docker compose ps
curl http://localhost:8080/health
```

---

## 📚 Additional Resources

- [Full Deployment Guide](./DEPLOYMENT.md)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [DigitalOcean Droplet Setup](https://docs.digitalocean.com/products/droplets/how-to/create/)
- [Docker Compose Production Guide](https://docs.docker.com/compose/production/)

---

## 🎓 Next Steps

After successful deployment:

1. **Setup monitoring**: Configure external uptime monitoring (UptimeRobot, etc.)
2. **Enable HTTPS**: Use Caddy or Nginx with Let's Encrypt
3. **Backup automation**: Setup daily backups of `/opt/aether-guard/data/`
4. **Alerting**: Configure Alertmanager to send alerts to Slack/PagerDuty
5. **Scaling**: If needed, move to Kubernetes using `k8s/` manifests

---

For questions, see [DEPLOYMENT.md](./DEPLOYMENT.md) or open an issue on GitHub.