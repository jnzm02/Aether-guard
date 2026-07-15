#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Prometheus Docker Entrypoint — Renders SLO alert templates with envsubst
# ─────────────────────────────────────────────────────────────────────────────
set -e

# Default MONITORED_JOB if not provided (backward compatibility)
export MONITORED_JOB="${MONITORED_JOB:-target-service}"

echo "[entrypoint] Rendering Prometheus alert rule templates..."
echo "[entrypoint] MONITORED_JOB=${MONITORED_JOB}"

# Render slo_alerts.yml from template
if [ -f /etc/prometheus/rules/slo_alerts.yml.template ]; then
    # CRITICAL: Only substitute ${MONITORED_JOB}, leave Prometheus template vars ({{ $value }}) untouched
    envsubst '${MONITORED_JOB}' < /etc/prometheus/rules/slo_alerts.yml.template > /etc/prometheus/rules/slo_alerts.yml
    echo "[entrypoint] ✓ Rendered /etc/prometheus/rules/slo_alerts.yml"
else
    echo "[entrypoint] ⚠ Template file not found: /etc/prometheus/rules/slo_alerts.yml.template"
    echo "[entrypoint]   Using existing slo_alerts.yml (if present)"
fi

# Validate rendered rules with promtool (if available)
if command -v promtool >/dev/null 2>&1; then
    echo "[entrypoint] Validating rendered Prometheus rules..."
    if promtool check rules /etc/prometheus/rules/slo_alerts.yml; then
        echo "[entrypoint] ✓ Alert rules validation passed"
    else
        echo "[entrypoint] ✗ Alert rules validation FAILED — Prometheus may not start correctly"
        exit 1
    fi
else
    echo "[entrypoint] ⚠ promtool not found — skipping validation"
fi

echo "[entrypoint] Starting Prometheus..."

# Execute Prometheus with all provided arguments
exec /bin/prometheus "$@"
