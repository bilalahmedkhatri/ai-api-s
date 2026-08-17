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

# Install system dependencies (ffmpeg is needed for video ingest)
RUN apt-get update -qq && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy application source
COPY . .

# Create directories required at runtime
RUN mkdir -p /app/logs

# Expose ASGI port
EXPOSE 8000

# Worker count (override in production via WORKERS env var if needed)
ENV WORKERS=1

# Uvicorn entrypoint — see entrypoint.sh
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
