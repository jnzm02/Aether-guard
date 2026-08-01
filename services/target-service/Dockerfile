# ── Build stage ───────────────────────────────────────────────────────────────
# IMPORTANT: build with the REPO ROOT as the context, not services/target-service.
# go.mod has `replace github.com/jnzm02/aether-guard/sdk/go => ../../sdk/go`, so the
# sibling sdk/go module must be inside the build context. Invoke as:
#     docker build -f services/target-service/Dockerfile -t <img> .
FROM golang:1.25-alpine AS builder

WORKDIR /src

# Dependency layer (cached until a go.mod/go.sum changes): copy the module manifests
# for the service AND the sdk it replaces, preserving the repo-relative layout the
# replace directive expects (../../sdk/go from services/target-service).
COPY sdk/go/go.mod sdk/go/go.sum ./sdk/go/
COPY services/target-service/go.mod services/target-service/go.sum ./services/target-service/
WORKDIR /src/services/target-service
RUN go mod download

# Sources.
COPY sdk/go/ /src/sdk/go/
COPY services/target-service/ /src/services/target-service/

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
