# Quick Deploy Reference Card

**Server**: YOUR_SERVER_IP
**Repo**: https://github.com/jnzm02/aether-guard

---

## 🚀 First-Time Setup (5 Minutes)

### 1. Add GitHub Secrets
Go to: https://github.com/jnzm02/aether-guard/settings/secrets/actions

Click **"New repository secret"** and add these **7 secrets**:

| Secret Name | Example Value |
|-------------|---------------|
| `SSH_PRIVATE_KEY` | `-----BEGIN OPENSSH PRIVATE KEY-----`<br>`...your full key...`<br>`-----END OPENSSH PRIVATE KEY-----` |
| `SERVER_HOST` | `YOUR_SERVER_IP` |
| `SERVER_USER` | `root` |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-XXXX...` |
| `DOCKER_REGISTRY` | `ghcr.io/jnzm02` (GitHub CR)<br>OR `docker.io/youruser` (Docker Hub) |
| `DOCKER_REGISTRY_USERNAME` | `jnzm02` (GitHub username)<br>OR your Docker Hub username |
| `DOCKER_REGISTRY_PASSWORD` | `ghp_XXXX...` (GitHub PAT)<br>OR Docker Hub password |

### 2. Test Setup (Optional)
```bash
cd /Users/nizamijussupov/Desktop/AI/Aether\ Guard
./scripts/verify-cd-setup.sh
```

### 3. Deploy!
1. Go to: https://github.com/jnzm02/aether-guard/actions
2. Click **"CD (Production Deployment)"**
3. Click **"Run workflow"** → Select **"production"** → Click **"Run workflow"**
4. Watch the magic happen! 🎉

---

## 🔄 Regular Deployments (30 Seconds)

```bash
# 1. Make changes
git add .
git commit -m "feat: your change"
git push

# 2. Wait for CI ✓

# 3. Deploy via GitHub UI
# Actions → CD → Run workflow → production
```

---

## 🔍 Quick Commands

### Check Status
```bash
ssh root@YOUR_SERVER_IP "cd /opt/aether-guard/infra && docker compose ps"
```

### View Logs
```bash
ssh root@YOUR_SERVER_IP "cd /opt/aether-guard/infra && docker compose logs -f agent"
```

### Health Check
```bash
curl https://app.aether-guard.com/health
curl https://agent.aether-guard.com/health
```

### Dashboards
- Grafana: https://monitor.aether-guard.com (admin/aether-guard)
- Prometheus: https://prometheus.aether-guard.com

---

## 🚨 Emergency Rollback

```bash
ssh root@YOUR_SERVER_IP
cd /opt/aether-guard
ls backups/  # Find latest backup timestamp
cp backups/.env.TIMESTAMP .env
cd infra && docker compose down && docker compose up -d
```

---

## 🎓 Docker Registry Quick Setup

### GitHub Container Registry (Recommended)
```bash
# 1. Create Personal Access Token:
#    https://github.com/settings/tokens/new
#    Scopes: write:packages, read:packages

# 2. Use in GitHub Secrets:
DOCKER_REGISTRY=ghcr.io/jnzm02
DOCKER_REGISTRY_USERNAME=jnzm02
DOCKER_REGISTRY_PASSWORD=ghp_YOUR_TOKEN_HERE
```

### Docker Hub
```bash
# 1. Create account: https://hub.docker.com/signup
# 2. Use in GitHub Secrets:
DOCKER_REGISTRY=docker.io/youruser
DOCKER_REGISTRY_USERNAME=youruser
DOCKER_REGISTRY_PASSWORD=your_password
```

---

## 📞 Help

- Full guide: `PRODUCTION_SETUP.md`
- Troubleshooting: `docs/CD-SETUP-GUIDE.md`
- Architecture: `docs/CICD-ARCHITECTURE.md`

**Ready?** → Add GitHub Secrets → Run CD Workflow → Done! ✓
