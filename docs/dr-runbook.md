# Disaster Recovery: Backup Cadence + Drill Procedure

Raven's Postgres database runs directly on the VPS (not containerized — see
`raven/settings.py` `DATABASES['default']`). There was no backup job or
RTO/RPO measurement in place before this runbook. This defines both.

## 1. Set up scheduled backups (defines RPO)

On the VPS, per environment directory:

```bash
# staging
cd /srv/raven/staging
crontab -e
# add:
0 */6 * * * /srv/raven/staging/scripts/db-backup.sh staging >> /srv/raven/staging/backups/cron.log 2>&1

# production
cd /srv/raven/prod
crontab -e
# add:
0 */6 * * * /srv/raven/prod/scripts/db-backup.sh production >> /srv/raven/prod/backups/cron.log 2>&1
```

**The cron interval IS the RPO.** Every 6 hours → worst case 6 hours of data
loss. Tighten to `0 * * * *` (hourly) if that's not acceptable, at the cost
of more dump load on the DB.

`scripts/db-backup.sh` also enforces a 14-day retention window and logs each
run's duration/size to `backups/backup-history.log`.

**Off-host copy (not yet automated):** the script above only writes to local
disk on the same VPS — that protects against DB corruption/accidental drop,
not against losing the whole host. Before quoting RPO/RTO externally, add an
off-box copy (`rsync`/`rclone` to another host, or S3-compatible storage) and
note it here once wired up.

## 2. Run a drill (measures real RTO/RPO)

Always drill staging first:

```bash
cd /srv/raven/staging
./scripts/dr-drill.sh staging
```

This restores the latest backup into a scratch database, times it, verifies
the schema actually has tables (not a silent empty restore), then drops the
scratch DB. It never touches the live staging/production database. Output:

```
RPO (data loss window) : <N> minutes   — from backup age, not restore speed
RTO (data restore only): <N> seconds
```

Run it on production the same way once staging looks right — it prompts for
a `yes` confirmation first, and still only restores into a scratch DB.

Both runs append a line to `backups/dr-drill-history.log` so results trend
over time instead of being a one-off number.

## 3. What full-stack RTO adds on top

The drill above only measures the data-layer restore. A real production
outage's total RTO also includes:

- **Container redeploy**: `scripts/deploy-prod.sh` health-checks for up to
  90s before declaring success/rollback — add this to the drill's RTO number.
- **VPS loss (not just DB loss)**: if the whole host dies, add provisioning
  time for a replacement VPS + `git clone` + `.env` recreation + first
  `docker compose up` + restoring the backup onto it. This isn't scripted
  yet — time it manually once, on a spare VPS, to get a real number instead
  of guessing.

## 4. Reporting real figures

Once a few drills have run, report the actual numbers from
`backups/dr-drill-history.log` — don't extrapolate from a single run. Update
this file's "off-host copy" section once that's wired up, since it changes
both the RPO story and the RTO story (restoring from remote storage is
slower than local disk).
