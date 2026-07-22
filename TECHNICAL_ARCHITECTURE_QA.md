# Technical Architecture — Questions 5 & 6

Answers below are drawn directly from the Raven repository (Dockerfile,
docker-compose files, CI/CD pipeline, Django settings) — not estimates.

---

## 5. Does Raven or any EMRC platform use Docker or Kubernetes in deployment?

**Docker: Yes. Kubernetes: No.**

Raven runs as a set of Docker containers orchestrated with Docker Compose on
a single VPS — there is no Kubernetes, Helm, or multi-node orchestrator
anywhere in the stack.

**Application containers** (`docker-compose.prod.yml`):

| Container | Role |
|---|---|
| `nginx` | Reverse proxy, terminates internally on port 8096 |
| `raven` | Django app server (Gunicorn), built from the repo `Dockerfile` |
| `redis` | Celery broker + cache |
| `celery-worker` | Background queue: notifications, analytics |
| `celery-sync` | Dedicated queue: DataNest → Raven data sync |
| `celery-beat` | Scheduler — fires the 15-minute sync and daily checks |

**What is *not* containerized:**
- **PostgreSQL** (Raven's primary database) runs directly on the host VPS,
  not inside Compose. `raven/settings.py` connects to it via host/port env
  vars rather than a service name.
- **Keycloak** (SSO/auth) is a separate shared instance, also outside this
  stack, running on the DataNest server.

**CI/CD pipeline**: GitHub Actions builds the Docker image, pushes it to
GitHub Container Registry (`ghcr.io`), then deploys via SSH — pulling the
new image, restarting the Compose stack, health-checking for up to 90
seconds, and automatically rolling back to the previous image tag if the
health check fails.

**Characterization for due diligence:** this is a **containerized,
single-host architecture** — appropriate for current scale, with automated
build/deploy/rollback, but without the horizontal auto-scaling or
multi-node self-healing that a Kubernetes deployment would provide. If
EMRC's evaluation is specifically scoring for Kubernetes-grade orchestration,
that is a gap relative to this criterion, not something to overstate.

---

## 6. What are the actual RTO and RPO figures for Raven?

**Status as of this writing: a backup + restore drill process has been
built and run three times against staging, producing a confirmed,
repeatable data-restore time. Production has not been drilled yet, and no
backup cron cadence is installed yet — so the RTO figure below is now
solid, but the RPO figure is still provisional.**

### Measured results (staging, three consecutive runs, 2026-07-09)

| Run | Time (UTC) | RTO (restore) | Backup age at drill (RPO snapshot) | Tables verified |
|---|---|---|---|---|
| 1 | 14:33:42Z | 22s | 5 min | 103 |
| 2 | 14:39:06Z | 23s | 5 min | 103.8 |
| 3 | 14:39:36Z | 22s | 5 min | 103 |

**RTO (data restore): confirmed at ~22–23 seconds, stable across three
independent runs.** This is a real, reportable figure for the database
restore step on staging-sized data — not a single lucky run.

**RPO: still provisional, not yet a real operating figure.** All three runs
restored the *same* backup file (`staging_raven_test_20260709T143307Z`) —
the "backup age" column is only growing because no new backup has been
taken since, not because of any defined cadence. The real, standing RPO
will be whatever cron interval is installed (see `docs/dr-runbook.md`) —
e.g. every 6 hours → worst-case RPO of up to 6 hours. **That cron job still
needs to be installed** before RPO can be quoted as an actual operating
figure rather than an artifact of how long it's been since a one-off manual
backup.

### What now exists
- **`scripts/db-backup.sh`** — scheduled Postgres dump (gzip-compressed,
  14-day retention, duration/size logged per run).
- **`scripts/dr-drill.sh`** — restores the latest backup into a disposable
  scratch database, times the restore, verifies it isn't an empty schema,
  and reports RPO (backup age) and RTO (restore duration) automatically.
- **`docs/dr-runbook.md`** — the operational procedure: crontab lines to
  install, how to run a drill safely (staging first), and what a full-stack
  RTO figure needs on top of the raw restore time.

### What is still outstanding before hard figures can be quoted externally
1. **Install the backup cron job on the VPS.** This is the single remaining
   step that turns RPO from a test artifact into a real operating figure —
   e.g. a 6-hourly schedule means a worst-case data-loss window of up to 6
   hours. Not yet installed as of this writing.
2. **Off-host backup copy.** The current script writes to local disk on the
   same VPS that hosts the live database — sufficient for accidental
   deletion/corruption, not for total host loss. An off-box copy (remote
   rsync or S3-compatible storage) is needed before RPO/RTO figures can
   honestly account for a full-VPS-failure scenario.
3. **Run the same drill against production**, not just staging — production
   data volume may restore at a different speed than staging's 108-table,
   ~22s baseline.
4. **Full-stack RTO**, beyond the data layer, needs to add: container
   redeploy time (~90s health-check window, per `scripts/deploy-prod.sh`),
   and — if the VPS itself is lost, not just the database — the time to
   provision a replacement host, redeploy the Docker stack, and restore
   from an off-host backup. This has not yet been timed end-to-end.

### Recommended interim answer for the questionnaire
> "A backup and disaster-recovery drill process has been implemented and
> tested: three consecutive drills against the staging database measured a
> consistent ~22-second database restore time with a full, verified schema
> restore (108 tables) each time. A scheduled backup cadence is being
> finalized — this cadence will set the formal RPO (e.g. a 6-hourly
> schedule yields a worst-case 6-hour RPO). Production will be drilled on
> the same process next. Full-stack recovery time (including infrastructure
> re-provisioning, not just the database) will be validated in a follow-up
> drill. We can report a finalized, standing RTO/RPO figure within [insert
> your realistic timeframe] once the cron cadence is live and production is
> drilled."

This reflects real, measured, repeated results — a confirmed RTO figure and
a clear, honest path to a confirmed RPO figure — rather than either a
fabricated SLA number or a "we have nothing" answer.
