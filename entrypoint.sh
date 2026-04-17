#!/bin/sh
echo "=== Entrypoint starting ==="
echo "PORT=$PORT"
echo "WEBHOOK_PORT=$WEBHOOK_PORT"
echo "WEBHOOK_HOST=$WEBHOOK_HOST"
echo "DATABASE_URL is set: $([ -n "$DATABASE_URL" ] && echo yes || echo no)"

echo "Running migrations..."
alembic upgrade head || echo "Migration skipped — no DB or failed"

echo "Starting server on port ${PORT:-${WEBHOOK_PORT:-8000}}..."
exec python -m src.main serve
