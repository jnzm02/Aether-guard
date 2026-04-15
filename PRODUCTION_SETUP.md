# Production CD Setup - Step-by-Step Guide

This guide will help you configure the CD pipeline for your **live production server at YOUR_SERVER_IP**.

---

## 🎯 What We Know

From the sandbox configuration, I found:
- **Server IP**: `YOUR_SERVER_IP`
- **Live URLs**:
  - Monitoring: https://monitor.aether-guard.com
  - Application: https://app.aether-guard.com
  - Prometheus: https://prometheus.aether-guard.com
  - Alertmanager: https://alerts.aether-guard.com
  - Listener: https://listener.aether-guard.com
  - Agent: https://agent.aether-guard.com
- **GitHub Repo**: https://github.com/jnzm02/aether-guard

---

## 📋 Required Information Checklist

Before proceeding, gather these credentials:

- [ ] **SSH Private Key** for server access (you mentioned you have this)
- [ ] **SSH Username** (likely `root` or `deploy`)
- [ ] **Anthropic API Key** (`sk-ant-api03-...`)
- [ ] **Docker Registry** choice:
  - Docker Hub username/password
  - OR GitHub Container Registry token
  - OR DigitalOcean Container Registry credentials

---

## 🔐 Step 1: Add GitHub Secrets

Go to your GitHub repository: https://github.com/jnzm02/aether-guard

Navigate to: **Settings → Secrets and variables → Actions → New repository secret**

### Add These 7 Secrets:

#### 1. SSH_PRIVATE_KEY
```
Name: SSH_PRIVATE_KEY
Value: [Paste your ENTIRE SSH private key including the header/footer]

Example format:
-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAABlwAAAAdzc2gtcn
NhAAAAAwEAAQAAAYEAxy... (many lines)
...
-----END OPENSSH PRIVATE KEY-----
```

**Important**: Include the `-----BEGIN` and `-----END` lines!

#### 2. SERVER_HOST
```
Name: SERVER_HOST
Value: YOUR_SERVER_IP
```

#### 3. SERVER_USER
```
Name: SERVER_USER
Value: root
```
(Change to `deploy` if you're using a non-root user)

#### 4. ANTHROPIC_API_KEY
```
Name: ANTHROPIC_API_KEY
Value: sk-ant-api03-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

Get this from: https://console.anthropic.com/settings/keys

#### 5. DOCKER_REGISTRY

Choose ONE of these options:

**Option A: Docker Hub (Easiest)**
```
Name: DOCKER_REGISTRY
Value: docker.io/YOUR_DOCKERHUB_USERNAME

Or simply:
Value: YOUR_DOCKERHUB_USERNAME
```

**Option B: GitHub Container Registry (Recommended for private repos)**
```
Name: DOCKER_REGISTRY
Value: ghcr.io/jnzm02
```

**Option C: DigitalOcean Container Registry**
```
Name: DOCKER_REGISTRY
Value: registry.digitalocean.com/YOUR_REGISTRY_NAME
```

#### 6. DOCKER_REGISTRY_USERNAME

**For Docker Hub:**
```
Name: DOCKER_REGISTRY_USERNAME
Value: YOUR_DOCKERHUB_USERNAME
```

**For GitHub Container Registry:**
```
Name: DOCKER_REGISTRY_USERNAME
Value: jnzm02
```
(Your GitHub username)

**For DigitalOcean:**
```
Name: DOCKER_REGISTRY_USERNAME
Value: YOUR_DO_USERNAME
```

#### 7. DOCKER_REGISTRY_PASSWORD

**For Docker Hub:**
```
Name: DOCKER_REGISTRY_PASSWORD
Value: YOUR_DOCKERHUB_PASSWORD
```
(Or use an access token: https://hub.docker.com/settings/security)

**For GitHub Container Registry:**
```
Name: DOCKER_REGISTRY_PASSWORD
Value: ghp_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```
Generate a Personal Access Token:
1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Name: "Aether Guard GHCR"
4. Select scopes: `write:packages`, `read:packages`, `delete:packages`
5. Click "Generate token"
6. Copy the token (starts with `ghp_`)

**For DigitalOcean:**
```
Name: DOCKER_REGISTRY_PASSWORD
Value: dop_v1_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
```
Generate token in DO dashboard: https://cloud.digitalocean.com/account/api/tokens

---

## 🧪 Step 2: Test SSH Connection

Before running CD, verify SSH access works:

```bash
# Test SSH connection (from your local machine)
ssh -i /path/to/your/private/key root@YOUR_SERVER_IP

# If it works, you should see the Ubuntu welcome message
# Exit with: exit
```

If SSH fails:
- Check firewall allows port 22
- Verify the private key has correct permissions: `chmod 600 ~/.ssh/your_key`
- Ensure public key is in server's `~/.ssh/authorized_keys`

---

## 🚀 Step 3: Run First Deployment

Once all GitHub Secrets are configured:

1. Go to: https://github.com/jnzm02/aether-guard/actions
2. Click on **"CD (Production Deployment)"** workflow
3. Click **"Run workflow"** button (top right)
4. Select `production` from dropdown
5. Click **"Run workflow"** green button
6. Monitor the deployment progress

### What Will Happen:

```
1. Build & Push (5-10 min)
   - Builds Docker images for all 3 services
   - Pushes to your registry with SHA tags

2. Deploy (2-3 min)
   - SSHs into YOUR_SERVER_IP
   - Copies deployment files
   - Runs deploy.sh script
   - Performs rolling update

3. Verify (1 min)
   - Health checks all services
   - Tests Prometheus targets

4. Success ✓ or Auto-rollback ✗
```

---

## 🔍 Step 4: Verify Deployment

After CD completes successfully, verify everything is running:

### From Your Local Machine:

```bash
# Check target-service
curl https://app.aether-guard.com/health

# Check listener
curl https://listener.aether-guard.com/health

# Check agent
curl https://agent.aether-guard.com/health

# Check Prometheus
curl https://prometheus.aether-guard.com/-/healthy

# Check Grafana
open https://monitor.aether-guard.com
# Login: admin / aether-guard
```

### SSH Into Server:

```bash
ssh root@YOUR_SERVER_IP

# Check running containers
cd /opt/aether-guard/infra
docker compose ps

# Should show 6 containers running:
# target-service, listener, agent, prometheus, alertmanager, grafana

# View logs
docker compose logs -f agent

# Check disk space
df -h

# View recent deployments
ls -lh /opt/aether-guard/backups/
```

---

## 🎛️ Optional: Setup Docker Registry

### Option 1: GitHub Container Registry (Recommended)

Already set up! Just need the Personal Access Token (see Step 1).

### Option 2: Docker Hub

```bash
# Create Docker Hub account (if you don't have one)
# https://hub.docker.com/signup

# Login locally to test
docker login
Username: YOUR_USERNAME
Password: YOUR_PASSWORD

# Your registry will be: docker.io/YOUR_USERNAME
# Or simply: YOUR_USERNAME
```

### Option 3: DigitalOcean Container Registry

```bash
# Install doctl
brew install doctl  # macOS
# or
snap install doctl  # Linux

# Authenticate
doctl auth init

# Create registry
doctl registry create aether-guard-prod

# Login
doctl registry login

# Your registry will be:
# registry.digitalocean.com/aether-guard-prod
```

---

## 🚨 Troubleshooting

### Issue: "Permission denied (publickey)"

**Solution**: SSH key not configured correctly

```bash
# Verify your SSH key is correct
cat /path/to/your/private/key

# Should start with: -----BEGIN OPENSSH PRIVATE KEY-----

# Ensure you copied the ENTIRE key to GitHub Secret
# Including the BEGIN and END lines
```

### Issue: "Docker registry authentication failed"

**Solution**: Registry credentials are wrong

```bash
# Test registry login manually on server
ssh root@YOUR_SERVER_IP
echo "YOUR_PASSWORD" | docker login YOUR_REGISTRY -u YOUR_USERNAME --password-stdin

# If it works, you've proven the credentials are correct
# Then update GitHub Secrets to match
```

### Issue: "Health check failed"

**Solution**: Service didn't start properly

```bash
# SSH into server
ssh root@YOUR_SERVER_IP
cd /opt/aether-guard/infra

# Check which service failed
docker compose ps

# View logs
docker compose logs target-service
docker compose logs agent

# Common issue: Missing ANTHROPIC_API_KEY
cat /opt/aether-guard/.env | grep ANTHROPIC_API_KEY

# Restart failed service
docker compose restart agent
```

---

## 📊 Monitoring Your Deployment

### GitHub Actions

View all deployments: https://github.com/jnzm02/aether-guard/actions

- Green checkmark ✓ = Success
- Red X ✗ = Failed (check logs)
- Orange dot = In progress

### Production Dashboards

- **Grafana**: https://monitor.aether-guard.com
- **Prometheus**: https://prometheus.aether-guard.com
- **Alertmanager**: https://alerts.aether-guard.com

### Server Logs

```bash
ssh root@YOUR_SERVER_IP
cd /opt/aether-guard/infra

# All services
docker compose logs -f

# Specific service
docker compose logs -f agent --tail=100

# Check for errors
docker compose logs | grep ERROR
```

---

## ✅ Post-Deployment Checklist

After your first successful deployment:

- [ ] All 6 containers running: `docker compose ps`
- [ ] All health checks passing
- [ ] Grafana accessible at https://monitor.aether-guard.com
- [ ] Prometheus targets up
- [ ] Agent processing alerts: `curl https://agent.aether-guard.com/stats`
- [ ] SSL certificates valid (Let's Encrypt)
- [ ] Firewall configured: `ufw status`
- [ ] Deployment backup created: `ls /opt/aether-guard/backups/`

---

## 🔄 Future Deployments

For subsequent deployments:

1. Push code to `main` branch
2. Wait for CI to pass ✓
3. Go to Actions → CD → Run workflow
4. Select `production`
5. Click "Run workflow"
6. Monitor progress
7. Verify health endpoints

---

## 📞 Need Help?

If you encounter issues:

1. Check the troubleshooting section above
2. Review GitHub Actions logs
3. SSH into server and check `docker compose logs`
4. Verify all GitHub Secrets are correct
5. Ensure server has enough disk space: `df -h`

---

**Next Steps**: Complete Step 1 (add GitHub Secrets), then proceed to Step 3 (run deployment).

Once you've added the 7 GitHub Secrets, let me know and I'll help you trigger the first deployment!
