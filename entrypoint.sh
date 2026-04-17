#!/bin/sh
echo "=== Elaxtra Outreach Entrypoint ==="
echo "PORT=${PORT:-not set}"
echo "WEBHOOK_PORT=${WEBHOOK_PORT:-not set}"
echo "DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo yes || echo no)"
echo "ANTHROPIC_API_KEY set: $([ -n "$ANTHROPIC_API_KEY" ] && echo yes || echo no)"

echo "=== Running migrations (best effort) ==="
alembic upgrade head 2>&1 || echo "WARN: alembic exited with error — continuing anyway"

echo "=== Migrations done ==="
echo "Python version: $(python --version 2>&1)"
echo "Starting server with: python -m src.main serve"
echo "=== Server starting now ==="

exec python -m src.main serve
