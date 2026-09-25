#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${TERACOTA_APP_DIR:-/opt/teracota}"
APP_USER="${TERACOTA_APP_USER:-teracota}"
APP_GROUP="${TERACOTA_APP_GROUP:-teracota}"
BRANCH="${TERACOTA_BRANCH:-main}"
DB_PATH="${TERACOTA_DB_PATH:-}"
BACKUP_DIR="${TERACOTA_BACKUP_DIR:-/var/backups/teracota}"
SERVICE_SOURCE="${APP_DIR}/deploy/teracota.service"
SERVICE_TARGET="/etc/systemd/system/teracota.service"
IMPORT_SERVICE_SOURCE="${APP_DIR}/deploy/teracota-measurement-import.service"
IMPORT_SERVICE_TARGET="/etc/systemd/system/teracota-measurement-import.service"
IMPORT_TIMER_SOURCE="${APP_DIR}/deploy/teracota-measurement-import.timer"
IMPORT_TIMER_TARGET="/etc/systemd/system/teracota-measurement-import.timer"
NGINX_SOURCE="${APP_DIR}/deploy/nginx-teracota.conf"
NGINX_TARGET="/etc/nginx/sites-available/teracota"
HEALTH_URL="http://127.0.0.1:8000/healthz"

log() {
    printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
    printf '\nERROR: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n "${TERACOTA_UPDATE_TEMP:-}" ]]; then
        rm -f -- "${TERACOTA_UPDATE_TEMP}"
    fi
}

on_error() {
    local exit_code=$?
    printf '\nUpdate failed on line %s (exit %s).\n' "${BASH_LINENO[0]}" "${exit_code}" >&2
    if systemctl list-unit-files teracota.service >/dev/null 2>&1; then
        systemctl status teracota --no-pager --lines=20 >&2 || true
    fi
    exit "${exit_code}"
}

# Run from a temporary copy so pulling a new version of this script cannot
# change the instructions Bash is currently executing.
if [[ "${TERACOTA_UPDATE_STAGED:-0}" != "1" ]]; then
    staged_script="$(mktemp /tmp/teracota-update.XXXXXX)"
    cp -- "$0" "${staged_script}"
    chmod 0700 "${staged_script}"
    exec env \
        TERACOTA_UPDATE_STAGED=1 \
        TERACOTA_UPDATE_TEMP="${staged_script}" \
        TERACOTA_APP_DIR="${APP_DIR}" \
        TERACOTA_APP_USER="${APP_USER}" \
        TERACOTA_APP_GROUP="${APP_GROUP}" \
        TERACOTA_BRANCH="${BRANCH}" \
        TERACOTA_DB_PATH="${DB_PATH}" \
        TERACOTA_BACKUP_DIR="${BACKUP_DIR}" \
        bash "${staged_script}" "$@"
fi

trap cleanup EXIT
trap on_error ERR

[[ "${EUID}" -eq 0 ]] || fail "Run this updater with sudo."

for command in cmp curl flock git nginx runuser sqlite3 systemctl; do
    command -v "${command}" >/dev/null 2>&1 || fail "Required command is missing: ${command}"
done

[[ -d "${APP_DIR}/.git" ]] || fail "Git repository not found at ${APP_DIR}"
[[ -f "${APP_DIR}/.env" ]] || fail "Missing ${APP_DIR}/.env"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "Missing Python environment at ${APP_DIR}/.venv"
[[ -f "${SERVICE_SOURCE}" ]] || fail "Missing ${SERVICE_SOURCE}"
[[ -f "${IMPORT_SERVICE_SOURCE}" ]] || fail "Missing ${IMPORT_SERVICE_SOURCE}"
[[ -f "${IMPORT_TIMER_SOURCE}" ]] || fail "Missing ${IMPORT_TIMER_SOURCE}"
[[ -f "${NGINX_SOURCE}" ]] || fail "Missing ${NGINX_SOURCE}"

if [[ -z "${DB_PATH}" ]]; then
    DB_PATH="$(sed -n 's/^TERACOTA_DB_PATH=//p' "${APP_DIR}/.env" | tail -n 1)"
    DB_PATH="${DB_PATH%\"}"
    DB_PATH="${DB_PATH#\"}"
fi
DB_PATH="${DB_PATH:-/var/lib/teracota/teracota.sqlite3}"
[[ "${DB_PATH}" == /* ]] || fail "TERACOTA_DB_PATH must be an absolute path."

exec 9>/run/lock/teracota-update.lock
flock -n 9 || fail "Another TeraCota update is already running."

install -d -o "${APP_USER}" -g "${APP_GROUP}" -m 0750 "${BACKUP_DIR}"

git_status="$(runuser -u "${APP_USER}" -- git -C "${APP_DIR}" status --porcelain)"
if [[ -n "${git_status}" ]]; then
    printf '%s\n' "${git_status}" >&2
    fail "The server checkout has local changes. Resolve them before updating."
fi

previous_commit="$(runuser -u "${APP_USER}" -- git -C "${APP_DIR}" rev-parse --short HEAD)"
stamp="$(date +%Y%m%d-%H%M%S)"
database_backup=""

if [[ -f "${DB_PATH}" ]]; then
    database_backup="${BACKUP_DIR}/before-update-${stamp}.sqlite3"
    log "Backing up the SQLite database"
    runuser -u "${APP_USER}" -- sqlite3 "${DB_PATH}" ".backup '${database_backup}'"
fi

log "Pulling origin/${BRANCH}"
runuser -u "${APP_USER}" -- git -C "${APP_DIR}" fetch --prune origin "${BRANCH}"
runuser -u "${APP_USER}" -- git -C "${APP_DIR}" pull --ff-only origin "${BRANCH}"
current_commit="$(runuser -u "${APP_USER}" -- git -C "${APP_DIR}" rev-parse --short HEAD)"

# If this pull updated the updater itself, continue with the newly pulled
# version so path or deployment changes take effect in the current run.
if [[ "${TERACOTA_UPDATE_REEXECED:-0}" != "1" ]] && \
   ! cmp -s -- "$0" "${APP_DIR}/deploy/update_lightsail.sh"; then
    log "Continuing with the newly pulled updater"
    exec env \
        TERACOTA_UPDATE_STAGED=1 \
        TERACOTA_UPDATE_REEXECED=1 \
        TERACOTA_UPDATE_TEMP="${TERACOTA_UPDATE_TEMP:-}" \
        TERACOTA_APP_DIR="${APP_DIR}" \
        TERACOTA_APP_USER="${APP_USER}" \
        TERACOTA_APP_GROUP="${APP_GROUP}" \
        TERACOTA_BRANCH="${BRANCH}" \
        TERACOTA_DB_PATH="${DB_PATH}" \
        TERACOTA_BACKUP_DIR="${BACKUP_DIR}" \
        bash "${APP_DIR}/deploy/update_lightsail.sh" "$@"
fi

log "Installing Python dependencies"
runuser -u "${APP_USER}" -- \
    "${APP_DIR}/.venv/bin/python" -m pip install \
    --disable-pip-version-check \
    -r "${APP_DIR}/requirements.txt"

log "Checking Python source"
runuser -u "${APP_USER}" -- \
    "${APP_DIR}/.venv/bin/python" -m compileall -q \
    "${APP_DIR}/app.py" \
    "${APP_DIR}/wsgi.py" \
    "${APP_DIR}/gunicorn.conf.py" \
    "${APP_DIR}/measurements"

log "Updating the systemd service"
install -o root -g root -m 0644 "${SERVICE_SOURCE}" "${SERVICE_TARGET}"
install -o root -g root -m 0644 "${IMPORT_SERVICE_SOURCE}" "${IMPORT_SERVICE_TARGET}"
install -o root -g root -m 0644 "${IMPORT_TIMER_SOURCE}" "${IMPORT_TIMER_TARGET}"
systemctl daemon-reload
systemctl enable teracota >/dev/null
systemctl enable teracota-measurement-import.timer >/dev/null
systemctl restart teracota-measurement-import.timer

log "Checking the live Nginx configuration"
nginx_backup="${BACKUP_DIR}/nginx-before-update-${stamp}.conf"
if [[ -f "${NGINX_TARGET}" ]]; then
    cp -- "${NGINX_TARGET}" "${nginx_backup}"
else
    install -o root -g root -m 0644 "${NGINX_SOURCE}" "${NGINX_TARGET}"
    ln -sfn "${NGINX_TARGET}" /etc/nginx/sites-enabled/teracota
fi

upload_limit="$(awk '$1 == "client_max_body_size" { gsub(/;/, "", $2); print $2; exit }' "${NGINX_SOURCE}")"
[[ "${upload_limit}" =~ ^[0-9]+[kKmMgG]$ ]] || fail "Invalid client_max_body_size in ${NGINX_SOURCE}"
app_read_timeout="$(awk '$1 == "proxy_read_timeout" { gsub(/;/, "", $2); value=$2 } END { print value }' "${NGINX_SOURCE}")"
[[ "${app_read_timeout}" =~ ^[0-9]+s$ ]] || fail "Invalid application proxy_read_timeout in ${NGINX_SOURCE}"

if grep -Eq '^[[:space:]]*client_max_body_size[[:space:]]+' "${NGINX_TARGET}"; then
    sed -i -E \
        "s/^[[:space:]]*client_max_body_size[[:space:]]+[^;]+;/    client_max_body_size ${upload_limit};/" \
        "${NGINX_TARGET}"
else
    sed -i \
        "/server_name[[:space:]]\+teracota\.matoug\.com;/a\\    client_max_body_size ${upload_limit};" \
        "${NGINX_TARGET}"
fi

sed -i -E \
    "/^[[:space:]]*location[[:space:]]+\/[[:space:]]+\{/,/^[[:space:]]*\}/ s/^([[:space:]]*)proxy_read_timeout[[:space:]]+[^;]+;/\1proxy_read_timeout ${app_read_timeout};/" \
    "${NGINX_TARGET}"

if ! nginx -t; then
    if [[ -f "${nginx_backup}" ]]; then
        cp -- "${nginx_backup}" "${NGINX_TARGET}"
        nginx -t || true
    fi
    fail "Nginx validation failed; the previous live configuration was restored."
fi

log "Restarting TeraCota"
systemctl restart teracota

healthy=0
for _ in {1..18}; do
    # A connection refusal is expected briefly while Gunicorn binds its port.
    # Allow a modest response window on small instances under memory pressure.
    # Keep retries quiet; the failure path below prints the service journal.
    if curl --fail --silent --connect-timeout 2 --max-time 10 "${HEALTH_URL}" >/dev/null 2>&1; then
        healthy=1
        break
    fi
    sleep 2
done

if [[ "${healthy}" -ne 1 ]]; then
    journalctl -u teracota -n 60 --no-pager >&2 || true
    fail "TeraCota did not become healthy at ${HEALTH_URL}. Database backup: ${database_backup:-not created}"
fi

systemctl reload nginx

cat >/usr/local/sbin/update-teracota <<EOF
#!/bin/sh
exec bash "${APP_DIR}/deploy/update_lightsail.sh" "\$@"
EOF
chmod 0755 /usr/local/sbin/update-teracota

log "Update complete"
printf 'Code: %s -> %s\n' "${previous_commit}" "${current_commit}"
printf 'Health: OK (%s)\n' "${HEALTH_URL}"
if [[ -n "${database_backup}" ]]; then
    printf 'Database backup: %s\n' "${database_backup}"
fi
printf 'Next time, run: sudo update-teracota\n'
