# Redis Security Hardening Guide

## Overview

This guide documents the security measures implemented to protect the Redis instance used by Aether Guard from unauthorized access.

## Security Issue

**Vulnerability:** Unauthenticated Redis instance exposed to the Internet

The German Federal Office for Information Security (BSI) reported that our Redis server at IP `116.202.19.79` was:
- Accessible from the Internet without authentication
- Running Redis 7.4.9 without SASL/password authentication
- Exposing data to potential unauthorized access, modification, or deletion

## Implemented Security Measures

### 1. Authentication (requirepass)

Redis now requires password authentication for all connections.

**Changes:**
- Added `--requirepass` flag to Redis server command
- Password stored securely in environment variables
- All client connections updated to include authentication

### 2. Network Isolation

**Development Environment:**
- Redis port bound to localhost only (`127.0.0.1:6379:6379`)
- Only accessible from the host machine

**Production Environment:**
- Redis port mapping completely removed
- Only accessible via internal Docker network or Kubernetes ClusterIP
- No external exposure

### 3. Connection String Security

All Redis connection URLs updated to include authentication:
```
redis://:${REDIS_PASSWORD}@redis:6379/0
```

## Deployment Instructions

### Docker Compose Deployment

1. **Generate a secure password:**
   ```bash
   openssl rand -base64 32
   ```

2. **Update your `.env` file:**
   ```bash
   # Add to .env (DO NOT commit this file!)
   REDIS_PASSWORD=your_generated_password_here
   ```

3. **Deploy the stack:**
   ```bash
   # Development
   docker compose -f infra/docker-compose.yml up -d

   # Production
   docker compose -f infra/docker-compose.yml -f infra/docker-compose.prod.yml up -d
   ```

4. **Verify authentication is required:**
   ```bash
   # This should fail with "NOAUTH Authentication required"
   docker exec redis redis-cli ping

   # This should succeed
   docker exec redis redis-cli -a "$REDIS_PASSWORD" ping
   ```

### Kubernetes Deployment

1. **Create the Redis password secret:**
   ```bash
   # Generate and create secret in one command
   kubectl create secret generic redis-password \
     --from-literal=password=$(openssl rand -base64 32) \
     -n aether-guard
   ```

2. **Deploy Redis:**
   ```bash
   kubectl apply -f infra/k8s/redis-deployment.yaml
   ```

3. **Deploy the agent (or update existing deployment):**
   ```bash
   kubectl apply -f infra/k8s/agent-deployment.yaml
   ```

4. **Verify the secret is properly mounted:**
   ```bash
   # Check Redis pod can start
   kubectl get pods -n aether-guard -l app=redis

   # Test authentication
   kubectl exec -n aether-guard deployment/redis -- \
     sh -c 'redis-cli -a "$REDIS_PASSWORD" ping'
   ```

## Verification Steps

### 1. Check Redis is not publicly accessible

From an external machine (or after deployment):
```bash
# This should timeout or be refused (not return "PONG")
nc -zv YOUR_PUBLIC_IP 6379
```

### 2. Verify authentication is enforced

```bash
# Docker Compose
docker exec redis redis-cli ping  # Should fail with NOAUTH
docker exec redis redis-cli -a "$REDIS_PASSWORD" ping  # Should return PONG

# Kubernetes
kubectl exec -n aether-guard deployment/redis -- redis-cli ping  # Should fail
kubectl exec -n aether-guard deployment/redis -- sh -c 'redis-cli -a "$REDIS_PASSWORD" ping'  # Should succeed
```

### 3. Verify application connectivity

Check that the agent service can still connect to Redis:
```bash
# Docker Compose
docker logs agent | grep -i redis

# Kubernetes
kubectl logs -n aether-guard deployment/agent | grep -i redis
```

You should see log messages like:
```
Redis connected: redis://:***@redis:6379/0
```

### 4. Test end-to-end functionality

Trigger an alert and verify the incident is stored in Redis:
```bash
# Docker Compose
docker exec redis redis-cli -a "$REDIS_PASSWORD" KEYS "incident:*"

# Kubernetes
kubectl exec -n aether-guard deployment/redis -- \
  sh -c 'redis-cli -a "$REDIS_PASSWORD" KEYS "incident:*"'
```

## Security Best Practices

1. **Never commit passwords to version control**
   - Use `.env` files (already in `.gitignore`)
   - Use Kubernetes Secrets
   - Consider external secret management (HashiCorp Vault, AWS Secrets Manager, etc.)

2. **Rotate credentials regularly**
   - Update `REDIS_PASSWORD` in `.env`
   - Restart services to pick up new password
   - For Kubernetes, update the secret and restart pods

3. **Monitor access attempts**
   - Review Redis logs for authentication failures
   - Set up alerts for unusual connection patterns

4. **Use TLS in production**
   - For production deployments, consider enabling Redis TLS/SSL
   - See: https://redis.io/docs/management/security/encryption/

5. **Network-level protection**
   - Use firewall rules to restrict access to Redis port
   - In production, ensure Redis is not exposed to the Internet
   - Use VPCs or private networks

## Additional Production Hardening

For production deployments, consider these additional measures:

### 1. Firewall Rules

Ensure your server firewall blocks port 6379:
```bash
# UFW (Ubuntu)
sudo ufw deny 6379/tcp

# iptables
sudo iptables -A INPUT -p tcp --dport 6379 -j DROP
```

### 2. Redis Configuration Hardening

Create a `redis.conf` file with additional security settings:
```conf
# Disable dangerous commands
rename-command FLUSHDB ""
rename-command FLUSHALL ""
rename-command CONFIG ""
rename-command SHUTDOWN ""

# Bind to specific interfaces only
bind 127.0.0.1 ::1

# Enable protected mode
protected-mode yes

# Disable lua scripting if not needed
enable-lua false
```

Mount this in your Docker/Kubernetes deployment:
```yaml
volumes:
  - ./redis.conf:/usr/local/etc/redis/redis.conf
command: redis-server /usr/local/etc/redis/redis.conf
```

### 3. Monitoring & Alerting

Set up monitoring for:
- Failed authentication attempts: `INFO stats` -> `rejected_connections`
- Unusual connection patterns
- Memory usage
- Command execution patterns

## Troubleshooting

### Error: "NOAUTH Authentication required"

**Cause:** Application trying to connect without password

**Solution:** Ensure `REDIS_URL` includes the password:
```bash
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
```

### Error: "ERR invalid password"

**Cause:** Password mismatch between Redis server and client

**Solution:**
1. Check the Redis server password: `docker exec redis env | grep REDIS_PASSWORD`
2. Check the agent's REDIS_URL environment variable
3. Ensure both match

### Health check failing

**Cause:** Health check not using password

**Solution:** Already fixed in the deployment files. Health checks now include `-a "$REDIS_PASSWORD"`

## References

- [Redis Security Documentation](https://redis.io/docs/management/security/)
- [Redis AUTH command](https://redis.io/commands/auth/)
- [Docker Compose Secrets](https://docs.docker.com/compose/use-secrets/)
- [Kubernetes Secrets](https://kubernetes.io/docs/concepts/configuration/secret/)
- [BSI CERT-Bund Reports](https://reports.cert-bund.de/en/)

## Change History

| Date | Change | Author |
|------|--------|--------|
| 2026-07-29 | Initial Redis security hardening | Claude Code |
| 2026-07-29 | Added authentication, network isolation, updated docs | Claude Code |
