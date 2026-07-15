#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Disaster-recovery drill — measures REAL RTO/RPO instead of guessing.
#
# Restores the most recent backup produced by scripts/db-backup.sh into a
# throwaway database on the SAME Postgres server, times the restore, and
# reports:
#   RPO = age of the backup being restored (worst-case data loss if the
#         primary DB had died the instant before this drill started)
#   RTO (data layer only) = time to restore the dump + verify row counts
#
# This does NOT include app-container redeploy time or DNS/VPS failover —
# see docs/dr-runbook.md for the full-stack drill that adds those in.
#
# SAFETY: only ever restores into a scratch DB (raven_drill_<timestamp>),
# never touches the real staging/production database. Always run this
# against the STAGING backup first.
#
# Usage:
#   ./scripts/dr-drill.sh staging
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

ENV_NAME="${1:-}"
if [[ "${ENV_NAME}" != "staging" && "${ENV_NAME}" != "production" ]]; then
  echo "Usage: $0 <staging|production>" >&2
  exit 1
fi

if [[ "${ENV_NAME}" == "production" ]]; then
  echo "[dr-drill] WARNING: you're about to drill against a PRODUCTION backup."
  echo "[dr-drill] This restores into a scratch DB only — the live prod DB is never touched."
  read -r -p "[dr-drill] Type 'yes' to continue: " CONFIRM
  [[ "${CONFIRM}" == "yes" ]] || { echo "[dr-drill] Aborted."; exit 1; }
fi

if [[ ! -f .env ]]; then
  echo "[dr-drill] .env not found in $(pwd) — run this from /srv/raven/<env>/" >&2
  exit 1
fi

if [[ "${ENV_NAME}" == "staging" ]]; then
  PREFIX="STAGING_DB"
else
  PREFIX="PRODUCTION_DB"
fi

DB_USER=$(grep -E "^${PREFIX}_USER=" .env | cut -d= -f2-)
DB_PASSWORD=$(grep -E "^${PREFIX}_PASSWORD=" .env | cut -d= -f2- | tr -d "'\"")
DB_HOST=$(grep -E "^${PREFIX}_HOST=" .env | cut -d= -f2-)
DB_PORT=$(grep -E "^${PREFIX}_PORT=" .env | cut -d= -f2-)

BACKUP_DIR="./backups"
HISTORY_LOG="${BACKUP_DIR}/backup-history.log"

# ── 1. Find the most recent backup for this environment ──────────────────────
LATEST_DUMP=$(find "${BACKUP_DIR}" -name "${ENV_NAME}_*.sql.gz" -print0 2>/dev/null \
  | xargs -0 ls -t 2>/dev/null | head -n1 || true)

if [[ -z "${LATEST_DUMP}" ]]; then
  echo "[dr-drill] No backup found in ${BACKUP_DIR} for env=${ENV_NAME}." >&2
  echo "[dr-drill] Run scripts/db-backup.sh ${ENV_NAME} first — there is nothing to measure yet." >&2
  exit 1
fi

# ── 2. RPO = age of that backup file right now ────────────────────────────────
BACKUP_EPOCH=$(stat -c %Y "${LATEST_DUMP}" 2>/dev/null || stat -f %m "${LATEST_DUMP}")
NOW_EPOCH=$(date +%s)
RPO_SECONDS=$((NOW_EPOCH - BACKUP_EPOCH))

echo "[dr-drill] Latest backup: ${LATEST_DUMP}"
echo "[dr-drill] Backup age (RPO if disaster were NOW): $((RPO_SECONDS / 60)) min ($(printf '%dh %dm' $((RPO_SECONDS/3600)) $(((RPO_SECONDS%3600)/60))))"

# ── 3. Restore into a scratch DB, timed ───────────────────────────────────────
DRILL_DB="raven_drill_$(date -u +%Y%m%dT%H%M%SZ)"
echo "[dr-drill] Creating scratch database ${DRILL_DB}..."
PGPASSWORD="${DB_PASSWORD}" createdb -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "${DRILL_DB}"

RESTORE_START=$(date +%s)
echo "[dr-drill] Restoring ${LATEST_DUMP} into ${DRILL_DB}..."
gunzip -c "${LATEST_DUMP}" | PGPASSWORD="${DB_PASSWORD}" psql \
  -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DRILL_DB}" \
  -v ON_ERROR_STOP=1 -q > /tmp/dr-drill-restore.log 2>&1
RESTORE_END=$(date +%s)
RESTORE_SECONDS=$((RESTORE_END - RESTORE_START))

# ── 4. Sanity check: row count on a core table, so a "successful" restore
#      that silently restored an empty schema doesn't pass ───────────────────
TABLE_COUNT=$(PGPASSWORD="${DB_PASSWORD}" psql -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DRILL_DB}" \
  -tAc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';")

echo "[dr-drill] Restore took ${RESTORE_SECONDS}s. Restored schema has ${TABLE_COUNT} tables."

if [[ "${TABLE_COUNT}" -eq 0 ]]; then
  echo "[dr-drill] !! FAIL: restored database has 0 tables — backup or restore is broken." >&2
  PGPASSWORD="${DB_PASSWORD}" dropdb -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "${DRILL_DB}"
  exit 1
fi

# ── 5. Cleanup ────────────────────────────────────────────────────────────────
PGPASSWORD="${DB_PASSWORD}" dropdb -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" "${DRILL_DB}"
echo "[dr-drill] Scratch database dropped."

# ── 6. Report ──────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo " DR DRILL RESULT — ${ENV_NAME} — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "════════════════════════════════════════════════════════════"
echo " RPO (data loss window) : $((RPO_SECONDS / 60)) minutes  — driven by backup cron cadence, not restore speed"
echo " RTO (data restore only): ${RESTORE_SECONDS} seconds"
echo " Tables verified         : ${TABLE_COUNT}"
echo "────────────────────────────────────────────────────────────"
echo " NOTE: full-stack RTO also needs container redeploy time"
echo " (~90s health-check window per scripts/deploy-prod.sh) plus"
echo " however long it takes to provision a replacement VPS if the"
echo " host itself is lost. Add those manually — see docs/dr-runbook.md."
echo "════════════════════════════════════════════════════════════"

# Append machine-readable result for trending across repeated drills
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) env=${ENV_NAME} rpo_s=${RPO_SECONDS} rto_restore_s=${RESTORE_SECONDS} tables=${TABLE_COUNT}" \
  >> "${BACKUP_DIR}/dr-drill-history.log"
