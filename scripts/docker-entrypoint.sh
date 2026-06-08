#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Docker entrypoint — runs inside the Raven container at startup.
# Order: collectstatic → migrate → daphne (ASGI — supports HTTP + WebSocket)
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

echo "[entrypoint] Running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Starting Daphne (ASGI)..."
exec daphne \
    -b 0.0.0.0 \
    -p 8000 \
    --access-log - \
    --http-timeout 180 \
    raven.asgi:application
