#!/usr/bin/env bash
# =============================================================================
#  Simulated RF/BLE sensor collector.
# =============================================================================
#  Stands in for the repurposed Tuya IR/RF remote so the broker, the ACL, the
#  allow-list flow and the Home Assistant discovery path can all be exercised
#  before the real firmware exists.
#
#    ./scripts/sim-rf-collector.sh online
#    ./scripts/sim-rf-collector.sh announce 42A7
#    ./scripts/sim-rf-collector.sh discovery 42A7 "Garden 433"
#    ./scripts/sim-rf-collector.sh publish 42A7
#    ./scripts/sim-rf-collector.sh check-allowlist
#
#  Typical order: `online` -> `announce` (device shows up in the External
#  Sensors view) -> allow-list it from Home Assistant -> `discovery` -> and from
#  then on `publish` for every received packet.
#
#  Everything the real device must do is here and nowhere else: no server-side
#  component is involved in adding a sensor.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

HOST="${MQTT_HOST:-127.0.0.1}"
PORT="${MQTT_PORT:-1883}"
USER_NAME="${RF_COLLECTOR_MQTT_USER:-rf_collector}"
PASSWORD="${RF_COLLECTOR_MQTT_PASSWORD:?missing RF_COLLECTOR_MQTT_PASSWORD}"
IMAGE="eclipse-mosquitto:2.0.22"

COLLECTOR="${COLLECTOR:-rf433}"
DISCOVERY_PREFIX="${DISCOVERY_PREFIX:-homeassistant}"

pub() { # pub <topic> <payload> <retain 0|1>
  docker run --rm --network host \
    -e H="$HOST" -e P="$PORT" -e U="$USER_NAME" -e W="$PASSWORD" \
    -e T="$1" -e M="$2" -e R="$3" \
    "$IMAGE" sh -c '
      if [ "$R" = "1" ]; then
        mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -m "$M" -q 1 -r
      else
        mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -m "$M" -q 1
      fi
    '
}

cmd_online() {
  pub "sensors/$COLLECTOR/status" "online" 1
  echo "sensors/$COLLECTOR/status = online (retained)"
}

cmd_offline() {
  pub "sensors/$COLLECTOR/status" "offline" 1
  echo "sensors/$COLLECTOR/status = offline (retained)"
}

# Stage 1: "I can hear this device." Not retained — it describes the air right
# now, and a retained announcement would resurrect long-gone devices.
cmd_announce() {
  local id="${1:?usage: announce <device_id>}"
  local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local payload
  payload=$(cat <<JSON
{"source":"rf433","protocol":"example_weather","device_id":"$id","temperature_c":-3.8,"humidity_pct":84,"rssi":-71,"battery_low":false,"timestamp":"$ts"}
JSON
)
  pub "sensors/$COLLECTOR/_discovered/$id" "$payload" 0
  echo "announced $id (not retained) — check 'RF Last Discovered' in Home Assistant"
}

# Stage 2: the device was allow-listed, so publish MQTT Discovery configs.
# Retained, because Home Assistant must find them again after a restart.
cmd_discovery() {
  local id="${1:?usage: discovery <device_id> [alias]}"
  local alias="${2:-RF $id}"
  local base="sensors/$COLLECTOR/$id"
  local dev="{\"identifiers\":[\"${COLLECTOR}_${id}\"],\"name\":\"$alias\",\"manufacturer\":\"third-party\",\"model\":\"example_weather (433 MHz)\",\"via_device\":\"rf_ble_collector\"}"
  local avail="\"availability_topic\":\"sensors/$COLLECTOR/status\",\"payload_available\":\"online\",\"payload_not_available\":\"offline\""

  # expire_after makes an external sensor go unavailable when it stops
  # transmitting. That is correct here and wrong for my own station: these are
  # disposable, and a silently frozen third-party value is worse than a gap.
  pub "$DISCOVERY_PREFIX/sensor/${COLLECTOR}_${id}/temperature/config" \
    "{\"name\":\"Temperature\",\"unique_id\":\"${COLLECTOR}_${id}_temperature\",\"object_id\":\"${COLLECTOR}_${id}_temperature\",\"state_topic\":\"$base/temperature\",\"unit_of_measurement\":\"°C\",\"device_class\":\"temperature\",\"state_class\":\"measurement\",\"expire_after\":3600,$avail,\"device\":$dev}" 1

  pub "$DISCOVERY_PREFIX/sensor/${COLLECTOR}_${id}/humidity/config" \
    "{\"name\":\"Humidity\",\"unique_id\":\"${COLLECTOR}_${id}_humidity\",\"object_id\":\"${COLLECTOR}_${id}_humidity\",\"state_topic\":\"$base/humidity\",\"unit_of_measurement\":\"%\",\"device_class\":\"humidity\",\"state_class\":\"measurement\",\"expire_after\":3600,$avail,\"device\":$dev}" 1

  pub "$DISCOVERY_PREFIX/sensor/${COLLECTOR}_${id}/rssi/config" \
    "{\"name\":\"RSSI\",\"unique_id\":\"${COLLECTOR}_${id}_rssi\",\"object_id\":\"${COLLECTOR}_${id}_rssi\",\"state_topic\":\"$base/rssi\",\"unit_of_measurement\":\"dBm\",\"device_class\":\"signal_strength\",\"state_class\":\"measurement\",\"entity_category\":\"diagnostic\",$avail,\"device\":$dev}" 1

  pub "$DISCOVERY_PREFIX/binary_sensor/${COLLECTOR}_${id}/battery_low/config" \
    "{\"name\":\"Battery low\",\"unique_id\":\"${COLLECTOR}_${id}_battery_low\",\"object_id\":\"${COLLECTOR}_${id}_battery_low\",\"state_topic\":\"$base/battery_low\",\"payload_on\":\"true\",\"payload_off\":\"false\",\"device_class\":\"battery\",\"entity_category\":\"diagnostic\",$avail,\"device\":$dev}" 1

  pub "$DISCOVERY_PREFIX/sensor/${COLLECTOR}_${id}/last_seen/config" \
    "{\"name\":\"Last seen\",\"unique_id\":\"${COLLECTOR}_${id}_last_seen\",\"object_id\":\"${COLLECTOR}_${id}_last_seen\",\"state_topic\":\"$base/last_seen\",\"device_class\":\"timestamp\",\"entity_category\":\"diagnostic\",\"device\":$dev}" 1

  echo "published 5 discovery configs for ${COLLECTOR}_${id} under $DISCOVERY_PREFIX/ (retained)"
  echo "the device appears in Home Assistant within a second or two"
}

# Remove a device from Home Assistant entirely: empty retained payload on each
# config topic deletes it, rather than leaving an orphaned entity behind.
cmd_undiscovery() {
  local id="${1:?usage: undiscovery <device_id>}"
  for t in "sensor/${COLLECTOR}_${id}/temperature" "sensor/${COLLECTOR}_${id}/humidity" \
           "sensor/${COLLECTOR}_${id}/rssi" "binary_sensor/${COLLECTOR}_${id}/battery_low" \
           "sensor/${COLLECTOR}_${id}/last_seen"; do
    pub "$DISCOVERY_PREFIX/$t/config" "" 1
  done
  echo "removed discovery configs for ${COLLECTOR}_${id}"
}

# Stage 3: normal operation. Retained per-metric state + a non-retained raw
# event carrying the whole decoded packet.
cmd_publish() {
  local id="${1:?usage: publish <device_id>}"
  local base="sensors/$COLLECTOR/$id"
  local ts; ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local secs; secs=$(date +%s)
  local t h rssi
  t=$(awk -v s="$secs" 'BEGIN { printf "%.1f", -3.8 + 3.0 * sin(s / 1800.0) }')
  h=$(awk -v s="$secs" 'BEGIN { printf "%.0f", 84 + 6 * sin(s / 2400.0) }')
  rssi=$(awk -v s="$secs" 'BEGIN { printf "%.0f", -71 + 6 * sin(s / 600.0) }')

  pub "$base/temperature" "$t"    1
  pub "$base/humidity"    "$h"    1
  pub "$base/rssi"        "$rssi" 1
  pub "$base/battery_low" "false" 1
  pub "$base/last_seen"   "$ts"   1

  pub "$base/event" \
    "{\"source\":\"rf433\",\"protocol\":\"example_weather\",\"device_id\":\"$id\",\"temperature_c\":$t,\"humidity_pct\":$h,\"rssi\":$rssi,\"battery_low\":false,\"timestamp\":\"$ts\"}" 0

  echo "$id: t=${t}C h=${h}% rssi=${rssi}dBm (state retained, event not retained)"
}

# What has Home Assistant allow-listed? Reads the retained command topics back.
cmd_check_allowlist() {
  echo "retained allow-list entries under sensors/$COLLECTOR/cmd/enable/:"
  docker run --rm --network host \
    -e H="$HOST" -e P="$PORT" -e U="$USER_NAME" -e W="$PASSWORD" -e C="$COLLECTOR" \
    "$IMAGE" sh -c '
      mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" \
        -t "sensors/$C/cmd/enable/#" -v -W 3 2>/dev/null
    ' || true
  echo "(nothing above means no device has been enabled yet)"
}

case "${1:-}" in
  online)          cmd_online ;;
  offline)         cmd_offline ;;
  announce)        shift; cmd_announce "$@" ;;
  discovery)       shift; cmd_discovery "$@" ;;
  undiscovery)     shift; cmd_undiscovery "$@" ;;
  publish)         shift; cmd_publish "$@" ;;
  check-allowlist) cmd_check_allowlist ;;
  *) sed -n '2,25p' "$0"; exit 2 ;;
esac
