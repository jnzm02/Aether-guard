# ─────────────────────────────────────────────────────────────────────────────
# Aether-Guard — Terraform Outputs
# ─────────────────────────────────────────────────────────────────────────────

output "cluster_id" {
  description = "Kubernetes cluster ID"
  value       = digitalocean_kubernetes_cluster.aether_guard.id
}

output "cluster_endpoint" {
  description = "Kubernetes cluster API endpoint"
  value       = digitalocean_kubernetes_cluster.aether_guard.endpoint
}

output "cluster_status" {
  description = "Kubernetes cluster status"
  value       = digitalocean_kubernetes_cluster.aether_guard.status
}

output "kubeconfig_command" {
  description = "Command to download kubeconfig"
  value       = "doctl kubernetes cluster kubeconfig save ${digitalocean_kubernetes_cluster.aether_guard.id}"
}

output "postgres_host" {
  description = "PostgreSQL private host (for K8s Secret)"
  value       = digitalocean_database_cluster.postgres.private_host
  sensitive   = true
}

output "postgres_port" {
  description = "PostgreSQL port"
  value       = digitalocean_database_cluster.postgres.port
}

output "postgres_database" {
  description = "PostgreSQL database name"
  value       = digitalocean_database_db.aether_guard_db.name
}

output "postgres_user" {
  description = "PostgreSQL username"
  value       = digitalocean_database_user.aether_guard_user.name
  sensitive   = true
}

output "postgres_password" {
  description = "PostgreSQL password (auto-generated)"
  value       = digitalocean_database_user.aether_guard_user.password
  sensitive   = true
}

output "postgres_connection_string" {
  description = "Full PostgreSQL connection string for K8s Secret"
  value       = "postgresql://${digitalocean_database_user.aether_guard_user.name}:${digitalocean_database_user.aether_guard_user.password}@${digitalocean_database_cluster.postgres.private_host}:${digitalocean_database_cluster.postgres.port}/${digitalocean_database_db.aether_guard_db.name}?sslmode=require"
  sensitive   = true
}

output "estimated_monthly_cost" {
  description = "Estimated monthly cost breakdown (destroy after demo!)"
  value = <<-EOT
    ─────────────────────────────────────────────────────
    ESTIMATED MONTHLY COST (DigitalOcean, as of 2024):
    ─────────────────────────────────────────────────────
    1 × ${var.node_size} Droplet:       ~$18/month
    Managed Postgres (${var.db_size}):  ~$15/month
    Load Balancer:                       ~$12/month
    ─────────────────────────────────────────────────────
    TOTAL:                               ~$45/month

    ⚠️  For ~1 hour demo runtime:        ~$0.06

    Remember to run 'terraform destroy' after capturing
    screenshots/demo to avoid ongoing charges!
    ─────────────────────────────────────────────────────
  EOT
}
