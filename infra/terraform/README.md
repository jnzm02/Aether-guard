# Aether-Guard — Kubernetes Deployment (DigitalOcean)

This directory contains Terraform configuration to deploy Aether-Guard's core services to a managed Kubernetes cluster on DigitalOcean.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  DigitalOcean Kubernetes (DOKS)                                 │
│  ┌────────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │  target-service    │  │      agent      │  │    Redis     │ │
│  │  (ClusterIP)       │  │  (LoadBalancer) │  │  (ClusterIP) │ │
│  └────────────────────┘  └─────────────────┘  └──────────────┘ │
│                                  │                               │
│                                  ▼                               │
│                          External IP (LB)                        │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
┌──────────────────────────────────┴──────────────────────────────┐
│  Managed PostgreSQL (DigitalOcean DBaaS)                        │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  aether_guard database (incident reports)                  │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Local (docker-compose)                                         │
│  ┌──────────────┐  ┌────────────┐  ┌───────┐  ┌──────────────┐ │
│  │  Prometheus  │  │  Grafana   │  │ Tempo │  │   listener   │ │
│  │  (metrics)   │  │ (dashboards)│  │(traces)│  │  (webhook)  │ │
│  └──────────────┘  └────────────┘  └───────┘  └──────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼ (webhook to agent LB external IP)
```

**What runs in Kubernetes:**
- `target-service`: The intentionally-breakable Go microservice
- `agent`: AI SRE agent (Claude-powered RCA)
- `Redis`: Incident state cache

**What stays local:**
- `Prometheus`, `Grafana`, `Tempo`: Observability stack (docker-compose)
- `listener`: Alert enrichment service (reaches agent via LoadBalancer IP)

## Cost Estimate

```
Component                         Cost/month    Note
─────────────────────────────────────────────────────────────────
1 × s-1vcpu-2gb Droplet (node)    ~$18         Single-node demo
Managed Postgres (db-s-1vcpu-1gb) ~$15         Basic tier
Load Balancer                     ~$12         For agent service
─────────────────────────────────────────────────────────────────
TOTAL                             ~$45/month

For ~1 hour demo runtime:         ~$0.06
```

**⚠️ IMPORTANT:** Run `terraform destroy` immediately after capturing screenshots/demo to avoid ongoing charges!

## Prerequisites

1. **DigitalOcean account** with API access
2. **doctl CLI** installed and configured:
   ```bash
   brew install doctl
   doctl auth init
   ```
3. **Terraform** installed (≥1.0):
   ```bash
   brew install terraform
   ```
4. **kubectl** installed:
   ```bash
   brew install kubectl
   ```
5. **Docker images built locally**:
   ```bash
   cd ../../
   docker compose -f infra/docker-compose.yml build target-service agent
   ```

## Deployment Steps

### 1. Configure Terraform variables

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` and fill in:
- `do_token`: Your DigitalOcean API token (from https://cloud.digitalocean.com/account/api/tokens)
- `anthropic_api_key`: Your Anthropic API key for the agent

### 2. Initialize Terraform

```bash
terraform init
```

### 3. Review the plan

```bash
terraform plan
```

**Expected resources:**
- `digitalocean_kubernetes_cluster.aether_guard` (1-node cluster)
- `digitalocean_database_cluster.postgres` (managed Postgres)
- `digitalocean_database_db.aether_guard_db` (database)
- `digitalocean_database_user.aether_guard_user` (user + auto-generated password)
- `digitalocean_firewall.cluster_firewall` (allow LB traffic)

### 4. Apply (provision cluster + database)

```bash
terraform apply
```

**Wait ~5-8 minutes** for cluster + database to provision.

After success, note the outputs:
```bash
terraform output estimated_monthly_cost  # Confirm cost
terraform output kubeconfig_command      # Command to download kubeconfig
```

### 5. Download kubeconfig

```bash
doctl kubernetes cluster kubeconfig save $(terraform output -raw cluster_id)
```

Verify connectivity:
```bash
kubectl get nodes
```

### 6. Create Kubernetes secrets

```bash
kubectl create secret generic aether-guard-secrets \
  --namespace=aether-guard \
  --from-literal=anthropic-api-key="$(grep anthropic_api_key terraform.tfvars | cut -d'=' -f2 | tr -d ' "')" \
  --from-literal=postgres-url="$(terraform output -raw postgres_connection_string)" \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 7. Deploy services to Kubernetes

```bash
cd ../k8s
kubectl apply -k .
```

**Wait ~2-3 minutes** for pods to start and LoadBalancer IP to be assigned.

### 8. Verify deployment

```bash
# Check pods
kubectl get pods -n aether-guard

# Get LoadBalancer IP
kubectl get svc -n aether-guard agent

# Test agent health endpoint
export AGENT_IP=$(kubectl get svc -n aether-guard agent -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
curl http://$AGENT_IP/health
```

**Expected response:**
```json
{
  "status": "ok",
  "service": "aether-guard/agent",
  "version": "2.0.0",
  ...
}
```

### 9. (Optional) Update local listener webhook

If you want the local listener to send alerts to the K8s agent:

```bash
# In docker-compose.yml, update agent service environment:
AGENT_URL=http://<AGENT_IP>  # Use IP from step 8
```

## Teardown

**IMPORTANT:** Run this after capturing screenshots/demo to avoid charges!

```bash
cd infra/terraform
terraform destroy
```

Confirm with `yes` when prompted.

**Verify cleanup:**
- Check DigitalOcean console: no Kubernetes cluster or database
- Check LoadBalancer is deleted
- Total resources: 0

## Troubleshooting

### Pods stuck in Pending
```bash
kubectl describe pod -n aether-guard <pod-name>
```

Common causes:
- Insufficient node resources (check `kubectl top nodes`)
- Image pull errors (images must be built locally, not from registry)

### LoadBalancer stuck in Pending
```bash
kubectl describe svc -n aether-guard agent
```

Wait ~2-3 minutes for DO to provision the load balancer.

### Database connection errors
```bash
# Verify secret was created correctly
kubectl get secret -n aether-guard aether-guard-secrets -o yaml

# Check Postgres is reachable from cluster
kubectl run -it --rm debug --image=postgres:16-alpine --restart=Never -- \
  psql "$(terraform output -raw postgres_connection_string)"
```

## Files

- `main.tf`: Cluster, database, firewall resources
- `variables.tf`: Input variables (token, region, sizes)
- `outputs.tf`: Cluster endpoint, DB connection, cost estimate
- `terraform.tfvars.example`: Template (copy to `terraform.tfvars`)
- `../k8s/`: Kubernetes manifests (deployments, services)
