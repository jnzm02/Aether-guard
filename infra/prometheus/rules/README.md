# Prometheus Alert Rules — Template System

This directory contains Aether-Guard's SLO alert rules, which are parameterized to work with any Prometheus-instrumented service.

## Files

| File | Purpose |
|------|---------|
| `slo_alerts.yml.template` | **Source template** with `${MONITORED_JOB}` placeholders |
| `slo_alerts.yml` | **Rendered output** generated at Prometheus container startup (DO NOT EDIT) |
| `incident_alerts.yml` | Incident report generation rules (static, no template) |

## How It Works

1. **At container startup**, the Prometheus entrypoint script (`/docker-entrypoint.sh`) runs:
   ```bash
   envsubst '${MONITORED_JOB}' < slo_alerts.yml.template > slo_alerts.yml
   ```

2. **Prometheus loads** the rendered `slo_alerts.yml` file with all `${MONITORED_JOB}` placeholders replaced with the actual service name (e.g., `target-service`, `my-api-service`, etc.)

3. **Validation**: The entrypoint script runs `promtool check rules` to ensure the rendered file is valid before starting Prometheus.

## Configuration

Set the `MONITORED_JOB` environment variable to match the `job_name` in your Prometheus scrape config:

```yaml
# infra/docker-compose.yml
services:
  prometheus:
    environment:
      - MONITORED_JOB=my-api-service  # Must match scrape_configs.job_name
```

Or in Kubernetes:

```yaml
# k8s/prometheus-deployment.yaml
env:
- name: MONITORED_JOB
  value: "my-api-service"
```

## Example Transformation

**Template** (`slo_alerts.yml.template`):
```yaml
- alert: TargetServiceDown
  expr: up{job="${MONITORED_JOB}"} == 0
  labels:
    service: ${MONITORED_JOB}
  annotations:
    summary: "CRITICAL: ${MONITORED_JOB} is DOWN"
```

**Rendered** (`slo_alerts.yml` with `MONITORED_JOB=my-api-service`):
```yaml
- alert: TargetServiceDown
  expr: up{job="my-api-service"} == 0
  labels:
    service: my-api-service
  annotations:
    summary: "CRITICAL: my-api-service is DOWN"
```

## Editing Alert Rules

### ⚠️ IMPORTANT: Always edit the template, not the rendered file

- **DO**: Edit `slo_alerts.yml.template` and restart the Prometheus container
- **DON'T**: Edit `slo_alerts.yml` (it will be overwritten on next container restart)

### Making Changes

1. Edit `slo_alerts.yml.template`:
   ```bash
   vim infra/prometheus/rules/slo_alerts.yml.template
   ```

2. Test rendering locally (optional):
   ```bash
   MONITORED_JOB=target-service envsubst '${MONITORED_JOB}' \
     < slo_alerts.yml.template \
     | promtool check rules /dev/stdin
   ```

3. Restart Prometheus:
   ```bash
   docker compose -f infra/docker-compose.yml restart prometheus
   ```

4. Verify the rendered file:
   ```bash
   docker exec prometheus cat /etc/prometheus/rules/slo_alerts.yml
   ```

## Troubleshooting

### Problem: Alert rules not loading

**Check entrypoint logs:**
```bash
docker logs prometheus 2>&1 | grep entrypoint
```

Expected output:
```
[entrypoint] Rendering Prometheus alert rule templates...
[entrypoint] MONITORED_JOB=target-service
[entrypoint] ✓ Rendered /etc/prometheus/rules/slo_alerts.yml
[entrypoint] Validating rendered Prometheus rules...
[entrypoint] ✓ Alert rules validation passed
[entrypoint] Starting Prometheus...
```

### Problem: Prometheus template variables ({{ $value }}) are empty

**Cause:** `envsubst` is substituting `$value` variables.

**Fix:** Ensure entrypoint uses explicit variable list:
```bash
envsubst '${MONITORED_JOB}' < template > output
#        ^^^^^^^^^^^^^^^^^^^ Only substitute these vars
```

This prevents `envsubst` from replacing Prometheus's `{{ $value }}` template syntax.

### Problem: Alert rules validation fails

**Debug:**
```bash
# Check rendered file syntax
docker exec prometheus promtool check rules /etc/prometheus/rules/slo_alerts.yml

# Or manually render and validate on host
MONITORED_JOB=target-service envsubst '${MONITORED_JOB}' \
  < slo_alerts.yml.template \
  | promtool check rules /dev/stdin
```

## Reference

For more details on using Aether-Guard with your own service, see:
- [docs/BRING_YOUR_OWN_SERVICE.md](../../../docs/BRING_YOUR_OWN_SERVICE.md)
