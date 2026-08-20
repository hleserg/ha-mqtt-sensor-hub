#!/usr/bin/env bash
# =============================================================================
#  Remove the entity debris MeshCore leaves in the registry.
# =============================================================================
#  Two different kinds of junk, both of which make the registry lie about what
#  this system actually measures.
#
#  1. Per-contact binary sensors. The integration creates one diagnostic
#     binary_sensor for every contact in the node's address book. On this mesh
#     that is 168 of them, and every one reads `unavailable` forever, because
#     MeshCoreContactDiagnosticBinarySensor.available is bool(self._contact_data)
#     and the coordinator's contact map does not carry the entry the sensor
#     looks up. `contact_discovery_mode: data_only` does not prevent them:
#     it suppresses entities for *discovered* contacts, while these are
#     *added* contacts, and added contacts get an entity in every mode
#     (custom_components/meshcore/binary_sensor.py, create_contact_binary_sensor).
#
#     So they are disabled here rather than deleted — deleting them only makes
#     the integration recreate them on the next reload. A disabled entity stays
#     disabled, produces no state, and drops out of pickers and templates.
#     Nothing is done to the node's own contact list; that is the owner's
#     address book, not ours. DECISIONS.md D-002.
#
#  2. Registry orphans from retired YAML. Home Assistant cleans up MQTT
#     entities that came from discovery, but a manually configured MQTT entity
#     that is removed from packages/ leaves its registry row behind, showing
#     `unavailable` forever. The meshcore_node_* placeholders removed with the
#     X5 mirror are exactly this case.
#
#    ./scripts/prune-meshcore-entities.sh          # show what would change
#    ./scripts/prune-meshcore-entities.sh --apply  # do it
#
#  Idempotent. Re-run after attaching a different Companion node.
# =============================================================================
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

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
echo "==> scanning the entity registry"
docker exec -i -e HA_TOKEN="$TOKEN" -e APPLY="$APPLY" iot-homeassistant python - <<'PYEOF'
import asyncio, os, aiohttp

APPLY = os.environ.get("APPLY") == "1"

# unique_id prefixes of retired YAML packages whose registry rows should go.
RETIRED = ("meshcore_node_",)

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

            entities = (await call({"type": "config/entity_registry/list"})).get("result", [])

            # binary_sensor only, and deliberately so: the integration also
            # owns select.meshcore_contact / _added_contact / _discovered_contact,
            # whose unique_ids contain "_contact_" too. Those are the recipient
            # pickers the messaging UI needs -- disabling them would break
            # sending, which is the opposite of a cleanup.
            contacts = [e for e in entities
                        if e.get("platform") == "meshcore"
                        and e["entity_id"].startswith("binary_sensor.")
                        and "_contact_" in (e.get("unique_id") or "")
                        and e.get("disabled_by") is None]
            orphans = [e for e in entities
                       if e.get("platform") == "mqtt"
                       and (e.get("unique_id") or "").startswith(RETIRED)]

            print(f"  per-contact sensors still enabled : {len(contacts)}")
            print(f"  registry orphans from retired YAML: {len(orphans)}")
            for e in orphans:
                print(f"      {e['entity_id']}")

            if not APPLY:
                print()
                print("  dry run - nothing changed. Re-run with --apply.")
                return

            disabled = failed = 0
            for e in contacts:
                res = await call({
                    "type": "config/entity_registry/update",
                    "entity_id": e["entity_id"],
                    "disabled_by": "user",
                })
                if res.get("success"):
                    disabled += 1
                else:
                    print(f"  FAILED disable {e['entity_id']}: {res.get('error')}")
                    failed += 1

            removed = 0
            for e in orphans:
                res = await call({
                    "type": "config/entity_registry/remove",
                    "entity_id": e["entity_id"],
                })
                if res.get("success"):
                    print(f"  removed {e['entity_id']}")
                    removed += 1
                else:
                    print(f"  FAILED remove {e['entity_id']}: {res.get('error')}")
                    failed += 1

            print()
            print(f"  disabled {disabled}, removed {removed}, failed {failed}")

asyncio.run(main())
PYEOF

if [ "$APPLY" = "1" ]; then
  echo
  echo "==> done. Disabled entities disappear from the state machine on the next"
  echo "    Home Assistant restart: docker compose restart homeassistant"
fi
