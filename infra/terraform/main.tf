# ─────────────────────────────────────────────────────────────────────────────
# Aether-Guard — Terraform Main Configuration
# ─────────────────────────────────────────────────────────────────────────────
# Deploys:
#   - DigitalOcean Kubernetes (DOKS) cluster (1-node for demo)
#   - Managed PostgreSQL database (basic tier)
#   - Firewall rules for load balancer access
#
# Cost: ~$39/month ($18 node + $15 DB + $12 LB) - destroy after demo to pay $0
# ─────────────────────────────────────────────────────────────────────────────

terraform {
  required_version = ">= 1.0"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.34"
    }
  }
}

provider "digitalocean" {
  token = var.do_token
}

# ─────────────────────────────────────────────────────────────────────────────
# Kubernetes Cluster
# ─────────────────────────────────────────────────────────────────────────────

resource "digitalocean_kubernetes_cluster" "aether_guard" {
  name    = var.cluster_name
  region  = var.region
  version = "1.29.1-do.0"  # Latest stable K8s version on DOKS

  node_pool {
    name       = "worker-pool"
    size       = var.node_size
    node_count = var.node_count
    auto_scale = false  # Fixed size for demo
  }

  tags = ["aether-guard", "demo", "portfolio"]
}

# ─────────────────────────────────────────────────────────────────────────────
# Managed PostgreSQL Database
# ─────────────────────────────────────────────────────────────────────────────

resource "digitalocean_database_cluster" "postgres" {
  name       = "${var.cluster_name}-postgres"
  engine     = "pg"
  version    = "16"
  size       = var.db_size
  region     = var.region
  node_count = 1  # Single node for demo

  tags = ["aether-guard", "demo", "portfolio"]
}

resource "digitalocean_database_db" "aether_guard_db" {
  cluster_id = digitalocean_database_cluster.postgres.id
  name       = "aether_guard"
}

resource "digitalocean_database_user" "aether_guard_user" {
  cluster_id = digitalocean_database_cluster.postgres.id
  name       = "aether_guard"
}

# ─────────────────────────────────────────────────────────────────────────────
# Firewall (Managed by DOKS automatically - no custom rules needed)
# ─────────────────────────────────────────────────────────────────────────────
# DigitalOcean Kubernetes automatically configures:
#   - Node-to-node communication
#   - LoadBalancer ingress rules
#   - Database private network access
