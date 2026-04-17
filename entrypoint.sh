#!/bin/sh
set -e
echo "Running migrations..."
alembic upgrade head || echo "Migration failed or no DB — continuing"
echo "Starting server..."
exec python -m src.main serve
