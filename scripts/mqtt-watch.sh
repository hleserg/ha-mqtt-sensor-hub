#!/usr/bin/env bash
# Watch MQTT traffic.
#
#   ./scripts/mqtt-watch.sh                 # everything (including $SYS)
#   ./scripts/mqtt-watch.sh 'weather/#'     # one namespace
#   ./scripts/mqtt-watch.sh 'weather/#' 5   # stop after 5 seconds
#
# Uses the monitor account, which by ACL may only read $SYS and monitor/#, so
# a wildcard here shows broker traffic but not telemetry. Pass USER=... and
# PASS=... to look at a namespace with an account allowed to read it, e.g.
#
#   USER=homeassistant PASS="$HA_MQTT_PASSWORD" ./scripts/mqtt-watch.sh 'weather/#'
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

HOST="${MQTT_HOST:-127.0.0.1}"
PORT="${MQTT_PORT:-1883}"
TOPIC="${1:-#}"
TIMEOUT="${2:-0}"
USER_NAME="${USER:-${HA_MQTT_USER:-homeassistant}}"
PASSWORD="${PASS:-${HA_MQTT_PASSWORD:?missing HA_MQTT_PASSWORD}}"

echo "subscribing to '$TOPIC' on $HOST:$PORT as $USER_NAME  (Ctrl-C to stop)"
echo "-----------------------------------------------------------------------"

docker run --rm -i --network host \
  -e H="$HOST" -e P="$PORT" -e U="$USER_NAME" -e W="$PASSWORD" \
  -e T="$TOPIC" -e TO="$TIMEOUT" \
  eclipse-mosquitto:2.0.22 sh -c '
    if [ "$TO" -gt 0 ] 2>/dev/null; then
      mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -v -W "$TO"
    else
      mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -v
    fi
  '
