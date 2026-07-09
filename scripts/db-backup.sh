#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Postgres backup — run on the VPS via cron (DB is not containerized, see
# raven/settings.py DATABASES['default'] — it runs directly on the host).
#
# Usage:
#   ./scripts/db-backup.sh staging
#   ./scripts/db-backup.sh production
#
# Reads DB_* creds from .env in the same dir this is run from (cd /srv/raven/<env> first).
# Writes timestamped, gzip-compressed dumps + a log line per run (start/end/duration)
# that scripts/dr-drill.sh reads to compute RPO.
#
# Suggested crontab (on the VPS, per environment dir):
#   0 */6 * * * /srv/raven/prod/scripts/db-backup.sh production >> /srv/raven/prod/backups/cron.log 2>&1
#   → every 6 hours. That cadence IS your RPO: worst case you lose up to 6h of data.
#   Tighten to hourly (0 * * * *) if 6h of data loss is unacceptable.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ENV_NAME="${1:-}"
if [[ "${ENV_NAME}" != "staging" && "${ENV_NAME}" != "production" ]]; then
  echo "Usage: $0 <staging|production>" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  echo "[db-backup] .env not found in $(pwd) — run this from /srv/raven/<env>/" >&2
  exit 1
fi

if [[ "${ENV_NAME}" == "staging" ]]; then
  PREFIX="STAGING_DB"
else
  PREFIX="PRODUCTION_DB"
fi

DB_NAME=$(grep -E "^${PREFIX}_NAME=" .env | cut -d= -f2-)
DB_USER=$(grep -E "^${PREFIX}_USER=" .env | cut -d= -f2-)
DB_PASSWORD=$(grep -E "^${PREFIX}_PASSWORD=" .env | cut -d= -f2- | tr -d "'\"")
DB_HOST=$(grep -E "^${PREFIX}_HOST=" .env | cut -d= -f2-)
DB_PORT=$(grep -E "^${PREFIX}_PORT=" .env | cut -d= -f2-)

BACKUP_DIR="./backups"
RETENTION_DAYS=14
mkdir -p "${BACKUP_DIR}"

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
DUMP_FILE="${BACKUP_DIR}/${ENV_NAME}_${DB_NAME}_${TIMESTAMP}.sql.gz"
LOG_FILE="${BACKUP_DIR}/backup-history.log"

START_EPOCH=$(date +%s)
echo "[db-backup] Starting dump of ${DB_NAME}@${DB_HOST}:${DB_PORT} -> ${DUMP_FILE}"

PGPASSWORD="${DB_PASSWORD}" pg_dump \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
  --format=plain --no-owner --no-privileges \
  | gzip > "${DUMP_FILE}"

END_EPOCH=$(date +%s)
DURATION=$((END_EPOCH - START_EPOCH))
SIZE=$(du -h "${DUMP_FILE}" | cut -f1)

echo "[db-backup] Done in ${DURATION}s, size ${SIZE}"

# One line per run — dr-drill.sh reads the last line to know the true backup cadence (RPO).
echo "${TIMESTAMP} env=${ENV_NAME} db=${DB_NAME} duration_s=${DURATION} size=${SIZE} file=${DUMP_FILE}" >> "${LOG_FILE}"

# ── Retention: delete dumps older than RETENTION_DAYS ─────────────────────────
find "${BACKUP_DIR}" -name "${ENV_NAME}_*.sql.gz" -mtime "+${RETENTION_DAYS}" -delete

echo "[db-backup] Retention enforced (${RETENTION_DAYS}d). Backup complete."
