#!/usr/bin/env bash
# =============================================================================
#  Backup the iot-stack.
# =============================================================================
#    ./scripts/backup.sh                  # config only, no credentials  (default)
#    ./scripts/backup.sh --with-db        # + the recorder database (weather history)
#    ./scripts/backup.sh --full           # + .env, mosquitto passwd, HA .storage
#    ./scripts/backup.sh --dest /path     # somewhere other than /mnt/backup/iot-stack
#
#  What the default archive contains:
#    * docker-compose.yml, .env.example, .gitignore, all documentation
#    * homeassistant/config: configuration.yaml, packages/, dashboards/,
#      automations, scripts, scenes, custom_components/
#    * mosquitto/config: mosquitto.conf, acl.conf   (NOT the password file)
#    * weather-engine/ (including config.yaml — it holds no credentials)
#    * scripts/ and firewall/
#
#  What it deliberately leaves out unless --full is given:
#    * .env                      — every MQTT password in plaintext
#    * mosquitto/config/passwd   — password hashes
#    * homeassistant/config/.storage/ — HA stores the MQTT broker password and
#      user credentials there in plaintext JSON
#
#  A --full archive is written chmod 600 and must be treated as a secret. See
#  BACKUP_RESTORE.md for what restoring each variant costs.
#
#  --full re-executes itself through sudo. Home Assistant writes five .storage
#  files as root with mode 600 (auth, auth_provider.homeassistant, http,
#  onboarding, core.uuid); an unprivileged tar aborts with exit 2 on the first
#  one, so the mode that exists to capture credentials cannot run without root.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
STACK_DIR="$PWD"

DEST="${BACKUP_DEST:-/mnt/backup/iot-stack}"
KEEP="${BACKUP_KEEP:-14}"
WITH_DB=0
FULL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --with-db) WITH_DB=1; shift ;;
    --full)    FULL=1; WITH_DB=1; shift ;;
    --dest)    DEST="${2:?--dest needs a path}"; shift 2 ;;
    --keep)    KEEP="${2:?--keep needs a number}"; shift 2 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# --- privilege -------------------------------------------------------------
# See the header: --full must read root-owned .storage files. Re-exec with
# resolved flags rather than forwarding the environment, so this works under a
# plain NOPASSWD sudoers rule with env_reset.
if [ "$FULL" -eq 1 ] && [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || {
    echo "--full needs root to read Home Assistant's .storage, and sudo is not available" >&2
    exit 1
  }
  echo "==> --full needs root to read Home Assistant's .storage; re-executing via sudo"
  exec sudo -- "$0" --full --dest "$DEST" --keep "$KEEP"
fi

# --- status reporting ------------------------------------------------------
# A scheduled backup that fails silently is worse than no backup at all: the
# first --full run aborted at the last step for weeks-invisible reasons. The
# outcome goes out as a retained MQTT message so Home Assistant can show its
# age and complain when it stops arriving. Uses the monitor account, which
# already owns monitor/# — this adds no new ACL surface.
STATUS_TOPIC="monitor/backup/status"
BYTES=0

publish_status() {
  local rc="$1"
  local env_file="$STACK_DIR/.env"
  [ -r "$env_file" ] || return 0
  local u w payload result
  u=$(sed -n 's/^MONITOR_MQTT_USER=//p'     "$env_file" | head -1)
  w=$(sed -n 's/^MONITOR_MQTT_PASSWORD=//p' "$env_file" | head -1)
  [ -n "$u" ] && [ -n "$w" ] || return 0
  result=ok; [ "$rc" -eq 0 ] || result=failed
  payload=$(printf '{"result":"%s","mode":"%s","archive":"%s","size_bytes":%s,"exit_code":%s,"finished_at":"%s"}'     "$result" "${SUFFIX:-unknown}" "$(basename "${ARCHIVE:-none}")" "${BYTES:-0}" "$rc" "$(date -Is)")
  timeout 20 docker run --rm --network host     -e U="$u" -e W="$w" -e T="$STATUS_TOPIC" -e M="$payload"     eclipse-mosquitto:2.0.22 sh -c     'mosquitto_pub -h 127.0.0.1 -p 1883 -u "$U" -P "$W" -t "$T" -m "$M" -q 1 -r'     >/dev/null 2>&1 || true
}
trap 'publish_status "$?"' EXIT

STAMP="$(date +%Y%m%d-%H%M%S)"
SUFFIX="config"
[ "$WITH_DB" -eq 1 ] && SUFFIX="config-db"
[ "$FULL" -eq 1 ]    && SUFFIX="full"
ARCHIVE="$DEST/iot-stack-$STAMP-$SUFFIX.tar.gz"

mkdir -p "$DEST"

EXCLUDES=(
  --exclude='./backups'
  --exclude='./mosquitto/log'
  --exclude='./mosquitto/data'
  --exclude='./homeassistant/config/home-assistant.log*'
  --exclude='./homeassistant/config/tts'
  --exclude='./homeassistant/config/deps'
  --exclude='./homeassistant/config/.cloud'
  --exclude='./homeassistant/config/backups'
  --exclude='./.weather-sim-rain'
  --exclude='./.git'
  --exclude='__pycache__'
  # Diagnostic scripts in tools/ are worth keeping; their virtualenv is not.
  # It is 1170 files of reinstallable dependencies and dominated the archive.
  --exclude='./tools/meshcore-venv'
)

if [ "$WITH_DB" -eq 0 ]; then
  EXCLUDES+=( --exclude='./homeassistant/config/home-assistant_v2.db*' )
fi

if [ "$FULL" -eq 0 ]; then
  EXCLUDES+=(
    --exclude='./.env'
    --exclude='./mosquitto/config/passwd'
    --exclude='./homeassistant/config/.storage'
    --exclude='./homeassistant/config/secrets.yaml'
  )
fi

echo "==> backing up $STACK_DIR"
echo "    mode:    $SUFFIX"
echo "    target:  $ARCHIVE"

# Flush Home Assistant's database to disk first so a --with-db archive is
# consistent rather than a torn snapshot of an open SQLite file.
if [ "$WITH_DB" -eq 1 ] && docker inspect iot-homeassistant >/dev/null 2>&1; then
  echo "==> asking Home Assistant to checkpoint its database"
  docker exec iot-homeassistant \
    python -c "import sqlite3,sys; c=sqlite3.connect('/config/home-assistant_v2.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()" \
    2>/dev/null || echo "    (checkpoint skipped — not fatal)"
fi

umask 077
tar czf "$ARCHIVE" -C "$STACK_DIR" "${EXCLUDES[@]}" . 2>/dev/null || {
  # tar returns 1 for "file changed as we read it", which is normal for a live
  # log or database. Only a hard failure should abort.
  rc=$?
  [ "$rc" -gt 1 ] && { echo "tar failed with $rc" >&2; exit "$rc"; }
  echo "    (tar reported files changing during read — expected on a live stack)"
}

if [ "$FULL" -eq 1 ]; then
  chmod 600 "$ARCHIVE"
  # Re-exec left us as root; a root-owned archive would need sudo just to copy
  # it off the box. Mode 600 is what protects it, not the owner uid.
  [ -n "${SUDO_UID:-}" ] && chown "$SUDO_UID:${SUDO_GID:-$SUDO_UID}" "$ARCHIVE"
  echo "    !! this archive contains plaintext credentials — chmod 600 applied"
else
  chmod 640 "$ARCHIVE"
fi

BYTES=$(stat -c%s "$ARCHIVE" 2>/dev/null || echo 0)
SIZE=$(du -h "$ARCHIVE" | cut -f1)
echo "==> wrote $ARCHIVE ($SIZE)"

# --- retention -------------------------------------------------------------
# Prune per mode, so a daily config backup can never push out the rarer full
# ones.
COUNT=$(find "$DEST" -maxdepth 1 -name "iot-stack-*-$SUFFIX.tar.gz" -type f | wc -l)
if [ "$COUNT" -gt "$KEEP" ]; then
  echo "==> pruning old '$SUFFIX' backups (keeping $KEEP of $COUNT)"
  find "$DEST" -maxdepth 1 -name "iot-stack-*-$SUFFIX.tar.gz" -type f -printf '%T@ %p\n' \
    | sort -n | head -n "$(( COUNT - KEEP ))" | cut -d' ' -f2- \
    | while read -r old; do echo "    rm $old"; rm -f "$old"; done
fi

echo "==> current backups in $DEST:"
ls -lh "$DEST" | tail -n +2 | awk '{printf "    %-46s %6s  %s %s %s\n", $9, $5, $6, $7, $8}'
