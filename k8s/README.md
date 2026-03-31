# Aether-Guard — Kubernetes Manifests

Production-grade Kubernetes deployment for all 6 Aether-Guard components.

## Prerequisites

- `kubectl` ≥ 1.28
- A running cluster: [minikube](https://minikube.sigs.k8s.io/) or [kind](https://kind.sigs.k8s.io/)
- Images built locally (or pushed to a registry — see [Image Registry](#image-registry))

## Quick Start (minikube)

```bash
# 1. Start cluster
minikube start --memory=4096 --cpus=4

# 2. Build images inside minikube's Docker daemon
eval $(minikube docker-env)
docker build -t aether-guard/target-service:latest services/target-service
docker build -t aether-guard/listener:latest        services/listener
docker build -t aether-guard/agent:latest           services/agent

# 3. Create the API key secret (never commit the real key)
kubectl create secret generic agent-secrets \
  --namespace aether-guard \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE

# 4. Deploy everything
kubectl apply -k k8s/

# 5. Wait for all pods to be ready
kubectl rollout status deployment/target-service -n aether-guard
kubectl rollout status deployment/prometheus      -n aether-guard
kubectl rollout status deployment/agent           -n aether-guard

# 6. Open services
minikube service prometheus  -n aether-guard  # :30090
minikube service grafana     -n aether-guard  # :30300
minikube service agent       -n aether-guard  # :30082
```

## Architecture

```
  ┌──────────────────────────────────────────── namespace: aether-guard ──┐
  │                                                                        │
  │  target-service (×2) ──/metrics──► prometheus ──alerts──► alertmanager│
  │       ▲  HPA(2-10)                      │                      │      │
  │       │  RollingUpdate                  │                      ▼      │
  │       │                            grafana             listener (×2)  │
  │       │                                                      │        │
  │       └──── RESTART/SCALE/ROLLBACK ◄── agent ◄── analyses ──┘        │
  │                                    (Claude API)                        │
  └────────────────────────────────────────────────────────────────────────┘
```

## Files

| File | Contents |
|------|----------|
| `namespace.yaml` | `aether-guard` namespace |
| `target-service.yaml` | Deployment (2 replicas) + ClusterIP Service + HPA (2–10 pods) |
| `prometheus.yaml` | RBAC + ConfigMap (config + SLO rules) + PVC (5 Gi) + Deployment + NodePort |
| `alertmanager.yaml` | ConfigMap + Deployment + NodePort |
| `listener.yaml` | Deployment (2 replicas) + ClusterIP |
| `agent.yaml` | Secret template + PVC (1 Gi) + Deployment + NodePort |
| `grafana.yaml` | ConfigMap (provisioning) + PVC (1 Gi) + Deployment + NodePort |
| `kustomization.yaml` | Kustomize root — applies all resources |

## NodePort Mapping

| Service | NodePort | Purpose |
|---------|----------|---------|
| Prometheus | 30090 | PromQL, targets, alerts |
| Alertmanager | 30093 | Firing alerts |
| Agent | 30082 | RCA analyses, post-mortems |
| Grafana | 30300 | SLO dashboard |

## Useful Commands

```bash
# Watch all pods
kubectl get pods -n aether-guard -w

# Check HPA status
kubectl get hpa -n aether-guard

# Tail agent logs (AI decisions)
kubectl logs -n aether-guard -l app=agent -f

# Inject chaos from outside the cluster
TARGET=$(minikube service target-service -n aether-guard --url)
curl -X POST "$TARGET/chaos/error?rate=0.5"
curl -X POST "$TARGET/chaos/latency?ms=400"
curl -X POST "$TARGET/chaos/reset"

# View agent RCA decisions
AGENT=$(minikube service agent -n aether-guard --url)
curl -s "$AGENT/analyses" | jq '.[-3:] | .[] | {alertname, action, confidence}'

# Force Prometheus config reload (after ConfigMap update)
curl -X POST http://$(minikube ip):30090/-/reload
```

## Secret Management

The `agent.yaml` file contains a **placeholder** secret with `REPLACE_ME`.  
**Do not commit real API keys.** For production use:

```bash
# Option A — kubectl create (safest for demos)
kubectl create secret generic agent-secrets \
  -n aether-guard \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-... \
  --dry-run=client -o yaml | kubectl apply -f -

# Option B — Sealed Secrets (production)
kubeseal --format yaml < secret.yaml > sealed-secret.yaml

# Option C — External Secrets Operator (enterprise)
# Mount from Vault / AWS Secrets Manager / GCP Secret Manager
```

## Image Registry

For a real cluster (not minikube), push images to a registry first:

```bash
# Example: GitHub Container Registry
docker tag aether-guard/target-service:latest ghcr.io/YOUR_ORG/target-service:latest
docker push ghcr.io/YOUR_ORG/target-service:latest

# Then update kustomization.yaml:
# kustomize edit set image aether-guard/target-service=ghcr.io/YOUR_ORG/target-service:latest
```

## Tear Down

```bash
kubectl delete namespace aether-guard
# PVCs are not deleted automatically — remove manually if needed:
kubectl get pvc -n aether-guard
```
