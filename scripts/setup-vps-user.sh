#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# One-time VPS setup — creates the dedicated "raven" deploy user.
# Run this ONCE on the VPS as root:
#
#   bash scripts/setup-vps-user.sh
#
# After this script runs, set VPS_USER=raven in GitHub Secrets.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DEPLOY_USER="raven"
STAGING_DIR="/srv/raven/staging"
PROD_DIR="/srv/raven/prod"
REPO_URL="https://github.com/EMRC-DigitalLab/Raven-Backend-KEDCO.git"

# ── 1. Create user ────────────────────────────────────────────────────────────
if id "${DEPLOY_USER}" &>/dev/null; then
  echo "[setup] User '${DEPLOY_USER}' already exists — skipping creation."
else
  useradd --system --create-home --shell /bin/bash "${DEPLOY_USER}"
  echo "[setup] User '${DEPLOY_USER}' created."
fi

# ── 2. Add raven to the docker group (so it can run docker commands) ──────────
usermod -aG docker "${DEPLOY_USER}"
echo "[setup] Added '${DEPLOY_USER}' to docker group."

# ── 3. Set up SSH authorized_keys ─────────────────────────────────────────────
SSH_DIR="/home/${DEPLOY_USER}/.ssh"
mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

echo ""
echo "─────────────────────────────────────────────────────────────────────"
echo "  Paste the PUBLIC key from your GitHub Actions SSH keypair below."
echo "  (the contents of ~/.ssh/github_actions_raven.pub on your local machine)"
echo "  Press Enter, then Ctrl+D when done."
echo "─────────────────────────────────────────────────────────────────────"
cat >> "${SSH_DIR}/authorized_keys"

chmod 600 "${SSH_DIR}/authorized_keys"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${SSH_DIR}"
echo "[setup] SSH public key added."

# ── 4. Create deploy directories and clone the repo ───────────────────────────
mkdir -p "${STAGING_DIR}" "${PROD_DIR}"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" /srv/raven

echo "[setup] Cloning repo into staging dir..."
sudo -u "${DEPLOY_USER}" git clone "${REPO_URL}" "${STAGING_DIR}" || true
sudo -u "${DEPLOY_USER}" git -C "${STAGING_DIR}" checkout staging 2>/dev/null || \
  sudo -u "${DEPLOY_USER}" git -C "${STAGING_DIR}" checkout -b staging

echo "[setup] Cloning repo into prod dir..."
sudo -u "${DEPLOY_USER}" git clone "${REPO_URL}" "${PROD_DIR}" || true
sudo -u "${DEPLOY_USER}" git -C "${PROD_DIR}" checkout main

# ── 5. Create .env files from template ────────────────────────────────────────
if [[ ! -f "${STAGING_DIR}/.env" ]]; then
  cp "${STAGING_DIR}/.env.example" "${STAGING_DIR}/.env"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${STAGING_DIR}/.env"
  echo "[setup] Created ${STAGING_DIR}/.env — EDIT THIS FILE with staging values."
fi

if [[ ! -f "${PROD_DIR}/.env" ]]; then
  cp "${PROD_DIR}/.env.example" "${PROD_DIR}/.env"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${PROD_DIR}/.env"
  echo "[setup] Created ${PROD_DIR}/.env — EDIT THIS FILE with production values."
fi

# ── 6. Summary ────────────────────────────────────────────────────────────────
echo ""
echo "═════════════════════════════════════════════════════════════════════"
echo "  Setup complete. Next steps:"
echo ""
echo "  1. Edit the .env files with real values:"
echo "       nano ${STAGING_DIR}/.env   (set APP_ENV=staging)"
echo "       nano ${PROD_DIR}/.env      (set APP_ENV=production)"
echo ""
echo "  2. Add these to GitHub Secrets:"
echo "       VPS_USER        = raven"
echo "       VPS_HOST        = $(curl -s ifconfig.me 2>/dev/null || echo '31.97.56.29')"
echo "       VPS_PORT        = 22"
echo "       DEPLOY_PATH     = /srv/raven  (staging/prod scripts handle subdirs)"
echo ""
echo "  3. Test SSH access from your local machine:"
echo "       ssh -i ~/.ssh/github_actions_raven raven@$(curl -s ifconfig.me 2>/dev/null || echo '31.97.56.29')"
echo "═════════════════════════════════════════════════════════════════════"
