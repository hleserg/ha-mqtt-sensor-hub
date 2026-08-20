# Backlog — NOW / NEXT / LATER

Ordered by dependency, not by enthusiasm. An item is only NOW if the thing it
depends on already exists.

Status: `[x]` done · `[~]` in progress · `[ ]` not started · `[!]` blocked on
hardware, secrets or a decision that is yours to make.

---

## NOW — the working foundation

| | Task | Depends on | State |
|---|---|---|---|
| N1 | Mosquitto with per-account ACL, anonymous off, LAN-only firewall | — | `[x]` |
| N2 | Home Assistant connected to the broker, entity ids normalized | N1 | `[x]` |
| N3 | End-to-end proof: simulated sensor → broker → HA entity → history | N2 | `[x]` |
| N4 | Freshness model — `fresh` / `stale` / `offline`, age always visible | N3 | `[x]` |
| N5 | Companion investigated: protocol, ownership, single-client behaviour | — | `[x]` |
| N6 | MeshCore integration live over TCP, `data_only` contact discovery | N5, N2 | `[x]` |
| N7 | Topic contract extended: ownership, identity, raw vs normalized | N1 | `[x]` |
| N8 | ACL for `radio/raw/…` and `observed/…`, verified positive and negative | N7 | `[x]` |
| N9 | Documentation set + decision log on doctor | all | `[x]` |

NOW is complete. What follows is not started, and deliberately so.

---

## NEXT — almost certainly needed

### X1 · Pin the Companion's address `[x]`
*Depends on: N6.* Done — the owner reports a DHCP reservation is already in
place on the router for `192.168.1.93`. That is a router-side fact and cannot be
confirmed from doctor; what doctor can confirm is that the node has stayed at
`.93` across every check and an HA restart. If MeshCore entities ever go
`unavailable` for no other reason, re-check the reservation first.

### X2 · Remote access to home data `[x]`
*Depends on: the router. Nothing further was needed from doctor.*

**WireGuard on the router, verified working 2026-08-20** — the owner reached
Home Assistant from a phone on mobile data. Tailscale was installed on doctor,
never authenticated, and removed at their request; the router is the better
place for this anyway, because it puts the whole LAN behind one tunnel instead
of one host, and doctor keeps zero VPN software. Removal was verified: no
binaries, no systemd units, no `tailscale0`, no repository, no leftover `ts-`
iptables rules, `/etc/resolv.conf` untouched, all 22 containers running.

The router's WireGuard server: udp/41495, tunnel subnet `172.16.6.0/24`, the
router itself `172.16.6.1`, clients from `.2` upward.

What doctor needed, and has:

- **1883** — `172.16.6.0/24` in `firewall/allowed-subnets.conf`, applied, and
  re-read at boot by `iot-stack-firewall.service`. Without it the tunnel would
  reach the host and then be dropped in `DOCKER-USER`.
- **8123** — nothing. Home Assistant runs with host networking, so ufw governs
  it, and ufw already accepts 8123 from any source that can route to the host.

The one non-obvious part was client-side, and cost a round trip: *Разрешённые
подсети* means opposite things at the two ends. On the router, a peer's entry is
the address that peer owns (`172.16.6.2/32`). In the phone's config, the peer
block's `AllowedIPs` is what gets routed **into** the tunnel, so it must list
`192.168.1.0/24` — with only `172.16.6.0/24` there, the tunnel comes up, the
handshake succeeds, and nothing in the house is reachable.

Consequence worth remembering: while the tunnel is up, `192.168.1.0/24` belongs
to home. On someone else's Wi-Fi using the same very common subnet, their local
addresses become unreachable.

### X3 · Real weather station `[~]` — self-built, incrementally
*Depends on: hardware being built, not bought.* Decided 2026-08-20: **no
commercial station.** The owner builds sensors himself, on **ESP32-C5**, adding
them one at a time. The purchase recommendation is withdrawn;
`WEATHER-STATION-CHOICE.md` was rewritten around the new plan and keeps the
part of the analysis that survives — the data-direction argument, which now
decides `mqtt:` over ESPHome's native API for the same reason it decided SDR over
a vendor gateway.

Everything downstream is unchanged and still proven against
`test-weather-publisher.sh`: topics, entities, freshness machinery, the watch
contract. `weather/outdoor/…` is now stated explicitly as a **role** — whatever
plays the outdoor-station part fills it — and every other own sensor lives in
`own/<id>/…` (`MQTT.md` §14, `SENSORS.md` case 5, D-013).

Ready and waiting for the first board:

- `esphome/own-sensor-reference.yaml` — copy-per-sensor config, **never built or
  flashed**, no C5 in hand. First flash is the test; its own checklist is at the
  bottom of the file.
- `own/` namespace, ACL pattern rules, four functional ACL tests passed.
- `expected_inputs:` in `weather-engine/config.yaml`, so a station that measures
  three things and not four reads `ok` instead of a permanent `partial`.

Two things to know before ordering: the C5 is **ESP-IDF only** in ESPHome and its
v1.0 silicon wants ESP-IDF ≥ 5.5.2 (pin it if the default lags); and Wi-Fi costs
100–150 mA with the radio up, so a battery outdoor node is a weeks-to-months
proposition, not years.

Snow is still not measured by anything in this plan, and that is a property of
piezo and tipping-bucket gauges rather than of the choice made here.

### X4 · Weather engine `[x]`
Done 2026-08-20. The container now derives and publishes, retained:
`weather_state/feels_like`, `frost_risk`, `ice_risk`, `data_quality`,
`computed_at`, `meta` and `engine_status`. It is part of the default stack
rather than the `engine` profile. Reasoning in `DECISIONS.md` D-012.

Everything published has a closed form or a written-out threshold rule, and
`derive.py` unit-tests all of them against published reference values:

```sh
docker compose run --rm --no-deps weather-engine python /app/main.py --selftest
```

Three of the pre-existing entities stay `unknown`, and that is the answer, not
a gap: `condition` needs precipitation type, `rain_risk` needs months of local
history to fit a probability against, `thunderstorm` needs strike rate rather
than distance-to-last-strike.

Two things changed from the plan in the process:

1. **`frost_risk` and `ice_risk` are categorical** — `none` / `watch` /
   `likely` — instead of `0–100 %`. The rules behind them are thresholds, and a
   threshold rendered as a percentage claims a probability nobody computed. Safe
   to change because no long-term statistics existed for them.
2. **Dew point and pressure tendency were not moved here.** Dew point stays a
   station measurement in `weather/outdoor/`; the engine computes it internally
   only when the station omits it. Pressure tendency stays in Home Assistant,
   which already holds the pressure history.

Verified on both freshness paths against the live broker: with the simulator's
last sample 4 h 55 m old it published `data_quality: degraded` and a
`computed_at` four hours in the past; one fresh sample later, `ok` with a 31 s
stamp. The negative path — outputs freezing and aging rather than being
overwritten with `unknown` — is the same behaviour as the MeshCore mirror.

Still true: with only the simulator publishing, these values describe simulated
weather. X3 is what makes them mean anything.

### X5 · MeshCore telemetry mirror `[x]`
Done 2026-08-20. The automation `MeshCore -> MQTT mirror` republishes an
allow-list of node metrics into `meshcore/<node_id>/<metric>`, retained, once a
minute: `battery`, `battery_voltage`, `status`, `last_seen`, `meta`. Verified
against the live node — `meshcore/044e2d/battery 73.08`,
`binary_sensor.meshcore_mirror_stale off`.

The design questions and their answers are in `DECISIONS.md` D-011: MQTT
Statestream was rejected on topic shape, the integration's own `mqtt_uploader`
turned out to be a LetsMesh packet feed rather than telemetry, and the metric
list is explicit rather than a loop because this mesh produces ~190 entities.

Two things changed from the original plan, both because the speculative version
did not survive contact with the hardware:

1. **Four of the six placeholder entities were deleted, not filled.** They
   expected `temperature`, `humidity`, `pressure` and `rssi`. This node carries
   no environmental sensor, and RSSI describes a received packet rather than a
   node. Publishing those topics would have been inventing data. They return
   under the same names when a node with a BME280 is attached.
2. **A 168-entity mess surfaced on the way** and was contained — see D-002's
   outcome note and `scripts/prune-meshcore-entities.sh`. Registry went from
   265 entities to 95.

What replaced the placeholders is the one thing the integration cannot report:
whether the MQTT copy is current. `sensor.meshcore_mirror_age` and
`binary_sensor.meshcore_mirror_stale` trip after ten minutes of silence from
either the automation or the radio.

### X6 · LPP pressure gap `[ ]`
*Depends on: a mesh node that actually reports pressure.* `meshcore-ha` v2.9.0
does not map Cayenne LPP type 115 (barometer); such a reading arrives as a
generic unitless sensor. Fix locally with a template sensor, or upstream with a
mapping patch. Evidence in `MESHCORE.md`.

### X7 · Schedule backups `[x]`
Done. Daily `--full` at 04:30 via `/etc/cron.d/iot-stack-backup`, retaining 14,
into `/mnt/backup/iot-stack` (a real ext4 mount on `/dev/sda1`). The owner chose
that location knowing the archive holds every device password in plaintext; it
is written `chmod 600`.

Three defects were found by testing rather than by reading, and fixed:

1. `--full` **had never worked**. Home Assistant writes five `.storage` files as
   root with mode 600, so tar aborted with exit 2 at the last step. The script
   now re-executes itself through sudo for that mode, and hands the finished
   archive back to the invoking user.
2. Every archive was carrying `tools/meshcore-venv` — 1170 files of
   reinstallable dependencies, 4.9 MB of a 5.5 MB archive. Excluded.
3. The status publish used `mosquitto_pub -W`, which does not exist; the guard
   swallowed the error and nothing was ever published. Fixed and tested on both
   the success and the failure path.

Every run now publishes retained JSON to `monitor/backup/status`, surfaced as
`sensor.backup_last_run`, `sensor.backup_last_result`, `sensor.backup_age` and
`binary_sensor.backup_failing`. A backup that silently stops is the failure this
exists to prevent — see `DECISIONS.md` D-010.

---

### X8 · The Minecraft power plug `[ ]`
*Raised 2026-08-21 by the owner: "умная розетка на перезагрузку minecraft
сервера… надо бы её тоже подключить." Deferred to the next session by his own
call. Nothing has been changed.*

**What is already there**, read-only inspection on doctor:

| | |
|---|---|
| Plug | TP-Link Tapo at `192.168.1.134` |
| Driver | `tapo-api` container (`tapo_api.py`), `POST /on`, `POST /off`, up 3 months |
| Caller | `/home/sergey/scripts/mc-healthcheck.sh`, cron `*/2 * * * *` |
| Target | the Minecraft host `192.168.1.50` — a *different* machine |
| Role | last-resort rung of an escalation ladder: alert → RCON warn + restart → docker kill → **Tapo power reset** → WOL → dead |

**The design question, and it is the whole task.** The plug already has exactly
one owner, and that owner is a state machine that cuts mains power to a running
server. Adding Home Assistant's Tapo integration would create a second owner of
one relay — avoidance #1, in its most literal form: HA could toggle power while
`mc-healthcheck.sh` is mid-escalation. Default answer, unless the owner wants
otherwise: **Home Assistant observes and does not control.** The script stays
the actor and publishes what it did; HA renders state and history and can alert.
A manual switch in HA, if wanted at all, needs an interlock — not a second
opinion about the same relay.

**Two findings from the inspection, neither introduced by us:**

1. A power cycle failed on the record. `2026-08-12 03:38` — `off` returned 200,
   the follow-up `on` returned 500: `ConnectionRefused` to `192.168.1.134:80`.
   The plug's own HTTP endpoint refused right after the cut. So the server was
   left unpowered by a routine that intended to restart it. Whatever shape the
   integration takes, `on` needs a retry with backoff and an alert on final
   failure; the current one-shot is a coin flip at the worst moment.
2. `mc-healthcheck.sh` carries a Telegram bot token and chat id hardcoded in
   plaintext, and posts through an external bot API host. Out of scope for this
   stack, worth moving to an env file regardless.

**Not decided yet:** whether the plug's state reaches MQTT from the script, from
a poller, or from HA in read-only mode; whether it lives under `own/` or a new
namespace, since it is neither a sensor of mine nor somebody else's transmitter.
Decide that before writing anything.

## LATER / EXPERIMENTAL

### L1 · MeshCore live map `[ ]`
*Depends on: X8 (a real packet feed) and one verification.* Topics line up
(`meshcore/#` vs `meshcore/{IATA}/{PUBLIC_KEY}/packets`), the hex field name
lines up (`raw` is in its `LIKELY_PACKET_KEYS`), but the map reads `rssi`/`snr`
lowercase while `meshcore-ha` publishes `RSSI`/`SNR`, and there is no key-case
normalization in its decoder. Verify with a real feed before installing.
Read-only consumer, so it cannot damage the data path — that is the point of
having it downstream of MQTT.

### L2 · Enable the packet feed `[ ]`
*Depends on: L1 being wanted.* `meshcore-ha` can publish packets to MQTT; the
namespace is reserved (`radio/raw/meshcore/…`) and the ACL rule exists. Not
enabled, because a packet stream from a 168-node mesh with no consumer is just
disk and CPU.

### L3 · RF collector ingestion `[!]`
*Depends on: the rebuilt Tuya IR/RF device at 192.168.1.48.* The contract is
written (`SENSORS.md` §2, allow-list flow), the simulator proves the whole path,
and the ACL is in place. Passive reception of unencrypted broadcasts only.

The node's own agent has published a hardware audit and is building a sensor
collector on it. Coordination — including the answered questions (a)–(d) — is in
`../COORDINATION-rf-node.md`. What that audit settles, and what nothing in this
repo can change: **the node has no FSK**, so Fine Offset/Ecowitt, Bresser and
LaCrosse are permanently out of its reach. It hears OOK — the cheap 433 MHz
thermo-hygrometer population — on 315, 433.92 and 868 MHz, one frequency at a
time.

### L3b · rtl_433 over the node's pulse data `[ ]`
*Depends on: L3, and on nothing else — no dongle needed.* The node's
`?format=ook` output is genuine rtl_433 `pulse_data`. A small host service that
polls it, runs `rtl_433 -r` and lets rtl_433's **native** MQTT output publish the
decode gives decoded third-party sensors with hardware already on the shelf, and
costs zero broker changes — `rtl_433/#`, the `rtl433` account and the HA read
grant all exist. Agreed with the node agent as the decode path (D-005 again: the
decoder is not ours to write).

Non-negotiable condition, recorded when this was agreed: **no entity is created
from `rtl_433/#` except by explicit allow-listing.** The gate in `SENSORS.md` §2
is what stops an open 433 MHz band becoming hundreds of rows, and moving decode
to the host must not quietly bypass it.

### L4 · rtl_433 with a real SDR `[!]`
*Depends on: an SDR dongle — now optional rather than planned.* Config exists and
uses rtl_433's **native** MQTT output (`DECISIONS.md` D-005). Its remaining value
is precisely the gap L3b cannot fill: **FSK** neighbours on 868 MHz, which the
node cannot demodulate at all. Whether that is worth ~3–4k ₽ is answerable for
free first — scan with the node and see what is actually on the air.

### L5 · The normalizer `[ ]`
*Depends on: two collectors hearing the same transmitter.* Today there is one
collector, so dedup would be theatre. The identity format, the `observed/…`
namespace and the metadata contract are already fixed — that is the part that is
expensive to change later. `MQTT.md` §12–13, and the dedup-key rule is already
fixed in `DECISIONS.md` D-009 — key on transmitter bytes, never on RSSI, hops or
receive time.

### L6 · Long-term storage `[ ]`
*Depends on: having history worth exporting.* Recorder holds 774 KB. Revisit
when a year of pressure data exists, or when a question needs SQL that recorder
cannot answer. `DECISIONS.md` D-006.

### L7 · MeshCore → home control `[ ]`
*Depends on: X5, and on an identity allow-list.* Sending an alert out over the
mesh is easy and safe. Accepting a *command* from the mesh is not: it needs an
allow-list by public key, because otherwise any participant can talk to the
house. Do not build the inbound direction without it.

### L8 · RemoteTerm as radio owner `[ ]`
*Depends on: a second Companion node.* Not a competitor to `meshcore-ha` on one
radio — they cannot share it. With two nodes both can run. Note its own warning
that bots execute arbitrary Python.

### L9 · ESP32-S3 watch firmware `[ ]`
*Depends on: hardware.* The backend already does what it needs to: every value
is retained, the `watch` account is read-only, and no HTTP frontend is in the
path. Nothing here blocks it.

---

## Explicitly not planned

| | Why |
|---|---|
| A homegrown rtl_433 parser | upstream publishes MQTT itself — D-005 |
| `meshcoretomqtt` | needs USB repeater + custom firmware build — D-004 |
| Two applications on one Companion | the transport evicts the older client — D-001 |
| An entity per discovered MeshCore contact | 168 of them — D-002 |
| InfluxDB/Prometheus/Grafana now | nothing to store yet — D-006 |
| MeshCore as transport for WiFi sensors | LoRa is for where WiFi is not; avoidance #10 in the brief |
