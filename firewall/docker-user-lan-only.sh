#!/usr/bin/env bash
# =============================================================================
#  Restrict the published MQTT port to the LAN.
# =============================================================================
#  Docker publishes ports by writing its own iptables rules in the FORWARD
#  path, which means `ufw allow/deny` has no effect on them at all. The
#  supported hook for host-side policy is the DOCKER-USER chain, so that is
#  where this lives.
#
#  Everything is confined to a dedicated IOT-MQTT-LAN chain plus one jump, so
#  it can be removed cleanly and cannot disturb the rules Docker or the other
#  projects on doctor rely on.
#
#  To let another network reach the broker — a WireGuard client range, say —
#  add its CIDR to firewall/allowed-subnets.conf and re-run `apply`. The file
#  is read at apply time, so the systemd unit picks it up on the next boot too.
#  Do not open 1883 to anything that is not authenticated at the network layer.
#
#    sudo ./docker-user-lan-only.sh apply
#    sudo ./docker-user-lan-only.sh status
#    sudo ./docker-user-lan-only.sh remove
#
#  Installed persistently as iot-stack-firewall.service.
# =============================================================================
set -euo pipefail

CHAIN="IOT-MQTT-LAN"
MQTT_PORT="${MQTT_PORT:-1883}"
LAN_SUBNET="${LAN_SUBNET:-192.168.1.0/24}"
DOCKER_SUBNET="${DOCKER_SUBNET:-172.28.0.0/24}"

# Optional additional sources, one CIDR per line, '#' comments allowed. This is
# the supported way to admit a VPN client range without editing the script.
EXTRA_CONF="$(dirname "$0")/allowed-subnets.conf"
EXTRA_SUBNETS=()
if [ -r "$EXTRA_CONF" ]; then
  while read -r line; do
    line="${line%%#*}"; line="${line// /}"
    [ -n "$line" ] && EXTRA_SUBNETS+=("$line")
  done < "$EXTRA_CONF"
fi

[ "$(id -u)" -eq 0 ] || { echo "must run as root (use sudo)" >&2; exit 1; }

apply() {
  iptables -N "$CHAIN" 2>/dev/null || true
  iptables -F "$CHAIN"

  # Allowed sources for the broker.
  iptables -A "$CHAIN" -p tcp --dport "$MQTT_PORT" -s "$LAN_SUBNET"    -j RETURN
  iptables -A "$CHAIN" -p tcp --dport "$MQTT_PORT" -s "$DOCKER_SUBNET" -j RETURN
  iptables -A "$CHAIN" -p tcp --dport "$MQTT_PORT" -s 127.0.0.0/8      -j RETURN
  for extra in ${EXTRA_SUBNETS+"${EXTRA_SUBNETS[@]}"}; do
    iptables -A "$CHAIN" -p tcp --dport "$MQTT_PORT" -s "$extra" -j RETURN
    echo "  also allowing $extra (from $(basename "$EXTRA_CONF"))"
  done
  # Anything else reaching 1883 is dropped — no MQTT from outside the LAN.
  iptables -A "$CHAIN" -p tcp --dport "$MQTT_PORT" -j DROP

  # Hook it in exactly once, as the first rule of DOCKER-USER.
  if ! iptables -C DOCKER-USER -j "$CHAIN" 2>/dev/null; then
    iptables -I DOCKER-USER 1 -j "$CHAIN"
  fi
  echo "applied: tcp/$MQTT_PORT reachable only from $LAN_SUBNET, $DOCKER_SUBNET, localhost${EXTRA_SUBNETS+ and ${EXTRA_SUBNETS[*]}}"
}

remove() {
  while iptables -C DOCKER-USER -j "$CHAIN" 2>/dev/null; do
    iptables -D DOCKER-USER -j "$CHAIN"
  done
  iptables -F "$CHAIN" 2>/dev/null || true
  iptables -X "$CHAIN" 2>/dev/null || true
  echo "removed"
}

status() {
  echo "--- DOCKER-USER ---"
  iptables -S DOCKER-USER 2>/dev/null || echo "(chain missing)"
  echo "--- $CHAIN ---"
  iptables -S "$CHAIN" 2>/dev/null || echo "(chain missing)"
}

case "${1:-status}" in
  apply)  apply ;;
  remove) remove ;;
  status) status ;;
  *) echo "usage: $0 {apply|remove|status}" >&2; exit 2 ;;
esac
