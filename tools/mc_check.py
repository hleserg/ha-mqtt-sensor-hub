import json
from ha_api import load_env, get_token, api

tok = get_token(load_env())
states = api("/api/states", tok)
mc = [s for s in states if ".meshcore" in s["entity_id"] or s["entity_id"].startswith(
      ("sensor.meshcore", "binary_sensor.meshcore", "device_tracker.meshcore", "select.meshcore"))]
print("meshcore entities: %d (of %d total)" % (len(mc), len(states)))
for s in sorted(mc, key=lambda x: x["entity_id"]):
    st = str(s["state"])
    print("  %-52s %s" % (s["entity_id"], st[:44]))
