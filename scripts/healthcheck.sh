#!/usr/bin/env bash
# =============================================================================
#  Stack health check.
# =============================================================================
#  Checks the six things that actually break:
#    1. Mosquitto alive and accepting authenticated connections
#    2. Home Assistant answering on 8123
#    3. disk usage on doctor
#    4. container restart loops
#    5. MQTT round-trip: publish -> broker -> subscribe
#    6. weather-engine reporting itself online on the bus
#
#  Exit code 0 = all good, 1 = at least one failure. Safe to run from cron or
#  to point uptime-kuma at.
#
#    ./scripts/healthcheck.sh
#    ./scripts/healthcheck.sh --quiet     # only print failures
# =============================================================================
set -uo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

HOST="${MQTT_HOST:-127.0.0.1}"
PORT="${MQTT_PORT:-1883}"
HA_URL="${HA_URL:-http://127.0.0.1:8123}"
DISK_WARN_PCT="${DISK_WARN_PCT:-85}"
IMAGE="eclipse-mosquitto:2.0.22"
QUIET=0
[ "${1:-}" = "--quiet" ] && QUIET=1

FAILED=0
ok()   { [ "$QUIET" -eq 1 ] || printf '  \033[32mOK\033[0m    %s\n' "$*"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAILED=1; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; }

[ "$QUIET" -eq 1 ] || echo "iot-stack health  —  $(date -Iseconds)"

# --- 1. broker alive -------------------------------------------------------
uptime_raw=$(docker run --rm --network host \
  -e H="$HOST" -e P="$PORT" -e U="$MONITOR_MQTT_USER" -e W="$MONITOR_MQTT_PASSWORD" \
  "$IMAGE" sh -c 'mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t "\$SYS/broker/uptime" -C 1 -W 5' 2>/dev/null)
if [ -n "$uptime_raw" ]; then
  ok "mosquitto alive (uptime: $uptime_raw)"
else
  fail "mosquitto did not answer an authenticated \$SYS subscribe"
fi

# --- 2. home assistant alive ----------------------------------------------
ha_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HA_URL/manifest.json" 2>/dev/null)
if [ "$ha_code" = "200" ]; then
  ok "home assistant answering on $HA_URL"
else
  fail "home assistant returned HTTP '$ha_code' on $HA_URL/manifest.json"
fi

# --- 3. disk ---------------------------------------------------------------
disk_pct=$(df -P / | awk 'NR==2 {gsub(/%/,"",$5); print $5}')
disk_free=$(df -Ph / | awk 'NR==2 {print $4}')
if [ "${disk_pct:-100}" -lt "$DISK_WARN_PCT" ]; then
  ok "disk ${disk_pct}% used, ${disk_free} free"
else
  fail "disk ${disk_pct}% used (threshold ${DISK_WARN_PCT}%), only ${disk_free} free"
fi

# --- 4. restart loops ------------------------------------------------------
# A container that keeps dying is usually still "Up" a second later, so status
# alone lies. RestartCount over a short uptime is the honest signal.
for c in iot-mosquitto iot-homeassistant iot-weather-engine; do
  if ! docker inspect "$c" >/dev/null 2>&1; then
    fail "$c does not exist"
    continue
  fi
  running=$(docker inspect -f '{{.State.Running}}' "$c")
  restarts=$(docker inspect -f '{{.RestartCount}}' "$c")
  started=$(docker inspect -f '{{.State.StartedAt}}' "$c")
  started_epoch=$(date -d "$started" +%s 2>/dev/null || echo 0)
  age=$(( $(date +%s) - started_epoch ))

  if [ "$running" != "true" ]; then
    fail "$c is not running"
  elif [ "$restarts" -gt 3 ] && [ "$age" -lt 600 ]; then
    fail "$c looks like a restart loop: $restarts restarts, up only ${age}s"
  elif [ "$restarts" -gt 0 ]; then
    warn "$c running (${age}s), but has restarted $restarts time(s)"
  else
    ok "$c running (${age}s, $restarts restarts)"
  fi

  health=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c")
  [ "$health" = "unhealthy" ] && fail "$c healthcheck reports unhealthy"
done

# --- 5. mqtt round trip ----------------------------------------------------
# Subscriber first, then publish, then confirm the exact payload came back.
token="healthcheck-$(date +%s)-$$"
roundtrip=$(docker run --rm --network host \
  -e H="$HOST" -e P="$PORT" -e U="$MONITOR_MQTT_USER" -e W="$MONITOR_MQTT_PASSWORD" -e TOK="$token" \
  "$IMAGE" sh -c '
    mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t monitor/healthcheck -C 1 -W 8 > /tmp/out &
    sub=$!
    sleep 1
    mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" -t monitor/healthcheck -m "$TOK" -q 1
    wait $sub 2>/dev/null
    cat /tmp/out
  ' 2>/dev/null)
if [ "$roundtrip" = "$token" ]; then
  ok "mqtt round-trip publish -> broker -> subscribe"
else
  fail "mqtt round-trip failed (sent '$token', got '${roundtrip:-nothing}')"
fi

# --- 6. weather engine -----------------------------------------------------
# Asks the bus, not Docker. `engine_status` is the container's own last will, so
# a process that is up but wedged, or one that lost the broker, reads `offline`
# here while `docker ps` still looks fine.
engine_status=$(docker run --rm --network host   -e H="$HOST" -e P="$PORT" -e U="$MONITOR_MQTT_USER" -e W="$MONITOR_MQTT_PASSWORD"   "$IMAGE" sh -c 'mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t weather_state/engine_status -C 1 -W 5' 2>/dev/null)
case "${engine_status:-}" in
  online)  ok "weather-engine reports online on weather_state/engine_status" ;;
  offline) fail "weather-engine reports offline (last will fired, or stopped)" ;;
  *)       fail "weather-engine has never published engine_status" ;;
esac

[ "$QUIET" -eq 1 ] || echo
if [ "$FAILED" -eq 0 ]; then
  [ "$QUIET" -eq 1 ] || echo "all checks passed"
else
  echo "one or more checks FAILED" >&2
fi
exit "$FAILED"
