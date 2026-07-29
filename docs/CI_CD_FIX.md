# CI/CD Workflow Fix Guide

## Current Issue

The CD workflow is failing with:
```
Run mkdir -p ~/.ssh
Error: Process completed with exit code 1.
```

## Root Causes

1. **Missing GitHub Secret:** `SSH_PRIVATE_KEY` not configured
2. **Missing REDIS_PASSWORD:** New security requirement not in workflow
3. **Auto-deploy triggered:** Push to `main` attempted deployment without manual REDIS_PASSWORD setup

## Quick Fix: Skip Auto-Deploy (RECOMMENDED)

**The CI/CD failure is expected and OK.** You should:

1. **Ignore the GitHub Actions failure** - it's not critical
2. **Do manual deployment** following `SECURITY_FIX_DEPLOYMENT.md`
3. **Fix CI/CD later** (optional, for future deployments)

## Long-term Fix: Configure CI/CD

If you want automatic deployments to work:

### Step 1: Add Missing GitHub Secrets

Go to: `https://github.com/jnzm02/Aether-guard/settings/secrets/actions`

Add these secrets:

#### Required Secrets

| Secret Name | Value | How to Get |
|------------|-------|------------|
| `SSH_PRIVATE_KEY` | SSH private key for server | See below |
| `SERVER_HOST` | `116.202.19.79` | Your Hetzner server IP |
| `SERVER_USER` | `root` | Server username |
| `REDIS_PASSWORD` | Strong random password | `openssl rand -base64 32` |

#### Optional Secrets (if using private registry)

| Secret Name | Value |
|------------|-------|
| `DOCKER_REGISTRY` | Registry URL |
| `DOCKER_REGISTRY_USERNAME` | Registry username |
| `DOCKER_REGISTRY_PASSWORD` | Registry password |

### Step 2: Generate SSH Key for CI/CD

On your **local machine**:

```bash
# Generate dedicated deployment key
ssh-keygen -t ed25519 -C "github-actions-deploy" -f ~/.ssh/aether-guard-deploy
# Press Enter twice (no passphrase for CI/CD)

# Copy public key
cat ~/.ssh/aether-guard-deploy.pub
```

On your **server** (116.202.19.79):

```bash
# Add public key to authorized_keys
ssh root@116.202.19.79

mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Paste the public key content:
cat >> ~/.ssh/authorized_keys << 'EOF'
ssh-ed25519 AAAAC3... github-actions-deploy
EOF

chmod 600 ~/.ssh/authorized_keys
```

Back on your **local machine**:

```bash
# Copy PRIVATE key for GitHub Secret
cat ~/.ssh/aether-guard-deploy
# Copy the ENTIRE output including:
# -----BEGIN OPENSSH PRIVATE KEY-----
# ...
# -----END OPENSSH PRIVATE KEY-----
```

### Step 3: Add Secrets to GitHub

1. Go to: https://github.com/jnzm02/Aether-guard/settings/secrets/actions
2. Click "New repository secret"
3. Add each secret:

**SSH_PRIVATE_KEY:**
- Name: `SSH_PRIVATE_KEY`
- Value: (paste the entire private key from previous step)

**SERVER_HOST:**
- Name: `SERVER_HOST`
- Value: `116.202.19.79`

**SERVER_USER:**
- Name: `SERVER_USER`
- Value: `root`

**REDIS_PASSWORD:**
- Name: `REDIS_PASSWORD`
- Value: Run `openssl rand -base64 32` and paste output

**IMPORTANT:** The `REDIS_PASSWORD` in GitHub Secrets MUST match what you deploy manually!

### Step 4: Update Server with Same Password

After adding `REDIS_PASSWORD` to GitHub Secrets, SSH to server and ensure it matches:

```bash
ssh root@116.202.19.79
cd /opt/aether-guard

# Check current password in .env
grep REDIS_PASSWORD .env

# If it doesn't match GitHub Secret, update it:
# (Replace with the same value you put in GitHub Secrets)
sed -i 's/^REDIS_PASSWORD=.*/REDIS_PASSWORD=YOUR_GITHUB_SECRET_VALUE/' .env

# Restart Redis with new password
docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml restart redis agent
```

### Step 5: Test Auto-Deploy

1. Commit a small change:
   ```bash
   echo "# Test deploy" >> README.md
   git add README.md
   git commit -m "test: verify CI/CD auto-deploy"
   git push origin main
   ```

2. Watch GitHub Actions:
   - Go to: https://github.com/jnzm02/Aether-guard/actions
   - Watch the "CD (Production Deployment)" workflow
   - All jobs should pass: build-and-push → deploy → verify

3. If it fails, check logs for which secret is missing

## Alternative: Disable Auto-Deploy

If you prefer manual deployments only:

```yaml
# .github/workflows/cd.yml
on:
  # Remove this section:
  # push:
  #   branches:
  #     - main

  # Keep only manual trigger:
  workflow_dispatch:
    inputs:
      environment:
        description: 'Target environment'
        required: true
        default: 'production'
        type: choice
        options:
          - production
          - staging
```

This way, deployment only runs when you manually trigger it from GitHub Actions UI.

## Current Status

✅ **CD workflow updated** to include `REDIS_PASSWORD`
❌ **GitHub Secrets not configured** (you need to do this)
⏸️ **Auto-deploy disabled** until secrets are configured

## Next Steps

Choose one:

**Option A: Manual Deploy Only (Recommended for now)**
1. Ignore CI/CD failure
2. Follow `SECURITY_FIX_DEPLOYMENT.md`
3. Fix CI/CD later when convenient

**Option B: Fix CI/CD Now**
1. Add all GitHub Secrets (Step 1-3 above)
2. Ensure server REDIS_PASSWORD matches (Step 4)
3. Test with small commit (Step 5)

## Troubleshooting

### "Permission denied (publickey)"
- Check `SSH_PRIVATE_KEY` secret has the complete key (including headers/footers)
- Verify public key is in server's `~/.ssh/authorized_keys`
- Check server sshd_config allows key auth: `PubkeyAuthentication yes`

### "mkdir: cannot create directory '/home/user/.ssh': Permission denied"
- This usually means the secret is empty/malformed
- Re-copy the private key ensuring no extra whitespace

### "NOAUTH Authentication required"
- `REDIS_PASSWORD` in GitHub Secrets doesn't match server `.env`
- Update server `.env` to match GitHub Secret
- Restart Redis: `docker compose restart redis`

### Build succeeds but deployment fails
- Check `SERVER_HOST`, `SERVER_USER` secrets are correct
- SSH to server manually to verify connectivity: `ssh -i ~/.ssh/aether-guard-deploy root@116.202.19.79`

## Security Notes

- **Never commit SSH private keys** to the repository
- **Use dedicated deployment keys** (not your personal SSH key)
- **Rotate keys regularly** (every 90 days recommended)
- **Limit key scope** - deployment key should only access deployment directory
- **Monitor deployment logs** for unauthorized access attempts
