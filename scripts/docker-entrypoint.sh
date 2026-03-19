#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Docker entrypoint — runs inside the Raven container at startup.
# Order: collectstatic → migrate → gunicorn
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo "[entrypoint] Collecting static files..."
python manage.py collectstatic --noinput

echo "[entrypoint] Running database migrations..."
python manage.py migrate --noinput

echo "[entrypoint] Creating superadmin (if not exists)..."
python manage.py create_superadmin

echo "[entrypoint] Starting Gunicorn..."
exec gunicorn raven.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-4}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --access-logfile - \
    --error-logfile -
