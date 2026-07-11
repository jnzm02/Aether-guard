# ─────────────────────────────────────────────────────────────────────────────
# Aether-Guard — Terraform Variables (DigitalOcean DOKS + Managed Postgres)
# ─────────────────────────────────────────────────────────────────────────────

variable "do_token" {
  description = "DigitalOcean API token (keep this secret!)"
  type        = string
  sensitive   = true
}

variable "region" {
  description = "DigitalOcean region for cluster and database"
  type        = string
  default     = "nyc3"
}

variable "cluster_name" {
  description = "Name for the Kubernetes cluster"
  type        = string
  default     = "aether-guard-demo"
}

variable "node_size" {
  description = "Droplet size for K8s nodes (smallest: s-1vcpu-1gb = $12/month)"
  type        = string
  default     = "s-1vcpu-2gb"  # $18/month - need 2GB for agent + target-service
}

variable "node_count" {
  description = "Number of nodes in the cluster (1 for cost-minimized demo)"
  type        = number
  default     = 1
}

variable "db_size" {
  description = "Managed Postgres size (db-s-1vcpu-1gb = $15/month)"
  type        = string
  default     = "db-s-1vcpu-1gb"
}

variable "anthropic_api_key" {
  description = "Anthropic API key for the agent (injected into K8s Secret)"
  type        = string
  sensitive   = true
}
