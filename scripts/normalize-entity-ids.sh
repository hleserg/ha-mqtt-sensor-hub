#!/usr/bin/env bash
# =============================================================================
#  Give the YAML-declared MQTT entities short, stable entity ids.
# =============================================================================
#  Home Assistant builds an entity id from the device name plus the entity
#  name, so "Outdoor Weather Station" + "Outdoor Temperature" becomes
#  sensor.outdoor_weather_station_outdoor_temperature. Manually configured MQTT
#  entities cannot override that from YAML — `object_id` is a discovery-only
#  option and the integration rejects it outright.
#
#  The entity registry is the supported place to fix this, and the registry is
#  only writable over the WebSocket API. So this does exactly what renaming in
#  the UI does, except deterministically and repeatably.
#
#  The rule is simple: entity id becomes <domain>.<unique_id>, and every
#  unique_id in packages/ was chosen to be the entity id we want. So
#  sensor.outdoor_temperature, sensor.weather_state_rain_risk, and so on.
#
#  Only entities from this stack's own YAML are touched — the allow-list below
#  is matched against unique_id. Devices discovered over MQTT keep the ids
#  their discovery config gave them.
#
#    ./scripts/normalize-entity-ids.sh
#
#  Idempotent; safe to re-run after adding entities to packages/.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "no .env — run scripts/gen-secrets.sh first" >&2; exit 1; }
set -a; . ./.env; set +a

HA_URL="${HA_URL:-http://127.0.0.1:8123}"
CLIENT_ID="${HA_CLIENT_ID:-http://127.0.0.1:8123/}"
: "${HA_ADMIN_USER:?HA_ADMIN_USER missing from .env — run bootstrap-homeassistant.sh first}"
: "${HA_ADMIN_PASSWORD:?HA_ADMIN_PASSWORD missing from .env}"

echo "==> logging in as $HA_ADMIN_USER"
FLOW=$(curl -s --max-time 20 -X POST "$HA_URL/auth/login_flow" -H 'Content-Type: application/json' \
  -d "{\"client_id\":\"$CLIENT_ID\",\"handler\":[\"homeassistant\",null],\"redirect_uri\":\"$CLIENT_ID\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('flow_id',''))")
[ -n "$FLOW" ] || { echo "could not start a login flow" >&2; exit 1; }
CODE=$(CID="$CLIENT_ID" U="$HA_ADMIN_USER" P="$HA_ADMIN_PASSWORD" python3 -c "
import json,os,urllib.request
body=json.dumps({'client_id':os.environ['CID'],'username':os.environ['U'],'password':os.environ['P']}).encode()
req=urllib.request.Request('$HA_URL/auth/login_flow/$FLOW', data=body, headers={'Content-Type':'application/json'})
print(json.load(urllib.request.urlopen(req, timeout=20)).get('result',''))")
[ -n "$CODE" ] || { echo "login failed" >&2; exit 1; }
TOKEN=$(curl -s --max-time 20 -X POST "$HA_URL/auth/token" \
  -d grant_type=authorization_code -d "code=$CODE" -d "client_id=$CLIENT_ID" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")
[ -n "$TOKEN" ] || { echo "could not get an access token" >&2; exit 1; }

# Run inside the Home Assistant container: it already has aiohttp, and the
# host's python does not necessarily have a websocket client.
echo "==> renaming entities through the registry"
docker exec -i -e HA_TOKEN="$TOKEN" iot-homeassistant python - <<'PYEOF'
import asyncio, json, os, aiohttp

# unique_id prefixes belonging to this stack's own YAML packages.
OWNED = (
    "outdoor_", "weather_state_", "meshcore_mirror_",
    "rf_last_discovered", "rf_collector_status",
    "mqtt_broker_", "mqtt_round_trip_echo",
    # Backup status, published retained by scripts/backup.sh. These sit on the
    # shared "IoT Stack Health" device, so without normalization they land as
    # sensor.iot_stack_health_backup_* and the age template cannot find them.
    "backup_last_",
)

async def main():
    token = os.environ["HA_TOKEN"]
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect("http://127.0.0.1:8123/api/websocket") as ws:
            await ws.receive_json()                       # auth_required
            await ws.send_json({"type": "auth", "access_token": token})
            auth = await ws.receive_json()
            if auth.get("type") != "auth_ok":
                raise SystemExit(f"websocket auth failed: {auth}")

            mid = 1
            async def call(payload):
                nonlocal mid
                payload["id"] = mid; mid += 1
                await ws.send_json(payload)
                while True:
                    msg = await ws.receive_json()
                    if msg.get("id") == payload["id"]:
                        return msg

            # Home Assistant converts wind_speed to the metric system default
            # (km/h). The topic contract publishes m/s, and a dashboard reading
            # in different units than the broker is a quiet way to confuse
            # yourself later. Pin the display unit to match.
            UNITS = {
                "outdoor_wind_speed": "m/s",
                "outdoor_wind_gust": "m/s",
            }

            listing = await call({"type": "config/entity_registry/list"})
            entities = listing.get("result", [])

            taken = {e["entity_id"] for e in entities}
            renamed = skipped = retuned = 0

            for e in entities:
                uid = e.get("unique_id") or ""
                if e.get("platform") != "mqtt" or not uid.startswith(OWNED):
                    continue
                domain = e["entity_id"].split(".", 1)[0]
                want = f"{domain}.{uid}"
                if e["entity_id"] == want:
                    continue
                if want in taken:
                    print(f"  SKIP {e['entity_id']} -> {want} (already taken)")
                    skipped += 1
                    continue
                res = await call({
                    "type": "config/entity_registry/update",
                    "entity_id": e["entity_id"],
                    "new_entity_id": want,
                })
                if res.get("success"):
                    print(f"  {e['entity_id']}  ->  {want}")
                    taken.discard(e["entity_id"]); taken.add(want)
                    renamed += 1
                else:
                    print(f"  FAILED {e['entity_id']}: {res.get('error')}")
                    skipped += 1

            # Second pass: display units, after the ids have settled.
            for e in (await call({"type": "config/entity_registry/list"})).get("result", []):
                uid = e.get("unique_id") or ""
                if e.get("platform") != "mqtt" or uid not in UNITS:
                    continue
                want = UNITS[uid]
                current = (e.get("options") or {}).get("sensor", {}).get("unit_of_measurement")
                if current == want:
                    continue
                res = await call({
                    "type": "config/entity_registry/update",
                    "entity_id": e["entity_id"],
                    "options_domain": "sensor",
                    "options": {"unit_of_measurement": want},
                })
                if res.get("success"):
                    print(f"  {e['entity_id']}  display unit -> {want}")
                    retuned += 1
                else:
                    print(f"  FAILED unit on {e['entity_id']}: {res.get('error')}")

            print(f"\n  renamed {renamed}, units set {retuned}, skipped {skipped}")

asyncio.run(main())
PYEOF

echo
echo "==> done. Restart Home Assistant so templates re-resolve:"
echo "    docker compose restart homeassistant"
