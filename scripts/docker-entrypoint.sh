#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Docker entrypoint — runs inside the Raven container at startup, as root.
# Order: fix volume ownership → collectstatic → migrate → daphne (as appuser)
#
# Runs as root specifically so it can self-heal ownership on /app/media and
# /app/staticfiles on every single startup — these are Docker named volumes,
# and Docker only copies the image's directory ownership into a volume the
# FIRST time that volume is ever created. Any volume that already existed
# before appuser ownership was added to the image (or was ever touched while
# a container ran as a different user) stays wrong forever otherwise, no
# matter how many times the image gets rebuilt/redeployed. This makes that
# a non-issue permanently — no manual `docker volume rm` ever required again.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "[entrypoint] Ensuring media/static volumes are owned by appuser..."
chown -R appuser:appuser /app/media /app/staticfiles

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

echo "[entrypoint] Running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Starting Daphne (ASGI) as appuser..."
exec su appuser -s /bin/sh -c "exec daphne \
    -b 0.0.0.0 \
    -p 8000 \
    --access-log - \
    --http-timeout 180 \
    raven.asgi:application"
