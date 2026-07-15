# syntax=docker/dockerfile:1
FROM golang:1.21-alpine AS builder

WORKDIR /build

# Copy go.mod first for better caching
COPY go.mod go.sum* ./
RUN go mod download

# Copy source
COPY . .

# Build binary
RUN CGO_ENABLED=0 GOOS=linux go build -o event-tracker ./cmd/server

# Final stage
FROM alpine:3.18

RUN apk --no-cache add ca-certificates tzdata

WORKDIR /app

COPY --from=builder /build/event-tracker .

# Run as non-root user
RUN addgroup -g 1000 app && adduser -D -u 1000 -G app app
USER app

EXPOSE 8083

CMD ["./event-tracker"]
