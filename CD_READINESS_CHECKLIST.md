# CD Readiness Checklist

## Status: ✅ CD Pipeline Configured (Auto-triggers on main push)

The CD pipeline is now configured to **automatically deploy to production** whenever code is pushed or merged to the `main` branch.

---

## ⚠️ Prerequisites Before First Deployment

Before your first automated deployment, you MUST complete these steps:

### 1. GitHub Secrets (Required) ✋

Go to: https://github.com/jnzm02/aether-guard/settings/secrets/actions

Add these **7 required secrets**:

| Secret Name | Description | Example |
|-------------|-------------|---------|
| `SSH_PRIVATE_KEY` | Private SSH key for server access | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `SERVER_HOST` | Your production server IP | `123.45.67.89` |
| `SERVER_USER` | SSH username | `root` or `deploy` |
| `ANTHROPIC_API_KEY` | Claude API key | `sk-ant-api03-...` |
| `DOCKER_REGISTRY` | Docker registry URL | `ghcr.io/jnzm02` or `docker.io/youruser` |
| `DOCKER_REGISTRY_USERNAME` | Registry username | Your GitHub/Docker username |
| `DOCKER_REGISTRY_PASSWORD` | Registry token/password | Personal access token or password |

**Without these secrets, the CD pipeline will FAIL immediately.**

### 2. Optional Telegram Notifications (Recommended) 📱

For deployment notifications, add these **2 optional secrets**:

| Secret Name | How to Get It |
|-------------|---------------|
| `TELEGRAM_BOT_TOKEN` | Create bot via @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | Your chat ID (use @userinfobot) |

See `TELEGRAM_SETUP.md` for detailed instructions.

### 3. Server Prerequisites ✅

Your production server must have:

- ✅ Docker installed (`docker --version`)
- ✅ Docker Compose installed (`docker compose version`)
- ✅ SSH access configured for the user specified in `SERVER_USER`
- ✅ Port 22 open for SSH
- ✅ Sufficient disk space (at least 10GB free)

**Verify with:**
```bash
ssh <SERVER_USER>@<SERVER_HOST> "docker --version && docker compose version && df -h"
```

---

## 🚀 How CD Works Now

### Automatic Deployment Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Developer pushes to main branch                             │
│  (or PR is merged to main)                                   │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  CI Workflow runs (in parallel)                              │
│  ├─ Go tests                                                 │
│  ├─ Python tests                                             │
│  ├─ Lint checks                                              │
│  ├─ Config validation                                        │
│  └─ Docker builds                                            │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  CD Workflow triggers automatically                          │
│  ├─ Build & push Docker images                              │
│  ├─ SSH into production server                              │
│  ├─ Deploy with zero-downtime rolling update                │
│  ├─ Health checks                                            │
│  └─ Auto-rollback on failure                                │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  🎉 Production is live with latest code!                     │
└─────────────────────────────────────────────────────────────┘
```

### Manual Deployment (Optional)

You can still trigger deployments manually:

1. Go to: https://github.com/jnzm02/aether-guard/actions
2. Click **"CD (Production Deployment)"**
3. Click **"Run workflow"**
4. Select environment (production/staging)
5. Click **"Run workflow"**

---

## ⚠️ Important Safety Notes

### Before Merging to Main

1. **Always ensure CI is green** ✅
   - Check: https://github.com/jnzm02/aether-guard/actions
   - Wait for all CI checks to pass before merging PRs

2. **Use Pull Requests for code review**
   - Don't push directly to main (unless you're confident)
   - Have another team member review changes
   - Use branch protection rules (optional but recommended)

3. **Test in staging first** (if you set up staging environment)
   - Push to `staging` branch first
   - Manually trigger CD with "staging" environment
   - Verify everything works before merging to main

### What Happens on Failure

If deployment fails:

1. **Automatic rollback** triggers for production
2. Previous version is restored
3. You receive a notification (if Telegram is configured)
4. Logs are available in GitHub Actions tab

### Emergency Rollback

If you need to manually rollback:

```bash
ssh <SERVER_USER>@<SERVER_HOST>
cd /opt/aether-guard
ls -lh backups/  # Find latest backup
cp backups/.env.YYYYMMDD-HHMMSS .env
cd infra
docker compose down
docker compose up -d
```

---

## 📋 Pre-Deployment Verification Script

Run this before your first automated deployment:

```bash
cd "/Users/nizamijussupov/Desktop/AI/Aether Guard"
./scripts/verify-cd-setup.sh
```

This will verify:
- ✅ SSH connection works
- ✅ Docker is installed on server
- ✅ Disk space is sufficient
- ✅ Required directories exist

---

## 🎯 Quick Start: Enable CD in 3 Steps

1. **Add GitHub Secrets** (5 minutes)
   - Go to: https://github.com/jnzm02/aether-guard/settings/secrets/actions
   - Add the 7 required secrets listed above

2. **Verify Server Setup** (1 minute)
   ```bash
   ./scripts/verify-cd-setup.sh
   ```

3. **Push to main** (done!)
   ```bash
   git push origin main
   ```

   CD will automatically:
   - Build Docker images
   - Deploy to production
   - Run health checks
   - Notify you of success/failure

---

## 📊 Monitoring Deployments

### GitHub Actions Dashboard
- View all workflows: https://github.com/jnzm02/aether-guard/actions
- Each push to main will show both CI and CD workflows running

### Production Health Endpoints
- App: `http://<SERVER_HOST>:8080/health`
- Listener: `http://<SERVER_HOST>:8081/health`
- Agent: `http://<SERVER_HOST>:8082/health`
- Prometheus: `http://<SERVER_HOST>:9090/-/healthy`

### SSH into Server
```bash
ssh <SERVER_USER>@<SERVER_HOST>
cd /opt/aether-guard/infra
docker compose ps    # View running containers
docker compose logs -f agent  # View live logs
```

---

## 📚 Additional Documentation

| Document | Purpose |
|----------|---------|
| `CD_CONFIGURATION_COMPLETE.md` | Original CD setup documentation |
| `PRODUCTION_SETUP.md` | Detailed production setup guide |
| `QUICK_DEPLOY.md` | Quick deployment reference |
| `docs/DEPLOYMENT.md` | Complete deployment guide |
| `HOW_TO_ADD_GITHUB_SECRETS.md` | Step-by-step secret configuration |

---

## ✅ Summary

**What Changed:**
- CD workflow now triggers automatically on push to `main`
- Defaults to production environment for automatic deployments
- Manual deployments still work via GitHub Actions UI

**What You Need to Do:**
1. Add 7 GitHub Secrets (see above)
2. Verify server prerequisites
3. Push to main → CD runs automatically

**Safety Features:**
- ✅ Automatic rollback on failure
- ✅ Health checks before marking deployment successful
- ✅ Zero-downtime rolling updates
- ✅ Backup of previous configuration
- ✅ Telegram notifications (optional)

---

**Questions?** See the documentation links above or run `./scripts/verify-cd-setup.sh` to diagnose issues.

**Ready to deploy?** Add the GitHub Secrets and push to main! 🚀
