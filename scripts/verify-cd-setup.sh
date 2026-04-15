#!/usr/bin/env bash
#
# Aether-Guard CD Setup Verification Script
# Helps verify all prerequisites before running CD pipeline
#

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}✓${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_step() {
    echo -e "${BLUE}▶${NC} $1"
}

# Server details - configure these before running
SERVER_IP="${SERVER_IP:-YOUR_SERVER_IP}"
SERVER_USER="${SERVER_USER:-root}"

echo "════════════════════════════════════════════════════════════"
echo "   Aether-Guard CD Setup Verification"
echo "════════════════════════════════════════════════════════════"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. Check SSH Connection
# ─────────────────────────────────────────────────────────────────────────────
log_step "1. Checking SSH connection to $SERVER_IP..."

if command -v ssh &> /dev/null; then
    log_info "SSH client found"

    # Try to connect (with timeout)
    if timeout 10 ssh -o BatchMode=yes -o ConnectTimeout=5 "$SERVER_USER@$SERVER_IP" "echo 'SSH connection successful'" 2>/dev/null; then
        log_info "SSH connection to $SERVER_IP works!"
    else
        log_error "Cannot connect to $SERVER_IP via SSH"
        echo "  Possible issues:"
        echo "  - SSH key not added to server's authorized_keys"
        echo "  - Firewall blocking port 22"
        echo "  - Wrong username (try: export SERVER_USER=deploy)"
        echo ""
        echo "  Test manually: ssh $SERVER_USER@$SERVER_IP"
        exit 1
    fi
else
    log_error "SSH client not found"
    exit 1
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 2. Check GitHub CLI (optional but helpful)
# ─────────────────────────────────────────────────────────────────────────────
log_step "2. Checking GitHub CLI..."

if command -v gh &> /dev/null; then
    log_info "GitHub CLI installed: $(gh --version | head -1)"

    if gh auth status &> /dev/null; then
        log_info "GitHub CLI authenticated"

        # Check if we can access the repo
        if gh repo view jnzm02/aether-guard &> /dev/null; then
            log_info "Can access jnzm02/aether-guard repository"
        else
            log_warn "Cannot access jnzm02/aether-guard (might be private)"
        fi
    else
        log_warn "GitHub CLI not authenticated (run: gh auth login)"
    fi
else
    log_warn "GitHub CLI not installed (optional)"
    echo "  Install: https://cli.github.com/"
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 3. Check Server Prerequisites
# ─────────────────────────────────────────────────────────────────────────────
log_step "3. Checking server prerequisites..."

ssh "$SERVER_USER@$SERVER_IP" bash << 'EOF'
    GREEN='\033[0;32m'
    RED='\033[0;31m'
    YELLOW='\033[1;33m'
    NC='\033[0m'

    log_info() {
        echo -e "${GREEN}✓${NC} $1"
    }

    log_error() {
        echo -e "${RED}✗${NC} $1"
    }

    log_warn() {
        echo -e "${YELLOW}⚠${NC} $1"
    }

    # Check Docker
    if command -v docker &> /dev/null; then
        log_info "Docker installed: $(docker --version | head -1)"
    else
        log_error "Docker NOT installed"
        echo "  Install: curl -fsSL https://get.docker.com | sh"
        exit 1
    fi

    # Check Docker Compose
    if docker compose version &> /dev/null; then
        log_info "Docker Compose installed: $(docker compose version)"
    else
        log_error "Docker Compose NOT installed"
        echo "  Install: apt-get install docker-compose-plugin"
        exit 1
    fi

    # Check deployment directory
    if [ -d "/opt/aether-guard" ]; then
        log_info "Deployment directory exists: /opt/aether-guard"
    else
        log_warn "Deployment directory NOT found: /opt/aether-guard"
        echo "  Will be created during first deployment"
    fi

    # Check disk space
    DISK_USAGE=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
    if [ "$DISK_USAGE" -lt 80 ]; then
        log_info "Disk space OK: ${DISK_USAGE}% used"
    else
        log_warn "Disk space getting full: ${DISK_USAGE}% used"
        echo "  Consider cleaning up: docker system prune -a"
    fi

    # Check if services are already running
    if docker compose ps &> /dev/null 2>&1; then
        RUNNING=$(cd /opt/aether-guard/infra 2>/dev/null && docker compose ps --services --filter "status=running" | wc -l)
        if [ "$RUNNING" -gt 0 ]; then
            log_info "Services already running: $RUNNING containers"
        fi
    fi
EOF

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 4. Check Live Production URLs
# ─────────────────────────────────────────────────────────────────────────────
log_step "4. Checking production URLs..."

URLS=(
    "https://app.aether-guard.com/health"
    "https://monitor.aether-guard.com"
    "https://prometheus.aether-guard.com/-/healthy"
)

for url in "${URLS[@]}"; do
    if curl -sf --max-time 5 "$url" > /dev/null 2>&1; then
        log_info "$url is accessible"
    else
        log_warn "$url is NOT accessible"
    fi
done

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 5. Check Docker Registry Access
# ─────────────────────────────────────────────────────────────────────────────
log_step "5. Docker Registry Configuration..."

echo "  You need to choose ONE of these registries:"
echo ""
echo "  Option 1: Docker Hub"
echo "    Registry: docker.io/YOUR_USERNAME"
echo "    Test: docker login"
echo ""
echo "  Option 2: GitHub Container Registry (Recommended)"
echo "    Registry: ghcr.io/jnzm02"
echo "    Requires: GitHub Personal Access Token with write:packages scope"
echo "    Test: echo \$TOKEN | docker login ghcr.io -u jnzm02 --password-stdin"
echo ""
echo "  Option 3: DigitalOcean Container Registry"
echo "    Registry: registry.digitalocean.com/YOUR_REGISTRY"
echo "    Test: doctl registry login"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 6. Check GitHub Secrets (manual verification)
# ─────────────────────────────────────────────────────────────────────────────
log_step "6. GitHub Secrets Checklist..."

echo ""
echo "  Verify these 7 secrets are configured at:"
echo "  https://github.com/jnzm02/aether-guard/settings/secrets/actions"
echo ""
echo "  Required secrets:"
echo "    [ ] SSH_PRIVATE_KEY          (your SSH private key)"
echo "    [ ] SERVER_HOST              (your server IP)"
echo "    [ ] SERVER_USER              (root or deploy)"
echo "    [ ] ANTHROPIC_API_KEY        (sk-ant-...)"
echo "    [ ] DOCKER_REGISTRY          (registry URL)"
echo "    [ ] DOCKER_REGISTRY_USERNAME (registry username)"
echo "    [ ] DOCKER_REGISTRY_PASSWORD (registry password/token)"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "   Verification Complete"
echo "════════════════════════════════════════════════════════════"
echo ""
log_info "SSH connection works ✓"
log_info "Server has Docker & Docker Compose ✓"
echo ""
log_step "Next Steps:"
echo "  1. Ensure all 7 GitHub Secrets are configured"
echo "  2. Choose and configure your Docker Registry"
echo "  3. Run first deployment:"
echo "     https://github.com/jnzm02/aether-guard/actions/workflows/cd.yml"
echo ""
echo "  See PRODUCTION_SETUP.md for detailed instructions."
echo ""
