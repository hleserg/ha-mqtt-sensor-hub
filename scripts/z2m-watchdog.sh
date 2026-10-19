#!/usr/bin/env bash
# =============================================================================
#  Zigbee2MQTT watchdog.
# =============================================================================
#  Restarts the bridge when it reports itself offline on the bus.
#
#  Why this exists: when the coordinator stops answering (ASH_NCP_FATAL_ERROR),
#  Zigbee2MQTT logs "Adapter disconnected, stopping" and then can hang on the
#  way out instead of exiting. The container stays "Up", so `restart:
#  unless-stopped` never fires, while the last will has already put `offline`
#  on zigbee2mqtt/bridge/state. On 2026-10-18 that left the house without
#  Zigbee for 23 hours. A plain restart brings the adapter back.
#
#  Never restarts a container that started less than MIN_AGE seconds ago, so a
#  stick that is really gone costs one restart per MIN_AGE, not a loop.
#
#    ./scripts/z2m-watchdog.sh            # from cron, see cron/iot-stack-z2m-watchdog
# =============================================================================
set -uo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

HOST="${MQTT_HOST:-127.0.0.1}"
PORT="${MQTT_PORT:-1883}"
IMAGE="eclipse-mosquitto:2.0.22"
CONTAINER="iot-zigbee2mqtt"
MIN_AGE="${MIN_AGE:-600}"

log() { echo "$(date -Iseconds) z2m-watchdog: $*"; }

running=$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)
# A container stopped on purpose (docker compose stop) is left alone.
[ "$running" = "true" ] || exit 0

started=$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER")
age=$(( $(date +%s) - $(date -d "$started" +%s 2>/dev/null || echo 0) ))
[ "$age" -ge "$MIN_AGE" ] || exit 0

state=$(docker run --rm --network host \
  -e H="$HOST" -e P="$PORT" -e U="$MONITOR_MQTT_USER" -e W="$MONITOR_MQTT_PASSWORD" \
  "$IMAGE" sh -c 'mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t zigbee2mqtt/bridge/state -C 1 -W 5' 2>/dev/null)

case "${state:-}" in
  *'"online"'*) exit 0 ;;
  *'"offline"'*)
    log "bridge reports offline while container is up ${age}s — restarting"
    docker restart -t 30 "$CONTAINER" >/dev/null && log "restarted" || log "restart FAILED"
    ;;
  *)
    # Broker unreachable or no retained state: not something a bridge restart fixes.
    log "could not read bridge/state (got '${state:-nothing}'), leaving it alone"
    ;;
esac
