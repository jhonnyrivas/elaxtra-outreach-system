#!/bin/sh
# Never exit on error — the server MUST start so /health can respond.
set +e

echo "=== Elaxtra Outreach Entrypoint ==="
echo "PORT=${PORT:-not set}"
echo "WEBHOOK_PORT=${WEBHOOK_PORT:-not set}"
echo "DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo yes || echo no)"

if [ -n "$DATABASE_URL" ]; then
    echo "Running migrations (best effort)..."
    alembic upgrade head
    alembic_exit=$?
    if [ "$alembic_exit" -ne 0 ]; then
        echo "WARN: alembic exited $alembic_exit — continuing; server will start and /health will serve."
        echo "      Migrations can be re-run manually once DB credentials are correct:"
        echo "      railway run alembic upgrade head"
    else
        echo "Migrations OK."
    fi
else
    echo "WARN: DATABASE_URL not set — skipping migrations."
fi

echo "Starting server on port ${PORT:-${WEBHOOK_PORT:-8000}}..."
exec python -m src.main serve
