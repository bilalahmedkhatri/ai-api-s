# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — dependency builder
# Uses uv for fast, deterministic installs.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Install uv (official installer, pinned for reproducibility)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Copy only the files uv needs to resolve deps — maximises layer cache reuse.
COPY pyproject.toml uv.lock ./

# Install production deps into an isolated prefix (no venv overhead in image)
RUN uv pip install --system --no-cache -r pyproject.toml

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime image
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install Litestream (SQLite → S3 streaming replication) and ffmpeg (video ingest)
# Both are lightweight system binaries — no Python dep added.
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        wget ca-certificates ffmpeg \
    && wget -qO /tmp/litestream.deb \
        https://github.com/benbjohnson/litestream/releases/download/v0.3.13/litestream-v0.3.13-linux-amd64.deb \
    && dpkg -i /tmp/litestream.deb \
    && rm -rf /tmp/litestream.deb /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy application source
COPY . .

# Create directories required at runtime
RUN mkdir -p /app/logs /app/data

# SQLite database lives on a mounted persistent volume at /app/data/
ENV DATABASE_URL="sqlite+aiosqlite:////app/data/gateway.db"

# Expose ASGI port
EXPOSE 8000

# Worker count: 1 for SQLite (aiosqlite is not fork-safe).
# Override with WORKERS env var when switching to PostgreSQL.
ENV WORKERS=1

# Litestream + Uvicorn entrypoint — see entrypoint.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
