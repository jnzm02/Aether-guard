# ── Build stage ───────────────────────────────────────────────────────────────
# Unlike the Go service, the build context is THIS directory only
# (services/target-service-java) — there is no sibling SDK/module to include.
#     docker build -t aether-guard/target-service-java services/target-service-java
FROM maven:3.9-eclipse-temurin-21 AS builder

WORKDIR /build

# Dependency layer (cached until pom.xml changes).
COPY pom.xml ./
RUN mvn -q -B -DskipTests dependency:go-offline

# Sources + package.
COPY src ./src
RUN mvn -q -B -DskipTests package

# ── Runtime stage ──────────────────────────────────────────────────────────────
FROM eclipse-temurin:21-jre

LABEL org.opencontainers.image.title="aether-guard/target-service-java" \
      org.opencontainers.image.description="JVM analog of the Go target-service — an intentionally breakable Spring Boot microservice for SRE chaos demos" \
      org.opencontainers.image.version="1.0.0"

WORKDIR /app
COPY --from=builder /build/target/target-service-java-*.jar /app/app.jar

EXPOSE 8085

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
