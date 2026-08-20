#!/usr/bin/env bash
# =============================================================================
#  First-run setup for Home Assistant.
# =============================================================================
#  Does the two things that cannot be expressed in configuration.yaml:
#
#    1. creates the owner account (onboarding)
#    2. adds the MQTT integration, pointed at the local broker
#
#  The MQTT broker connection is a *config entry*, not YAML — Home Assistant
#  keeps it in .storage and offers no YAML equivalent. Doing it over the API
#  here means the whole stack is reproducible rather than depending on someone
#  remembering to click through a wizard.
#
#    ./scripts/bootstrap-homeassistant.sh
#
#  Idempotent: if onboarding is already done it skips to the MQTT step, and if
#  MQTT is already configured it does nothing. The owner credentials are
#  generated once and stored in .env as HA_ADMIN_USER / HA_ADMIN_PASSWORD.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
STACK_DIR="$PWD"
ENV_FILE="$STACK_DIR/.env"
[ -f "$ENV_FILE" ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . "$ENV_FILE"; set +a

HA_URL="${HA_URL:-http://127.0.0.1:8123}"
CLIENT_ID="${HA_CLIENT_ID:-http://127.0.0.1:8123/}"
MQTT_BROKER="${MQTT_BROKER_FOR_HA:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"

# Read one field out of a JSON document on stdin.
jget() { python3 -c "import sys,json
try: d=json.load(sys.stdin)
except Exception: sys.exit(0)
$1"; }

# `tr </dev/urandom | head` dies of SIGPIPE under `set -o pipefail`, which is a
# very confusing way for this script to fail. Python is already a dependency.
randpw() {
  python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(20)))"
}

# --- wait for Home Assistant ----------------------------------------------
echo "==> waiting for Home Assistant at $HA_URL"
for i in $(seq 1 60); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HA_URL/manifest.json" || true)
  [ "$code" = "200" ] && { echo "    up"; break; }
  [ "$i" = "60" ] && { echo "    Home Assistant never came up" >&2; exit 1; }
  sleep 5
done

# --- 1. onboarding ---------------------------------------------------------
AUTH_CODE=""
onboarding=$(curl -s --max-time 10 "$HA_URL/api/onboarding" || echo '[]')
user_done=$(echo "$onboarding" | jget "print('yes' if any(s.get('step')=='user' and s.get('done') for s in d) else 'no')")

if [ "$user_done" = "yes" ]; then
  echo "==> onboarding already complete"
  if [ -z "${HA_ADMIN_PASSWORD:-}" ]; then
    echo "    .env has no HA_ADMIN_PASSWORD, so this script cannot log in." >&2
    echo "    Add the MQTT integration from the UI instead — see README.md." >&2
    exit 1
  fi
else
  echo "==> creating the owner account"
  if [ -z "${HA_ADMIN_USER:-}" ]; then
    HA_ADMIN_USER="sergey"
    HA_ADMIN_PASSWORD="$(randpw)"
    {
      printf '\n# Home Assistant owner account, created by bootstrap-homeassistant.sh\n'
      printf 'HA_ADMIN_USER=%s\n' "$HA_ADMIN_USER"
      printf 'HA_ADMIN_PASSWORD=%s\n' "$HA_ADMIN_PASSWORD"
    } >> "$ENV_FILE"
    echo "    generated credentials and appended them to .env"
  fi

  body=$(CLIENT_ID="$CLIENT_ID" U="$HA_ADMIN_USER" P="$HA_ADMIN_PASSWORD" python3 -c "
import json,os
print(json.dumps({'client_id':os.environ['CLIENT_ID'],'name':'Sergey',
                  'username':os.environ['U'],'password':os.environ['P'],'language':'en'}))")
  resp=$(curl -s --max-time 60 -X POST "$HA_URL/api/onboarding/users" \
    -H 'Content-Type: application/json' -d "$body")
  AUTH_CODE=$(echo "$resp" | jget "print(d.get('auth_code',''))")
  [ -n "$AUTH_CODE" ] || { echo "    failed to create the user: $resp" >&2; exit 1; }
  echo "    owner account '$HA_ADMIN_USER' created"
fi

# --- get an access token ---------------------------------------------------
if [ -n "$AUTH_CODE" ]; then
  CODE="$AUTH_CODE"
else
  echo "==> logging in as $HA_ADMIN_USER"
  flow=$(curl -s --max-time 20 -X POST "$HA_URL/auth/login_flow" \
    -H 'Content-Type: application/json' \
    -d "{\"client_id\":\"$CLIENT_ID\",\"handler\":[\"homeassistant\",null],\"redirect_uri\":\"$CLIENT_ID\"}")
  FLOW_ID=$(echo "$flow" | jget "print(d.get('flow_id',''))")
  [ -n "$FLOW_ID" ] || { echo "    could not start a login flow: $flow" >&2; exit 1; }
  lbody=$(CLIENT_ID="$CLIENT_ID" U="$HA_ADMIN_USER" P="$HA_ADMIN_PASSWORD" python3 -c "
import json,os
print(json.dumps({'client_id':os.environ['CLIENT_ID'],
                  'username':os.environ['U'],'password':os.environ['P']}))")
  res=$(curl -s --max-time 20 -X POST "$HA_URL/auth/login_flow/$FLOW_ID" \
    -H 'Content-Type: application/json' -d "$lbody")
  CODE=$(echo "$res" | jget "print(d.get('result',''))")
  [ -n "$CODE" ] || { echo "    login failed: $res" >&2; exit 1; }
fi

TOKEN=$(curl -s --max-time 20 -X POST "$HA_URL/auth/token" \
  -d "grant_type=authorization_code" -d "code=$CODE" -d "client_id=$CLIENT_ID" \
  | jget "print(d.get('access_token',''))")
[ -n "${TOKEN:-}" ] || { echo "could not obtain an access token" >&2; exit 1; }
AUTH="Authorization: Bearer $TOKEN"
echo "==> authenticated"

# --- finish the remaining onboarding steps --------------------------------
# Harmless if already done; each just returns an error we ignore.
curl -s --max-time 30 -o /dev/null -X POST "$HA_URL/api/onboarding/core_config" \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{}' || true
curl -s --max-time 30 -o /dev/null -X POST "$HA_URL/api/onboarding/analytics" \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{}' || true
curl -s --max-time 30 -o /dev/null -X POST "$HA_URL/api/onboarding/integration" \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"client_id\":\"$CLIENT_ID\",\"redirect_uri\":\"$CLIENT_ID\"}" || true

# --- 2. the MQTT config entry ---------------------------------------------
existing=$(curl -s --max-time 20 "$HA_URL/api/config/config_entries/entry" -H "$AUTH" \
  | jget "print(len([e for e in d if e.get('domain')=='mqtt']))")

if [ -n "$existing" ] && [ "$existing" != "0" ]; then
  echo "==> MQTT integration already configured ($existing entry)"
else
  echo "==> adding the MQTT integration -> $MQTT_BROKER:$MQTT_PORT as $HA_MQTT_USER"
  flow=$(curl -s --max-time 30 -X POST "$HA_URL/api/config/config_entries/flow" \
    -H "$AUTH" -H 'Content-Type: application/json' \
    -d '{"handler":"mqtt","show_advanced_options":true}')
  FLOW_ID=$(echo "$flow" | jget "print(d.get('flow_id',''))")
  STEP=$(echo "$flow" | jget "print(d.get('step_id',''))")
  [ -n "$FLOW_ID" ] || { echo "    could not start the MQTT config flow: $flow" >&2; exit 1; }
  echo "    flow $FLOW_ID, step '$STEP'"

  # The 2026.x flow nests the advanced fields in a required `other_settings`
  # section; omitting it fails with "required key not provided".
  mbody=$(B="$MQTT_BROKER" PT="$MQTT_PORT" U="$HA_MQTT_USER" P="$HA_MQTT_PASSWORD" python3 -c "
import json,os
print(json.dumps({
  'broker':   os.environ['B'],
  'port':     int(os.environ['PT']),
  'protocol': '5',
  'username': os.environ['U'],
  'password': os.environ['P'],
  'other_settings': {
      'set_client_cert': False,
      'set_ca_cert':     'off',
      'transport':       'tcp',
      'keepalive':       60,
  },
}))")
  result=$(curl -s --max-time 90 -X POST "$HA_URL/api/config/config_entries/flow/$FLOW_ID" \
    -H "$AUTH" -H 'Content-Type: application/json' -d "$mbody")

  TYPE=$(echo "$result" | jget "print(d.get('type',''))")
  if [ "$TYPE" = "create_entry" ]; then
    echo "    MQTT integration created"
  else
    echo "    MQTT config flow did not complete:" >&2
    echo "$result" >&2
    echo >&2
    echo "    Add it by hand: Settings > Devices & Services > + Add Integration > MQTT" >&2
    echo "    broker $MQTT_BROKER, port $MQTT_PORT, user $HA_MQTT_USER, password from .env" >&2
    exit 1
  fi
fi

echo
echo "==> done"
echo "    Home Assistant: http://192.168.1.51:8123"
echo "    user:           ${HA_ADMIN_USER}"
echo "    password:       in .env as HA_ADMIN_PASSWORD"
