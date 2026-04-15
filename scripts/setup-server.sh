#!/usr/bin/env bash
#
# Aether-Guard Server Setup Script
# One-time setup for DigitalOcean Droplet or any Ubuntu VPS
#
# Usage: curl -fsSL https://raw.githubusercontent.com/YOUR_REPO/main/scripts/setup-server.sh | bash
#   or:  ./setup-server.sh
#

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root or with sudo"
    exit 1
fi

log_info "Starting Aether-Guard server setup..."

# ─────────────────────────────────────────────────────────────────────────────
# 1. Update system packages
# ─────────────────────────────────────────────────────────────────────────────
log_info "Updating system packages..."
apt-get update
apt-get upgrade -y
apt-get install -y curl wget git jq vim ufw logrotate

# ─────────────────────────────────────────────────────────────────────────────
# 2. Install Docker
# ─────────────────────────────────────────────────────────────────────────────
if command -v docker &> /dev/null; then
    log_warn "Docker already installed: $(docker --version)"
else
    log_info "Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sh get-docker.sh
    rm get-docker.sh
    log_info "Docker installed: $(docker --version)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. Install Docker Compose V2
# ─────────────────────────────────────────────────────────────────────────────
if docker compose version &> /dev/null; then
    log_warn "Docker Compose already installed: $(docker compose version)"
else
    log_info "Installing Docker Compose plugin..."
    apt-get install -y docker-compose-plugin
    log_info "Docker Compose installed: $(docker compose version)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. Create deployment user (optional)
# ─────────────────────────────────────────────────────────────────────────────
DEPLOY_USER="${DEPLOY_USER:-deploy}"

if id "$DEPLOY_USER" &>/dev/null; then
    log_warn "User '$DEPLOY_USER' already exists"
else
    log_info "Creating deployment user: $DEPLOY_USER"
    useradd -m -s /bin/bash "$DEPLOY_USER"
    usermod -aG docker "$DEPLOY_USER"
    mkdir -p "/home/$DEPLOY_USER/.ssh"
    chmod 700 "/home/$DEPLOY_USER/.ssh"
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
    log_info "User '$DEPLOY_USER' created and added to docker group"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. Create application directory
# ─────────────────────────────────────────────────────────────────────────────
DEPLOY_DIR="/opt/aether-guard"

log_info "Creating application directory: $DEPLOY_DIR"
mkdir -p "$DEPLOY_DIR"/{infra,scripts,backups,data/{prometheus,agent,grafana}}

if [ "$DEPLOY_USER" != "root" ]; then
    chown -R "$DEPLOY_USER:$DEPLOY_USER" "$DEPLOY_DIR"
fi

log_info "Application directory created"

# ─────────────────────────────────────────────────────────────────────────────
# 6. Configure firewall
# ─────────────────────────────────────────────────────────────────────────────
log_info "Configuring UFW firewall..."

# Allow SSH, HTTP, HTTPS
ufw --force allow 22/tcp
ufw --force allow 80/tcp
ufw --force allow 443/tcp

# Optional: Allow application ports for debugging (comment out in production)
ufw --force allow 8080:8082/tcp comment 'Aether-Guard services'
ufw --force allow 9090:9093/tcp comment 'Prometheus/Alertmanager'
ufw --force allow 3001/tcp comment 'Grafana'

# Enable firewall
ufw --force enable

log_info "Firewall configured"
ufw status

# ─────────────────────────────────────────────────────────────────────────────
# 7. Configure Docker log rotation
# ─────────────────────────────────────────────────────────────────────────────
log_info "Configuring Docker daemon for log rotation..."

cat > /etc/docker/daemon.json <<EOF
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF

systemctl restart docker
log_info "Docker log rotation configured"

# ─────────────────────────────────────────────────────────────────────────────
# 8. Setup SSH key for GitHub Actions (manual step)
# ─────────────────────────────────────────────────────────────────────────────
log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log_info "Next steps:"
log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
log_info "1. Add your GitHub Actions SSH public key to /home/$DEPLOY_USER/.ssh/authorized_keys"
echo "   Example:"
echo "   echo 'ssh-ed25519 AAAA...' >> /home/$DEPLOY_USER/.ssh/authorized_keys"
echo "   chmod 600 /home/$DEPLOY_USER/.ssh/authorized_keys"
echo "   chown $DEPLOY_USER:$DEPLOY_USER /home/$DEPLOY_USER/.ssh/authorized_keys"
echo ""
log_info "2. Test SSH connection from your local machine:"
echo "   ssh $DEPLOY_USER@\$(curl -s ifconfig.me)"
echo ""
log_info "3. Configure GitHub Secrets in your repository:"
echo "   - SSH_PRIVATE_KEY"
echo "   - SERVER_HOST (this server's IP: $(curl -s ifconfig.me))"
echo "   - SERVER_USER ($DEPLOY_USER)"
echo "   - ANTHROPIC_API_KEY"
echo "   - DOCKER_REGISTRY"
echo "   - DOCKER_REGISTRY_USERNAME"
echo "   - DOCKER_REGISTRY_PASSWORD"
echo ""
log_info "4. Run your first deployment via GitHub Actions!"
echo ""
log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log_info "✅ Server setup complete!"
log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
