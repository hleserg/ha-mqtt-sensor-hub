"""Create the MeshCore config entry over the Home Assistant API.

Deliberate choices, both explained in DECISIONS.md:
  * connection_type = tcp  -> the Companion runs WiFi transport on 5000
  * contact_discovery_mode = data_only -> the mesh has 168 contacts, and
    upstream documents that the default ("full") makes one binary_sensor per
    discovered contact. That is hundreds of permanently-"discovered" entities.
"""
import json, sys
from ha_api import load_env, get_token, api

HOST, PORT = "192.168.1.93", 5000

env = load_env()
tok = get_token(env)

existing = api("/api/config/config_entries/entry", tok)
for e in existing:
    if e.get("domain") == "meshcore":
        print("meshcore entry already exists:", e.get("entry_id"), e.get("title"), e.get("state"))
        sys.exit(0)

flow = api("/api/config/config_entries/flow", tok,
           {"handler": "meshcore", "show_advanced_options": True})
print("step:", flow.get("step_id"), "| fields:", flow.get("data_schema") and
      [f.get("name") for f in flow["data_schema"]])

flow = api("/api/config/config_entries/flow/" + flow["flow_id"], tok,
           {"connection_type": "tcp"})
print("step:", flow.get("step_id"), "| fields:", flow.get("data_schema") and
      [f.get("name") for f in flow["data_schema"]])

result = api("/api/config/config_entries/flow/" + flow["flow_id"], tok, {
    "tcp_host": HOST,
    "tcp_port": PORT,
    "contact_discovery_mode": "data_only",
    "self_telemetry_enabled": True,
    "self_telemetry_interval": 300,
})
print("--- result ---")
print("type:", result.get("type"), "| title:", result.get("title"))
if result.get("type") == "form":
    print("errors:", result.get("errors"), "step:", result.get("step_id"))
    sys.exit(1)
print(json.dumps(result.get("result", {}), indent=1, default=str)[:800])
