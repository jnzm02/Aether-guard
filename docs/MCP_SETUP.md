# MCP Setup (Claude Code)

`/.mcp.json` (repo root) declares the **Model Context Protocol** servers that connect
Claude Code to Aether-Guard's live systems — GitHub, the incident Postgres DB, and
Grafana. This lets Claude read real Actions logs, query past incidents, and inspect
dashboards/metrics instead of only reading source.

`.mcp.json` is **project-scoped and checked in**, so all secrets are referenced via
`${ENV_VAR}` expansion — **nothing sensitive is committed**. You supply the values
through your environment.

## 1. Provide the environment values

Claude Code reads these from the shell environment it launches in (it does **not**
auto-load `.env`). Export them in your shell profile, via `direnv`, or source them
before launching Claude:

```bash
set -a && source .env.mcp && set +a && claude    # one option
```

| Variable | Used by | Notes |
|----------|---------|-------|
| `GITHUB_TOKEN` | github | Reuses the repo's existing PAT (`ghp_…`). Needs `repo` + `actions:read` scope to read runs/logs. |
| `AETHER_MCP_DB_URL` | postgres | Host-side connection string. Defaults to `postgresql://aether_guard:local_dev_password@localhost:5432/aether_guard`. Note: the app's `POSTGRES_URL` points at the Docker-internal host `postgres:5432`, which won't resolve from your machine — use `localhost` here. |
| `GRAFANA_URL` | grafana | Defaults to `http://host.docker.internal:3001` (the Grafana MCP runs in a container, so `localhost` would mean the container itself; `host.docker.internal` reaches Grafana on your host). |
| `GRAFANA_API_KEY` | grafana | Create a Grafana service-account token (Viewer role is enough for read-only). |

## 2. Prerequisites per server

- **github** — remote HTTP server, no local runtime needed. Just the token.
- **postgres** — needs Node (`npx`) and the incident Postgres reachable (`make docker-up`
  starts it). Uses the official `@modelcontextprotocol/server-postgres`.
- **grafana** — needs Docker running (you already use it for the stack) and pulls
  `mcp/grafana` (Grafana Labs' official server). Grafana can proxy Prometheus queries
  through its datasource, so this also covers metrics.

## 3. Approve and verify

On next launch, Claude Code will ask you to approve the project's MCP servers (this is
the security gate for a checked-in `.mcp.json`). After approving:

```
/mcp        # list servers and their connection status
```

A server whose env vars are unset (or whose backend isn't running) will simply show as
failed — it won't break anything else.

## 4. Optional servers to add

Kept out of `.mcp.json` so startup stays clean — paste into `mcpServers` and **verify
the package/image tag** before relying on them.

**Prometheus (direct, in addition to via Grafana):**
```jsonc
"prometheus": {
  "command": "docker",
  "args": ["run", "--rm", "-i", "-e", "PROMETHEUS_URL", "ghcr.io/pab1it0/prometheus-mcp-server:latest"],
  "env": { "PROMETHEUS_URL": "${PROMETHEUS_URL:-http://host.docker.internal:9090}" }
}
```

**Kubernetes (for the `k8s/` workloads):**
```jsonc
"kubernetes": {
  "command": "npx",
  "args": ["-y", "kubernetes-mcp-server@latest"]
}
```

## Security notes

- Secrets live only in your environment, never in `.mcp.json`.
- Prefer **read-only** credentials (Grafana Viewer token; a DB role with `SELECT`
  only; a fine-grained PAT). Claude should be *reading* incident data, not mutating it.
- `.env` and `.env.mcp` remain gitignored — do not commit real values.
