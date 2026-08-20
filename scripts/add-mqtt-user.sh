#!/usr/bin/env bash
# Add or update one Mosquitto account.
#
#   ./scripts/add-mqtt-user.sh <username> [password]
#
# With no password one is generated and printed. Remember to give the new user
# rules in mosquitto/config/acl.conf — without them it can connect but is
# denied every topic.
set -euo pipefail

cd "$(dirname "$0")/.."
STACK_DIR="$PWD"
USERNAME="${1:?usage: add-mqtt-user.sh <username> [password]}"
PASSWORD="${2:-$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 28)}"
MOSQ_IMAGE="eclipse-mosquitto:2.0.22"

docker run --rm --user root \
  -v "$STACK_DIR/mosquitto/config:/work" \
  -e U="$USERNAME" -e P="$PASSWORD" \
  "$MOSQ_IMAGE" sh -c '
    set -e
    [ -f /work/passwd ] || : > /work/passwd
    mosquitto_passwd -b /work/passwd "$U" "$P"
    chown 1883:1000 /work/passwd
    chmod 0640      /work/passwd
  '

echo "user:     $USERNAME"
echo "password: $PASSWORD"
echo
echo "Next:"
echo "  1. add rules for '$USERNAME' to mosquitto/config/acl.conf"
echo "  2. docker compose exec mosquitto kill -HUP 1"
echo "  3. record the password in .env if a service needs it"
