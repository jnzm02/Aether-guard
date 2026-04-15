# Aether-Guard — Production Deployment Guide

This guide covers deploying Aether-Guard to a DigitalOcean Droplet (or any VPS) using GitHub Actions CD pipeline.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Server Setup](#server-setup)
3. [GitHub Secrets Configuration](#github-secrets-configuration)
4. [Deployment Workflow](#deployment-workflow)
5. [Manual Deployment](#manual-deployment)
6. [Rollback Procedure](#rollback-procedure)
7. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Tools
- **DigitalOcean Droplet** (or any VPS with Ubuntu 22.04+)
  - Minimum: 2 vCPUs, 4GB RAM, 80GB SSD
  - Recommended: 4 vCPUs, 8GB RAM, 160GB SSD
- **Docker & Docker Compose** installed on server
- **Private Docker Registry** (Docker Hub, GitHub Container Registry, or DigitalOcean Container Registry)
- **SSH access** to your server
- **Anthropic API key** for Claude AI

### Local Tools
- Git
- SSH client
- GitHub account with repository access

---

## Server Setup

### 1. Initial Server Configuration

SSH into your DigitalOcean Droplet:

```bash
ssh root@YOUR_SERVER_IP
```

### 2. Install Docker & Docker Compose

```bash
# Update system packages
apt-get update && apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Install Docker Compose V2
apt-get install -y docker-compose-plugin

# Verify installation
docker --version
docker compose version
```

### 3. Create Deployment User (Optional but Recommended)

```bash
# Create deploy user
useradd -m -s /bin/bash deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh

# Copy your SSH public key (or generate new one for GitHub Actions)
# You'll add the GitHub Actions public key here later
```

### 4. Create Application Directory

```bash
# Create deployment directory
mkdir -p /opt/aether-guard/{infra,scripts,backups}
chown -R deploy:deploy /opt/aether-guard  # If using deploy user

# Create data directories for volumes
mkdir -p /opt/aether-guard/data/{prometheus,agent,grafana}
```

### 5. Configure Firewall

```bash
# Allow SSH, HTTP, HTTPS
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 8080:8082/tcp  # Application ports (optional, for debugging)
ufw allow 9090:9093/tcp  # Monitoring ports (optional, for debugging)
ufw allow 3001/tcp       # Grafana (optional)
ufw enable
```

### 6. Install Additional Dependencies

```bash
# Install curl, jq for deployment scripts
apt-get install -y curl jq

# Install logrotate for log management
apt-get install -y logrotate
```

---

## GitHub Secrets Configuration

Configure the following secrets in your GitHub repository:

**Settings → Secrets and variables → Actions → New repository secret**

### Required Secrets

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `SSH_PRIVATE_KEY` | Private SSH key for server access | `-----BEGIN OPENSSH PRIVATE KEY-----\n...` |
| `SERVER_HOST` | Server IP or hostname | `192.168.1.100` or `aether.example.com` |
| `SERVER_USER` | SSH username | `deploy` or `root` |
| `ANTHROPIC_API_KEY` | Claude API key | `sk-ant-api03-...` |
| `DOCKER_REGISTRY` | Private Docker registry URL | `registry.digitalocean.com/your-registry` |
| `DOCKER_REGISTRY_USERNAME` | Registry username | `your-username` |
| `DOCKER_REGISTRY_PASSWORD` | Registry password/token | `dop_v1_...` |

### Optional Variables (Repository Variables)

| Variable Name | Description | Default |
|---------------|-------------|---------|
| `APP_URL` | Production application URL | `http://YOUR_SERVER_IP:8080` |

---

## Setting Up SSH Key for GitHub Actions

### Option 1: Generate New SSH Key Pair (Recommended)

On your **local machine**:

```bash
# Generate new SSH key for GitHub Actions
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/github_actions_deploy

# Display public key (add this to server)
cat ~/.ssh/github_actions_deploy.pub

# Display private key (add this to GitHub Secrets as SSH_PRIVATE_KEY)
cat ~/.ssh/github_actions_deploy
```

On your **server**:

```bash
# Add the public key to authorized_keys
echo "YOUR_PUBLIC_KEY_HERE" >> /home/deploy/.ssh/authorized_keys
# or for root: /root/.ssh/authorized_keys

chmod 600 /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
```

### Option 2: Use Existing SSH Key

If you already have SSH access configured, copy your private key to GitHub Secrets.

**⚠️ Security Note:** Never commit private keys to your repository. Only add them to GitHub Secrets.

---

## Deployment Workflow

### Automatic Deployment (via GitHub Actions)

The CD pipeline is configured in `.github/workflows/cd.yml` with **manual approval** required.

#### Trigger Deployment

1. Go to **GitHub → Actions → CD (Production Deployment)**
2. Click **Run workflow**
3. Select environment:
   - `production` — Production server
   - `staging` — Staging server (if configured)
4. Click **Run workflow**

#### Workflow Steps

```
1. Build & Push Images
   ├─ Build target-service → Push to registry
   ├─ Build listener → Push to registry
   └─ Build agent → Push to registry

2. Deploy to Server
   ├─ SSH into server
   ├─ Copy deployment files
   ├─ Pull new images from registry
   ├─ Run deploy.sh script (rolling update)
   └─ Health check all services

3. Verify Deployment
   ├─ Check container status
   ├─ Test Prometheus targets
   └─ Verify logs

4. Rollback (if failure detected)
   └─ Restore previous state
```

#### Monitor Deployment

```bash
# SSH into server and monitor logs
ssh deploy@YOUR_SERVER_IP

cd /opt/aether-guard/infra
docker compose logs -f

# Check service status
docker compose ps

# View deployment history
ls -lh /opt/aether-guard/backups/
```

---

## Manual Deployment

If you need to deploy manually without GitHub Actions:

### 1. Build & Push Images Locally

```bash
# Login to your private registry
echo "YOUR_REGISTRY_PASSWORD" | docker login YOUR_REGISTRY -u YOUR_USERNAME --password-stdin

# Build images
docker build -t YOUR_REGISTRY/aether-guard/target-service:v1.0.0 services/target-service
docker build -t YOUR_REGISTRY/aether-guard/listener:v1.0.0 services/listener
docker build -t YOUR_REGISTRY/aether-guard/agent:v1.0.0 services/agent

# Push images
docker push YOUR_REGISTRY/aether-guard/target-service:v1.0.0
docker push YOUR_REGISTRY/aether-guard/listener:v1.0.0
docker push YOUR_REGISTRY/aether-guard/agent:v1.0.0
```

### 2. Deploy to Server

```bash
# Copy files to server
scp -r infra/ scripts/ deploy@YOUR_SERVER_IP:/opt/aether-guard/

# Create .env file
cat > .env.production << EOF
ANTHROPIC_API_KEY=sk-ant-...
CLAUDE_MODEL=claude-sonnet-4-5-20250929
CONFIDENCE_THRESHOLD=0.75
DRY_RUN=false
POLL_INTERVAL=10
DOCKER_REGISTRY=YOUR_REGISTRY
DOCKER_REGISTRY_USERNAME=YOUR_USERNAME
DOCKER_REGISTRY_PASSWORD=YOUR_PASSWORD
IMAGE_TAG=v1.0.0
EOF

scp .env.production deploy@YOUR_SERVER_IP:/opt/aether-guard/.env

# SSH and run deployment
ssh deploy@YOUR_SERVER_IP
cd /opt/aether-guard
chmod +x scripts/deploy.sh
./scripts/deploy.sh v1.0.0
```

---

## Rollback Procedure

### Automatic Rollback

The CD workflow automatically rolls back if health checks fail.

### Manual Rollback

```bash
# SSH into server
ssh deploy@YOUR_SERVER_IP
cd /opt/aether-guard

# View available backups
ls -lh backups/

# Restore specific backup
BACKUP_TIMESTAMP="20260415-143022"  # Choose from backups/ directory
cp backups/.env.${BACKUP_TIMESTAMP} .env
cp backups/docker-compose.${BACKUP_TIMESTAMP}.yml infra/docker-compose.yml

# Restart services
cd infra
docker compose down
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Verify rollback
docker compose ps
curl -sf http://localhost:8080/health
```

---

## Troubleshooting

### Services Won't Start

```bash
# Check container logs
cd /opt/aether-guard/infra
docker compose logs target-service
docker compose logs agent

# Check resource usage
docker stats

# Restart specific service
docker compose restart target-service
```

### Health Checks Failing

```bash
# Test health endpoints manually
curl -v http://localhost:8080/health
curl -v http://localhost:8081/health
curl -v http://localhost:8082/health

# Check if ports are listening
netstat -tulpn | grep -E '8080|8081|8082|9090|9093'
```

### Image Pull Errors

```bash
# Test registry authentication
echo "YOUR_PASSWORD" | docker login YOUR_REGISTRY -u YOUR_USERNAME --password-stdin

# Manually pull image
docker pull YOUR_REGISTRY/aether-guard/target-service:latest

# Check .env file
cat /opt/aether-guard/.env | grep DOCKER_REGISTRY
```

### Disk Space Issues

```bash
# Check disk usage
df -h

# Clean up Docker
docker system prune -a -f --volumes

# View large files
du -sh /opt/aether-guard/*
du -sh /var/lib/docker/*
```

### SSH Connection Issues

```bash
# Test SSH connection from local machine
ssh -v deploy@YOUR_SERVER_IP

# Check authorized_keys on server
cat /home/deploy/.ssh/authorized_keys

# Check SSH logs on server
tail -f /var/log/auth.log
```

### Application Errors

```bash
# Check agent logs for AI errors
cd /opt/aether-guard/infra
docker compose logs agent | grep ERROR

# Verify Anthropic API key
docker compose exec agent env | grep ANTHROPIC_API_KEY

# Test Claude API manually
docker compose exec agent python3 -c "
import os
from anthropic import Anthropic
client = Anthropic(api_key=os.environ['ANTHROPIC_API_KEY'])
print('API key is valid!')
"
```

---

## Post-Deployment Checklist

- [ ] All 6 services running: `docker compose ps`
- [ ] Health checks passing: `make health-check` (if using Makefile remotely)
- [ ] Prometheus targets up: http://YOUR_SERVER_IP:9090/targets
- [ ] Grafana accessible: http://YOUR_SERVER_IP:3001 (admin / aether-guard)
- [ ] Agent processing alerts: `curl http://localhost:8082/stats`
- [ ] Firewall configured: `ufw status`
- [ ] Backups created: `ls /opt/aether-guard/backups/`
- [ ] Logs rotating properly: `ls /var/log/` and check docker logs size

---

## Production Best Practices

1. **Enable HTTPS** — Use Nginx/Caddy reverse proxy with Let's Encrypt
2. **Monitor Disk Space** — Set up alerts when disk > 80% full
3. **Regular Backups** — Backup `/opt/aether-guard/data/` daily
4. **Rotate Logs** — Configure logrotate for Docker logs
5. **Update Images** — Deploy new versions regularly with `IMAGE_TAG`
6. **Security Scanning** — Scan Docker images with Trivy or Snyk
7. **Resource Limits** — Adjust docker-compose.prod.yml limits based on load
8. **Monitoring** — Set up external uptime monitoring (UptimeRobot, etc.)

---

## Additional Resources

- [Docker Documentation](https://docs.docker.com/)
- [DigitalOcean Droplet Setup](https://docs.digitalocean.com/products/droplets/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Anthropic API Documentation](https://docs.anthropic.com/)

---

For questions or issues, please open an issue on GitHub or consult the project README.