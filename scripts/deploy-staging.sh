#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Deploy script — STAGING
# Called by GitHub Actions via SSH.
# Pulls the :staging image, restarts containers, health-checks, rolls back on
# failure.
#
# Required env vars (passed by CI):
#   GITHUB_ACTOR       — ghcr.io login username
#   GITHUB_TOKEN       — ghcr.io login password
#   GITHUB_REPOSITORY  — e.g. org/repo (used to build image name)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_LOWER=$(echo "${GITHUB_REPOSITORY}" | tr '[:upper:]' '[:lower:]')
IMAGE="ghcr.io/${REPO_LOWER}/raven:staging"
COMPOSE_FILE="docker-compose.staging.yml"
CONTAINER="raven_staging"
HEALTH_URL="http://localhost:8090/api/auth/token/"
MAX_RETRIES=12        # 12 × 5s = 60 seconds
RETRY_INTERVAL=5

# ── 1. Save currently running image digest for rollback ──────────────────────
echo "[deploy-staging] Saving current image for rollback..."
ROLLBACK_IMAGE=$(docker inspect --format='{{.Image}}' "${CONTAINER}" 2>/dev/null || echo "")

if [[ -n "${ROLLBACK_IMAGE}" ]]; then
  docker tag "${ROLLBACK_IMAGE}" "ghcr.io/${REPO_LOWER}/raven:rollback-staging" || true
  echo "[deploy-staging] Rollback image tagged: rollback-staging"
else
  echo "[deploy-staging] No running container found — fresh deploy."
fi

# ── 2. Write .env from CI secret ─────────────────────────────────────────────
echo "[deploy-staging] Writing .env..."
printf '%s' "${ENV_FILE_CONTENT}" > .env

# ── 3. Log in to GHCR and pull new image ─────────────────────────────────────
echo "[deploy-staging] Logging in to GHCR..."
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR}" --password-stdin

echo "[deploy-staging] Pulling ${IMAGE}..."
docker pull "${IMAGE}"

# ── 3. Start new containers ───────────────────────────────────────────────────
echo "[deploy-staging] Starting containers..."
GITHUB_REPOSITORY="${REPO_LOWER}" \
  docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

# ── 4. Health check ───────────────────────────────────────────────────────────
echo "[deploy-staging] Waiting for health check on ${HEALTH_URL}..."
PASSED=false

for i in $(seq 1 "${MAX_RETRIES}"); do
  if curl -sf "${HEALTH_URL}" > /dev/null 2>&1; then
    echo "[deploy-staging] Health check passed (attempt ${i})."
    PASSED=true
    break
  fi
  echo "[deploy-staging] Attempt ${i}/${MAX_RETRIES} failed — retrying in ${RETRY_INTERVAL}s..."
  sleep "${RETRY_INTERVAL}"
done

# ── 5. Rollback if health check failed ───────────────────────────────────────
if [[ "${PASSED}" == "false" ]]; then
  echo "[deploy-staging] HEALTH CHECK FAILED — initiating rollback..."

  if [[ -n "${ROLLBACK_IMAGE}" ]]; then
    # Retag rollback image back to :staging so compose pulls it
    docker tag "ghcr.io/${REPO_LOWER}/raven:rollback-staging" "${IMAGE}"
    GITHUB_REPOSITORY="${REPO_LOWER}" \
      docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans
    echo "[deploy-staging] Rolled back to previous image."
  else
    echo "[deploy-staging] No rollback image available — stopping containers."
    docker compose -f "${COMPOSE_FILE}" down
  fi

  # Print last 50 lines of container logs to help debug
  echo "[deploy-staging] --- Container logs ---"
  docker logs "${CONTAINER}" --tail 50 || true

  exit 1
fi

# ── 6. Cleanup ────────────────────────────────────────────────────────────────
docker image prune -f
echo "[deploy-staging] Deployment complete."
