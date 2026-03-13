#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# Deploy script — PRODUCTION
# Called by GitHub Actions via SSH.
# Pulls the :latest image, restarts containers, health-checks, rolls back on
# failure.
#
# Required env vars (passed by CI):
#   GITHUB_ACTOR         — ghcr.io login username
#   GITHUB_TOKEN         — ghcr.io login password
#   GITHUB_REPOSITORY    — e.g. org/repo (used to build image name)
#   KC_ADMIN_PASSWORD    — Keycloak admin password
#   KC_HOSTNAME          — Keycloak public hostname
#   KC_DB_USER           — Keycloak DB user
#   KC_DB_PASSWORD       — Keycloak DB password
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_LOWER=$(echo "${GITHUB_REPOSITORY}" | tr '[:upper:]' '[:lower:]')
IMAGE="ghcr.io/${REPO_LOWER}/raven:latest"
COMPOSE_FILE="docker-compose.prod.yml"
CONTAINER="raven_prod"
HEALTH_URL="http://localhost:8096/api/auth/token/"
MAX_RETRIES=18        # 18 × 5s = 90 seconds (prod gets more time)
RETRY_INTERVAL=5

# ── 1. Save currently running image digest for rollback ──────────────────────
echo "[deploy-prod] Saving current image for rollback..."
ROLLBACK_IMAGE=$(docker inspect --format='{{.Image}}' "${CONTAINER}" 2>/dev/null || echo "")

if [[ -n "${ROLLBACK_IMAGE}" ]]; then
  docker tag "${ROLLBACK_IMAGE}" "ghcr.io/${REPO_LOWER}/raven:rollback-prod" || true
  echo "[deploy-prod] Rollback image tagged: rollback-prod"
else
  echo "[deploy-prod] No running container found — fresh deploy."
fi

# ── 2. Log in to GHCR and pull new image ─────────────────────────────────────
echo "[deploy-prod] Logging in to GHCR..."
echo "${GITHUB_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR}" --password-stdin

echo "[deploy-prod] Pulling ${IMAGE}..."
docker pull "${IMAGE}"

# ── 3. Start new containers ───────────────────────────────────────────────────
echo "[deploy-prod] Starting containers..."
docker compose -f "${COMPOSE_FILE}" down --remove-orphans 2>/dev/null || true
GITHUB_REPOSITORY="${REPO_LOWER}" \
KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD}" \
KC_HOSTNAME="${KC_HOSTNAME}" \
KC_DB_USER="${KC_DB_USER}" \
KC_DB_PASSWORD="${KC_DB_PASSWORD}" \
  docker compose -f "${COMPOSE_FILE}" up -d

# ── 4. Health check ───────────────────────────────────────────────────────────
echo "[deploy-prod] Waiting for health check on ${HEALTH_URL}..."
PASSED=false

for i in $(seq 1 "${MAX_RETRIES}"); do
  if curl -s -o /dev/null "${HEALTH_URL}"; then
    echo "[deploy-prod] Health check passed (attempt ${i})."
    PASSED=true
    break
  fi
  echo "[deploy-prod] Attempt ${i}/${MAX_RETRIES} failed — retrying in ${RETRY_INTERVAL}s..."
  sleep "${RETRY_INTERVAL}"
done

# ── 5. Rollback if health check failed ───────────────────────────────────────
if [[ "${PASSED}" == "false" ]]; then
  echo "[deploy-prod] !! HEALTH CHECK FAILED — initiating ROLLBACK !!"

  if [[ -n "${ROLLBACK_IMAGE}" ]]; then
    docker tag "ghcr.io/${REPO_LOWER}/raven:rollback-prod" "${IMAGE}"
    GITHUB_REPOSITORY="${REPO_LOWER}" \
    KC_ADMIN_PASSWORD="${KC_ADMIN_PASSWORD}" \
    KC_HOSTNAME="${KC_HOSTNAME}" \
    KC_DB_USER="${KC_DB_USER}" \
    KC_DB_PASSWORD="${KC_DB_PASSWORD}" \
      docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans
    echo "[deploy-prod] Rolled back to previous image."
  else
    echo "[deploy-prod] No rollback image available — stopping containers."
    docker compose -f "${COMPOSE_FILE}" down
  fi

  # Print container logs for post-mortem
  echo "[deploy-prod] --- Container logs (last 100 lines) ---"
  docker logs "${CONTAINER}" --tail 100 || true

  exit 1   # ← non-zero exit marks the GitHub Actions job as FAILED (sends notification)
fi

# ── 6. Cleanup ────────────────────────────────────────────────────────────────
docker image prune -f
echo "[deploy-prod] Production deployment complete."
