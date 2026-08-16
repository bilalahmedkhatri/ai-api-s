#!/bin/sh
# entrypoint.sh — starts Litestream replication then Uvicorn.
#
# If LITESTREAM_ACCESS_KEY_ID and LITESTREAM_SECRET_ACCESS_KEY are set,
# Litestream will stream WAL changes to S3 continuously.
# If not set, Uvicorn starts directly (safe for local/dev runs).

set -e

if [ -n "$LITESTREAM_ACCESS_KEY_ID" ] && [ -n "$LITESTREAM_S3_BUCKET" ]; then
    echo "Starting Litestream replication → s3://${LITESTREAM_S3_BUCKET}/gateway.db"
    exec litestream replicate \
        /app/data/gateway.db \
        "s3://${LITESTREAM_S3_BUCKET}/gateway.db" \
        -- \
        uvicorn main:app \
            --host 0.0.0.0 \
            --port 8000 \
            --workers "${WORKERS:-1}" \
            --log-level warning
else
    echo "Litestream not configured — starting Uvicorn directly."
    exec uvicorn main:app \
        --host 0.0.0.0 \
        --port 8000 \
        --workers "${WORKERS:-1}" \
        --log-level warning
fi
