#!/usr/bin/env bash
# =============================================================================
#  Simulated outdoor weather station.
# =============================================================================
#  Publishes the full weather/outdoor/# contract exactly the way a real sensor
#  node must: one retained message per metric, plus a measurement timestamp,
#  an availability LWT and a metadata blob.
#
#    ./scripts/test-weather-publisher.sh            # publish one sample
#    ./scripts/test-weather-publisher.sh --loop 30  # keep publishing every 30 s
#    ./scripts/test-weather-publisher.sh --offline  # mark the station offline
#    ./scripts/test-weather-publisher.sh --clear    # remove every retained value
#
#  Retained is the important part: a watch or a dashboard that connects a week
#  from now gets the last known values immediately, without waiting for the
#  next transmission.
#
#  It is also the part that outlives the simulation, which is what `--clear` is
#  for. Values left behind do not merely look stale -- the weather engine reads
#  them as real inputs, so a station that only measures temperature inherits a
#  wind speed from whenever this script last ran and derives wind chill from it
#  forever. `--offline` does not help: it flips availability and leaves every
#  value in place. Clear before a real node takes over the topics.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

HOST="${MQTT_HOST:-127.0.0.1}"
PORT="${MQTT_PORT:-1883}"
USER_NAME="${WEATHER_COLLECTOR_MQTT_USER:-weather_collector}"
PASSWORD="${WEATHER_COLLECTOR_MQTT_PASSWORD:?missing WEATHER_COLLECTOR_MQTT_PASSWORD}"
IMAGE="eclipse-mosquitto:2.0.22"
SENSOR_ID="${SENSOR_ID:-outdoor-sim-01}"

LOOP=0
OFFLINE=0
CLEAR=0
while [ $# -gt 0 ]; do
  case "$1" in
    --loop)    LOOP="${2:?--loop needs seconds}"; shift 2 ;;
    --offline) OFFLINE=1; shift ;;
    --clear)   CLEAR=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# One container, many publishes: far faster than a container per message.
pub_batch() {
  # stdin: lines of "<topic> <payload>". A line carrying only a topic publishes
  # a zero-length retained message, which is how MQTT deletes a retained value
  # -- there is no other way to take one back.
  docker run --rm -i --network host \
    -e H="$HOST" -e P="$PORT" -e U="$USER_NAME" -e W="$PASSWORD" \
    "$IMAGE" sh -c '
      while IFS=" " read -r topic payload; do
        [ -z "$topic" ] && continue
        if [ -z "$payload" ]; then
          mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" \
            -t "$topic" -n -q 1 -r
        else
          mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" \
            -t "$topic" -m "$payload" -q 1 -r
        fi
      done
    '
}

publish_clear() {
  # Ask the broker what is actually retained rather than clearing a hardcoded
  # list. The list would drift from publish_sample below, and it would miss
  # anything an older version of this script -- or a different publisher --
  # left behind.
  local topics
  topics=$(docker run --rm --network host \
    -e H="$HOST" -e P="$PORT" -e U="$USER_NAME" -e W="$PASSWORD" \
    "$IMAGE" sh -c '
      mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" \
        -t "weather/outdoor/#" --retained-only -W 3 -F "%t" 2>/dev/null' || true)

  if [ -z "$topics" ]; then
    echo "nothing retained under weather/outdoor/ — already clean"
    return 0
  fi

  printf '%s\n' "$topics" | pub_batch
  echo "cleared $(printf '%s\n' "$topics" | wc -l | tr -d ' ') retained topic(s):"
  printf '%s\n' "$topics" | sed 's/^/  /'
}

publish_offline() {
  printf "weather/outdoor/status offline\n" | pub_batch
  echo "published: weather/outdoor/status = offline (retained)"
}

publish_sample() {
  # A plausible, slowly drifting sample. Values are deliberately not random
  # noise — a flat-looking series makes it obvious when nothing is updating.
  local secs t h p dew wind gust rain_rate cloud lightning now
  secs=$(date +%s)
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  t=$(awk -v s="$secs" 'BEGIN { printf "%.1f", -3.5 + 4.0 * sin(s / 3600.0) }')
  h=$(awk -v s="$secs" 'BEGIN { printf "%.0f", 78 + 8 * sin(s / 2700.0) }')
  p=$(awk -v s="$secs" 'BEGIN { printf "%.1f", 1008.0 + 3.0 * sin(s / 21600.0) }')
  dew=$(awk -v t="$t" -v h="$h" 'BEGIN {
          # Magnus formula — the same one the real node should use.
          a = 17.62; b = 243.12;
          g = (a * t) / (b + t) + log(h / 100.0);
          printf "%.1f", (b * g) / (a - g) }')
  wind=$(awk -v s="$secs" 'BEGIN { printf "%.1f", 3.0 + 2.0 * sin(s / 900.0) }')
  gust=$(awk -v w="$wind" 'BEGIN { printf "%.1f", w * 1.7 }')
  rain_rate=$(awk -v s="$secs" 'BEGIN { v = 1.2 * sin(s / 5400.0); printf "%.1f", (v > 0 ? v : 0) }')
  cloud=$(awk -v s="$secs" 'BEGIN { printf "%.0f", 55 + 40 * sin(s / 4800.0) }')
  lightning=$(awk -v s="$secs" 'BEGIN { printf "%.0f", 18 + 6 * sin(s / 1800.0) }')

  # Rain is a cumulative counter (state_class: total_increasing), so it must
  # never go backwards between runs. Keep it in a small state file.
  local state_file=".weather-sim-rain"
  local rain_total
  rain_total=$(cat "$state_file" 2>/dev/null || echo "0.0")
  rain_total=$(awk -v r="$rain_total" -v rr="$rain_rate" 'BEGIN { printf "%.2f", r + rr / 120.0 }')
  echo "$rain_total" > "$state_file"

  pub_batch <<EOF
weather/outdoor/status online
weather/outdoor/temperature $t
weather/outdoor/humidity $h
weather/outdoor/pressure $p
weather/outdoor/dew_point $dew
weather/outdoor/wind_speed $wind
weather/outdoor/wind_gust $gust
weather/outdoor/rain $rain_total
weather/outdoor/rain_rate $rain_rate
weather/outdoor/cloud_index $cloud
weather/outdoor/lightning_distance $lightning
weather/outdoor/last_update $now
weather/outdoor/meta {"source":"test-publisher","sensor_id":"$SENSOR_ID","firmware":"sim-1.0","battery_pct":92}
EOF

  echo "$(date +%H:%M:%S)  t=${t}C  h=${h}%  p=${p}hPa  dew=${dew}C  wind=${wind}m/s  rain=${rain_total}mm"
}

if [ "$CLEAR" -eq 1 ]; then
  publish_clear
  exit 0
fi

if [ "$OFFLINE" -eq 1 ]; then
  publish_offline
  exit 0
fi

if [ "$LOOP" -gt 0 ]; then
  echo "publishing every ${LOOP}s to $HOST:$PORT as $USER_NAME — Ctrl-C to stop"
  trap 'echo; echo "stopped — station left online and every value retained."; echo "Run with --clear before a real node takes over these topics."; exit 0' INT TERM
  while true; do
    publish_sample
    sleep "$LOOP"
  done
else
  publish_sample
  echo
  echo "Read it back with:"
  echo "  ./scripts/mqtt-watch.sh 'weather/#'"
fi
