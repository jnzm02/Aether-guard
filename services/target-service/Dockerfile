# ── Build stage ───────────────────────────────────────────────────────────────
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Cache dependency layer — only re-fetched when go.mod/go.sum change.
COPY go.mod go.sum ./
RUN go mod download

COPY . .

# Produce a fully-static binary. CGO disabled for distroless compatibility.
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build \
    -ldflags="-s -w -X main.version=1.0.0" \
    -o /bin/target-service \
    ./cmd/server/

# ── Runtime stage ──────────────────────────────────────────────────────────────
# Alpine instead of distroless — adds wget for health checks and a shell
# for debugging, at the cost of ~5 MiB extra image size.
FROM alpine:3.19

RUN apk add --no-cache ca-certificates wget

LABEL org.opencontainers.image.title="aether-guard/target-service" \
      org.opencontainers.image.description="Intentionally breakable microservice for SRE chaos demos" \
      org.opencontainers.image.version="1.0.0"

WORKDIR /app
COPY --from=builder /bin/target-service .

EXPOSE 8080

ENTRYPOINT ["/app/target-service"]
