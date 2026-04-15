#!/usr/bin/env bash
#
# Aether-Guard Production Deployment Script
# Usage: ./deploy.sh [IMAGE_TAG]
#
# This script:
#   1. Backs up current configuration
#   2. Pulls new Docker images from private registry
#   3. Performs zero-downtime rolling update
#   4. Verifies all services are healthy
#   5. Cleans up old images
#

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEPLOY_DIR="/opt/aether-guard"
IMAGE_TAG="${1:-latest}"
COMPOSE_FILE="${DEPLOY_DIR}/infra/docker-compose.yml"
ENV_FILE="${DEPLOY_DIR}/.env"
BACKUP_DIR="${DEPLOY_DIR}/backups"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ─────────────────────────────────────────────────────────────────────────────
# Helper functions
# ─────────────────────────────────────────────────────────────────────────────

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_prerequisites() {
    log_info "Checking prerequisites..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not installed"
        exit 1
    fi

    if [ ! -f "${ENV_FILE}" ]; then
        log_error "Environment file not found: ${ENV_FILE}"
        exit 1
    fi

    if [ ! -f "${COMPOSE_FILE}" ]; then
        log_error "Docker Compose file not found: ${COMPOSE_FILE}"
        exit 1
    fi

    log_info "✅ Prerequisites check passed"
}

backup_current_state() {
    log_info "Backing up current configuration..."

    mkdir -p "${BACKUP_DIR}"

    # Backup .env file
    if [ -f "${ENV_FILE}" ]; then
        cp "${ENV_FILE}" "${BACKUP_DIR}/.env.${TIMESTAMP}"
        cp "${ENV_FILE}" "${DEPLOY_DIR}/.env.backup"
        log_info "✅ Backed up .env to ${BACKUP_DIR}/.env.${TIMESTAMP}"
    fi

    # Backup docker-compose.yml
    if [ -f "${COMPOSE_FILE}" ]; then
        cp "${COMPOSE_FILE}" "${BACKUP_DIR}/docker-compose.${TIMESTAMP}.yml"
        log_info "✅ Backed up docker-compose.yml"
    fi

    # Export current container states
    docker ps -a --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" > "${BACKUP_DIR}/containers.${TIMESTAMP}.txt"

    log_info "✅ Backup complete"
}

pull_images() {
    log_info "Pulling Docker images with tag: ${IMAGE_TAG}..."

    # Source environment variables for registry credentials
    set -a
    source "${ENV_FILE}"
    set +a

    cd "${DEPLOY_DIR}/infra"

    # Update IMAGE_TAG in .env if needed
    if grep -q "^IMAGE_TAG=" "${ENV_FILE}"; then
        sed -i "s/^IMAGE_TAG=.*/IMAGE_TAG=${IMAGE_TAG}/" "${ENV_FILE}"
    else
        echo "IMAGE_TAG=${IMAGE_TAG}" >> "${ENV_FILE}"
    fi

    # Pull images
    docker compose pull --quiet

    log_info "✅ Images pulled successfully"
}

perform_rolling_update() {
    log_info "Performing rolling update..."

    cd "${DEPLOY_DIR}/infra"

    # Use production override if it exists
    COMPOSE_CMD="docker compose -f docker-compose.yml"
    if [ -f "docker-compose.prod.yml" ]; then
        COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"
        log_info "Using production override configuration"
    fi

    # Strategy: Update one service at a time to minimize downtime
    SERVICES=("target-service" "listener" "agent")

    for service in "${SERVICES[@]}"; do
        log_info "Updating ${service}..."

        # Recreate the service with new image
        ${COMPOSE_CMD} up -d --no-deps --force-recreate "${service}"

        # Wait for service to be healthy
        log_info "Waiting for ${service} to be healthy..."
        sleep 10

        # Check if service is running
        if ! ${COMPOSE_CMD} ps "${service}" | grep -q "Up"; then
            log_error "${service} failed to start"
            return 1
        fi

        log_info "✅ ${service} updated successfully"
    done

    # Update observability stack (Prometheus, Alertmanager, Grafana)
    log_info "Updating observability stack..."
    ${COMPOSE_CMD} up -d --force-recreate prometheus alertmanager grafana

    log_info "✅ Rolling update complete"
}

verify_deployment() {
    log_info "Verifying deployment..."

    cd "${DEPLOY_DIR}/infra"

    # Wait for all services to stabilize
    sleep 20

    # Check container status
    log_info "Container status:"
    docker compose ps

    # Health check endpoints
    HEALTH_ENDPOINTS=(
        "http://localhost:8080/health|target-service"
        "http://localhost:8081/health|listener"
        "http://localhost:8082/health|agent"
        "http://localhost:9090/-/healthy|prometheus"
        "http://localhost:9093/-/healthy|alertmanager"
    )

    all_healthy=true
    for endpoint_info in "${HEALTH_ENDPOINTS[@]}"; do
        IFS='|' read -r endpoint name <<< "$endpoint_info"

        log_info "Checking ${name} health: ${endpoint}"

        if curl -sf "${endpoint}" > /dev/null; then
            log_info "✅ ${name} is healthy"
        else
            log_error "❌ ${name} health check failed"
            all_healthy=false
        fi
    done

    if [ "$all_healthy" = false ]; then
        log_error "Health checks failed. Check logs with: docker compose logs"
        return 1
    fi

    log_info "✅ All services are healthy"
}

cleanup_old_images() {
    log_info "Cleaning up old Docker images..."

    # Remove dangling images
    docker image prune -f

    # Keep only last 3 versions of each image
    for image in "aether-guard/target-service" "aether-guard/listener" "aether-guard/agent"; do
        if [ -n "${DOCKER_REGISTRY:-}" ]; then
            full_image="${DOCKER_REGISTRY}/${image}"
        else
            full_image="${image}"
        fi

        # Get list of image IDs, skip first 3 (most recent)
        old_images=$(docker images "${full_image}" --format "{{.ID}}" | tail -n +4)

        if [ -n "${old_images}" ]; then
            echo "${old_images}" | xargs -r docker rmi -f || true
            log_info "✅ Cleaned up old images for ${image}"
        fi
    done

    log_info "✅ Cleanup complete"
}

show_deployment_summary() {
    log_info "═══════════════════════════════════════════════════════"
    log_info "Deployment Summary"
    log_info "═══════════════════════════════════════════════════════"
    log_info "Image Tag:      ${IMAGE_TAG}"
    log_info "Timestamp:      ${TIMESTAMP}"
    log_info "Deploy Dir:     ${DEPLOY_DIR}"
    log_info "Backup Dir:     ${BACKUP_DIR}"
    log_info ""
    log_info "Running containers:"

    cd "${DEPLOY_DIR}/infra"
    docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

    log_info ""
    log_info "═══════════════════════════════════════════════════════"
    log_info "✅ Deployment completed successfully!"
    log_info "═══════════════════════════════════════════════════════"
}

rollback() {
    log_error "Deployment failed. Rolling back to previous state..."

    cd "${DEPLOY_DIR}/infra"

    # Restore backup .env
    if [ -f "${DEPLOY_DIR}/.env.backup" ]; then
        cp "${DEPLOY_DIR}/.env.backup" "${ENV_FILE}"
        log_info "✅ Restored previous .env"
    fi

    # Restart services with previous configuration
    docker compose down
    docker compose up -d

    log_info "✅ Rollback complete. Previous state restored."
    exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# Main deployment flow
# ─────────────────────────────────────────────────────────────────────────────

main() {
    log_info "Starting Aether-Guard deployment (tag: ${IMAGE_TAG})..."

    # Set trap for automatic rollback on error
    trap rollback ERR

    check_prerequisites
    backup_current_state
    pull_images
    perform_rolling_update
    verify_deployment
    cleanup_old_images
    show_deployment_summary

    # Disable rollback trap on success
    trap - ERR
}

# Execute main function
main "$@"
