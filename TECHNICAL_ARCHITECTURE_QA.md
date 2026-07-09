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

**Status as of this writing: no RTO/RPO had been formally defined, measured,
or tested.** Rather than supply invented figures, we built the missing
pieces — a backup process and a drill script — so real numbers can be
produced and reported honestly. Current state:

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
1. **Install the backup cron job on the VPS.** The cadence chosen *is* the
   RPO — e.g. a 6-hourly schedule means a worst-case data-loss window of up
   to 6 hours. Not yet installed as of this writing.
2. **Run the drill at least once** (`./scripts/dr-drill.sh staging`) to get
   a first real, measured RTO (data-restore time) and RPO (backup age)
   number, then repeat over a few cycles to confirm consistency rather than
   relying on a single run.
3. **Off-host backup copy.** The current script writes to local disk on the
   same VPS that hosts the live database — sufficient for accidental
   deletion/corruption, not for total host loss. An off-box copy (remote
   rsync or S3-compatible storage) is needed before RPO/RTO figures can
   honestly account for a full-VPS-failure scenario.
4. **Full-stack RTO**, beyond the data layer, needs to add: container
   redeploy time (~90s health-check window, per `scripts/deploy-prod.sh`),
   and — if the VPS itself is lost, not just the database — the time to
   provision a replacement host, redeploy the Docker stack, and restore
   from an off-host backup. This has not yet been timed end-to-end.

### Recommended interim answer for the questionnaire
> "No formal RTO/RPO has been defined or tested to date. A backup and
> disaster-recovery drill process has been implemented (scheduled Postgres
> backups + an automated restore drill); once the backup cadence is live and
> an initial drill cycle is complete, we will have measured figures for the
> database recovery point and recovery time. Full-stack recovery time
> (including infrastructure re-provisioning) will be validated in a
> follow-up drill. Formalizing these figures is an active, near-term
> hardening item, not a completed control."

This is accurate today and gives EMRC a credible remediation timeline
instead of a fabricated SLA-style number.
