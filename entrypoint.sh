#!/bin/sh
# Every diagnostic line is written to stderr so Railway's Deploy Logs
# (which surface stderr reliably) always captures them.
log() { printf '%s\n' "$*" 1>&2; }

log "=== Elaxtra Outreach Entrypoint ==="
log "PORT=${PORT:-not set}"
log "WEBHOOK_PORT=${WEBHOOK_PORT:-not set}"
log "DATABASE_URL set: $([ -n "$DATABASE_URL" ] && echo yes || echo no)"
log "ANTHROPIC_API_KEY set: $([ -n "$ANTHROPIC_API_KEY" ] && echo yes || echo no)"
log "Python: $(python --version 2>&1)"

# Migrations run in the background so they CANNOT block the HTTP server
# coming up. /health must respond before Railway's healthcheck deadline
# regardless of DB state. If migrations fail, run them manually with:
#     railway run alembic upgrade head
(
    if [ -n "$DATABASE_URL" ]; then
        log "[bg-migrate] alembic upgrade head starting..."
        alembic upgrade head 1>&2 2>&1
        rc=$?
        if [ "$rc" -eq 0 ]; then
            log "[bg-migrate] migrations OK"
        else
            log "[bg-migrate] WARN: migrations exited $rc. Run 'railway run alembic upgrade head' once DB is reachable."
        fi
    else
        log "[bg-migrate] DATABASE_URL not set — skipping migrations."
    fi
) &

log "=== Server starting now (migrations run in background) ==="
exec python -m src.main serve
