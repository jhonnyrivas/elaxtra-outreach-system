#!/bin/sh
echo "=== Elaxtra Outreach Entrypoint ==="
echo "PORT=${PORT:-not set}"
echo "DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo yes || echo no)"

# Migrations — best effort, NEVER block server startup
if [ -n "$DATABASE_URL" ]; then
    echo "Running migrations..."
    alembic upgrade head 2>&1 || echo "WARN: migrations skipped — DB may not be ready"
else
    echo "WARN: DATABASE_URL not set — skipping migrations"
fi

echo "Starting server..."
exec python -m src.main serve
