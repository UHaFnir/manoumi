#!/usr/bin/env bash
# Deployt custom_components/manoumi auf die echte Home-Assistant-Instanz.
# Siehe .claude/rules/ha-deploy.md: Dev-Server != Deploy, Zielpfad ist
# /config/custom_components/manoumi/, SSH-Ziel kommt aus .env.deploy (untracked).
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE=".env.deploy"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "Fehlt: $ENV_FILE (Vorlage: .env.deploy.example)" >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"

: "${HA_SSH_HOST:?HA_SSH_HOST fehlt in .env.deploy}"
: "${HA_SSH_USER:?HA_SSH_USER fehlt in .env.deploy}"
: "${HA_CONFIG_PATH:=/config}"
: "${HA_SSH_PORT:=22}"

TARGET="${HA_SSH_USER}@${HA_SSH_HOST}"
REMOTE_DIR="${HA_CONFIG_PATH}/custom_components/manoumi"

echo "Deploye custom_components/manoumi/ nach ${TARGET}:${REMOTE_DIR} (Port ${HA_SSH_PORT})"
# Home Assistant OS (Alpine, "Terminal & SSH"-Add-on) hat kein rsync -- tar+ssh
# statt rsync, damit der Deploy ohne Zusatz-Pakete auf der Appliance auskommt.
COPYFILE_DISABLE=1 tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' -C custom_components manoumi \
    | ssh -p "${HA_SSH_PORT}" "$TARGET" \
        "rm -rf '${REMOTE_DIR}' && mkdir -p '${HA_CONFIG_PATH}/custom_components' && tar xzf - -C '${HA_CONFIG_PATH}/custom_components'"

echo "Fertig. Integration in Home Assistant neu laden (Einstellungen -> Geraete & Dienste ->"
echo "MaNoUmi -> Neu laden) oder HA neu starten, damit der Code-Stand aktiv wird."
