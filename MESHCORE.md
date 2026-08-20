# MeshCore integration

## Status on doctor right now

**Connected and live since 2026-08-20.** A Companion node reachable at
`192.168.1.93:5000` over WiFi is configured in Home Assistant through the
`meshcore-ha` integration (v2.9.0), connection type **TCP**. It produces 28
entities carrying real values — battery percentage and voltage, frequency,
spreading factor, bandwidth, TX power, node status, GPS, and the remote-telemetry
rate-limiter budget.

The node runs the low-power firmware from
[`dt267/MeshCore-Low-Power-Firmware`](https://github.com/dt267/MeshCore-Low-Power-Firmware)
and sees a dense mesh — **168 contacts** at the time of connection.

### Read this before you pick up your phone

**A Companion serves exactly one client at a time, and a new connection evicts
the old one.** While Home Assistant is connected, the MeshCore phone app cannot
use this node over WiFi; if it connects anyway, the two will take turns kicking
each other off.

This is not a guess. The transport keeps a single `WiFiClient` and calls
`client.stop()` on the previous one (`SerialWifiInterface.cpp` in upstream
MeshCore), and the behaviour was measured on this node: socket A received EOF
the moment socket B connected. The full evidence, and why `meshcore-ha` rather
than RemoteTerm owns the radio, is `DECISIONS.md` D-001.

To hand the radio back: disable the MeshCore integration in Home Assistant, or
`docker compose stop homeassistant`. It reconnects on its own afterwards.

### What the integration changes on your node

`meshcore-ha` is not a passive reader. On connect it calls
`set_manual_add_contacts(True)`, so the node switches to manual contact
management. Before the integration was configured, our node reported
`manual_add_contacts: false`. Contact discovery is deliberately set to
**`data_only`**, intended so that 168 discovered contacts do not each become a
binary sensor. It did not work: those contacts are *added* on the node, and
added contacts get an entity in every mode. They are contained instead — see
`DECISIONS.md` D-002 and `scripts/prune-meshcore-entities.sh`.

## The supported route (do not write a protocol)

Use **[`meshcore-dev/meshcore-ha`](https://github.com/meshcore-dev/meshcore-ha)**,
the actively developed Home Assistant integration for MeshCore. It talks to a
MeshCore Companion over **USB, BLE or TCP**, creates entities for node
telemetry and radio diagnostics, and can publish to MQTT brokers itself.

It requires Home Assistant 2023.8+ (we run far newer) and a node with
API-compatible firmware.

Two things worth knowing before you start:

- The project describes itself as a work in progress, and **BLE is the least
  tested path**. Prefer USB or TCP if you have the choice.
- BLE pairing **through a Home Assistant Bluetooth proxy does not work** —
  only a Bluetooth adapter directly on the host. That is one reason Home
  Assistant here runs with `network_mode: host` and `/run/dbus` mounted.

## Choosing a connection

| | When it fits | Notes |
|---|---|---|
| **USB** | Companion plugged into doctor | Most reliable. Needs `/dev/ttyUSB*` or `/dev/ttyACM*` passed to the container |
| **TCP** | Companion is a Wi-Fi build, or reachable over the network | No cabling to doctor, no device passthrough, survives moving the radio |
| **BLE** | Nothing else is possible | Least tested; needs `bluetooth.service` started on doctor and direct-adapter pairing |

Recommendation: **TCP if the Companion runs a Wi-Fi firmware, otherwise USB.**
Both avoid the BLE caveats entirely.

## Install

The integration is not in Home Assistant core, so it lives in
`custom_components/`. Two ways:

### Via HACS (gets updates)

1. Install HACS if it is not there yet.
2. HACS → three-dot menu → **Custom repositories**
3. Repository `https://github.com/meshcore-dev/meshcore-ha`, category
   **Integration**
4. Install, then restart Home Assistant.

### Directly (no HACS needed)

`scripts/install-meshcore-integration.sh` fetches the repository and copies
`custom_components/meshcore` into `homeassistant/config/custom_components/`.
Re-run it to update. Restart Home Assistant afterwards.

## Configure

**Settings → Devices & Services → + Add Integration → MeshCore**, then pick
USB / BLE / TCP and fill in the port or address.

Then **Configure → Global Settings**:

- **Contact Discovery Mode** — set this to **Data only** or **Disabled** unless
  you specifically want an entity per contact. On a busy mesh the default
  ("Entity per contact") is the MeshCore equivalent of the RF firehose this
  stack works hard to avoid elsewhere.
- **Self diagnostics** — leave on; poll interval 300 s is fine. These calls
  (`GetStats`, `DeviceQuery`) do not transmit on the air.
- **MQTT broker settings** — optional, see below.

### For USB, pass the device through

Add to the `homeassistant` service in `docker-compose.yml`:

```yaml
    devices:
      - /dev/serial/by-id/YOUR-DEVICE-HERE:/dev/ttyUSB0
```

Use the `/dev/serial/by-id/…` path, never `/dev/ttyUSB0` directly — the
numbering changes when other USB devices come and go, and a Companion that
silently becomes `ttyUSB1` looks exactly like a dead radio.

Find it with: `ls -l /dev/serial/by-id/`

## Getting telemetry into `weather/` or `meshcore/`

The integration gives you Home Assistant entities. This stack additionally
wants the data in MQTT, in the normalized namespace, so the watches and the
weather engine can consume it without going through Home Assistant.

Required minimum from a MeshCore sensor node — temperature, humidity, pressure,
battery, last seen — mapped as in `MQTT.md` §5.

### How it works now

Both routes below were considered; the automation won. See `DECISIONS.md` D-011
for why, including why MQTT Statestream was rejected.

The automation `MeshCore -> MQTT mirror` in `automations.yaml` runs once a
minute and republishes an allow-list of metrics into `meshcore/<node>/…`,
retained:

```
meshcore/044e2d/battery          73.08
meshcore/044e2d/battery_voltage  3.877
meshcore/044e2d/status           online
meshcore/044e2d/last_seen        2026-08-20T17:58:53+00:00
meshcore/044e2d/meta             {"node_id":"044e2d","name":"MeshCore ✌️Beta Serega (044e2d)", …}
```

Three properties are worth knowing before you change it:

- **Nodes are discovered, not configured.** The automation finds node ids by
  matching `sensor.meshcore_<6 hex>_battery_voltage…` against the state
  machine, so attaching a different Companion needs no edit here — the new
  public key starts publishing under its own node id. Only the MQTT *entities*
  in `packages/meshcore.yaml` name a specific node, and only because an MQTT
  entity needs a literal topic.
- **The metric list is explicit.** Never a loop over `sensor.meshcore_*`: this
  mesh produces ~190 entities, and a generic mirror would move an entity
  explosion into topic space.
- **`unknown` is never published.** A retained topic holding "unknown" is worse
  than a stale one — it destroys the last good value instead of ageing it. What
  ages instead is `sensor.meshcore_mirror_age`, and
  `binary_sensor.meshcore_mirror_stale` trips after ten minutes of silence
  from either the automation or the radio.

`last_seen` is the node's own last report time (the newest `last_updated` among
the mirrored source entities), not the moment the mirror ran. That distinction
is the whole point of the freshness model: a mirror that stamps `now()` reports
itself as healthy forever.

### Option A — let the integration publish to MQTT itself

Not used, and worth understanding why before reaching for it. `meshcore-ha`'s
`mqtt_uploader.py` is a **LetsMesh** uploader: IATA-keyed topics, an external
`meshcore-decoder` command, signed auth tokens, raw packets and node status.
That is the `radio/raw/meshcore/…` layer (`MQTT.md` §12, backlog L2), not
measurements, and it does not produce `meshcore/<node>/<metric>` at all.

### Option B — mirror the entities with a Home Assistant automation

This is what was built. The working version lives in `automations.yaml`; read
it there rather than copying a sketch from this document.

### If the MeshCore node *is* the weather station

Mirror it into `weather/outdoor/#` instead of `meshcore/#`, and publish
`weather/outdoor/last_update` from the node's own measurement time — not from
`now()`. Mesh delivery is not instant, and stamping arrival time as measurement
time is precisely the failure the freshness logic exists to catch.

That is also the point of the whole namespace design: the consumer never learns
whether a reading arrived by LoRa, by 433 MHz or over Wi-Fi.

## Planned: moving to the T114 over USB

The v4 Companion at `192.168.1.93` is borrowed and goes back to its owner. A
Heltec Mesh Node **T114** takes its place, plugged into doctor over USB. Nothing
below has been executed — it is the procedure to follow when the hardware is on
the desk, written now so the swap does not have to be improvised.

**What is already swap-proof.** The mirror automation discovers the node id at
runtime by matching `sensor.meshcore_<6 hex>_battery_voltage`, so it starts
publishing under the new id with no edit. The ACL grants `meshcore/#`, not one
node. The recorder excludes are globs. The `weather/` namespace is untouched by
any of this.

**What is not.** Three things carry the literal `044e2d`, and all three are in
`homeassistant/config/packages/meshcore.yaml` — the two `state_topic`s and the
`json_attributes_topic` of the mirror entities. They need a one-line edit each.

### Before touching anything

```sh
ls -l /dev/serial/by-id/                      # what is there now
docker compose exec homeassistant ls /dev/serial/by-id/ 2>/dev/null
./scripts/backup.sh --full                    # .storage holds the config entry
```

The container runs `privileged: true`, so the host's `/dev` is already visible
inside it — a USB radio may work with no compose change at all. Prefer the
explicit mapping anyway: it documents the dependency and survives the day
`privileged` is dropped.

### The swap

1. **Plug in the T114 and identify it.** nRF52840 boards enumerate as CDC ACM,
   so expect `/dev/ttyACM*` rather than `/dev/ttyUSB*`. Take the stable name:
   `ls -l /dev/serial/by-id/` → `usb-…-if00`.
2. **Pass it through.** Add to the `homeassistant` service in
   `docker-compose.yml`, then `docker compose up -d homeassistant`:
   ```yaml
       devices:
         - /dev/serial/by-id/usb-YOUR-T114-if00:/dev/ttyACM0
   ```
   Never `/dev/ttyACM0` on the host side — the number moves when another USB
   device appears, and a radio that silently became `ttyACM1` looks exactly like
   a dead one.
3. **Remove the TCP config entry first, then add the USB one.** Settings →
   Devices & Services → MeshCore. Two entries for two radios is legitimate, but
   the v4 is leaving, so a stale entry would just retry forever and fill the log.
4. **Read the new node id.** It is the first six hex characters of the node's
   public key, and it is in the new entity ids the moment the integration
   connects: `(cd tools && python3 mc_check.py)` or Developer Tools → States, filter
   `sensor.meshcore_`.
5. **Edit the three topics** in `packages/meshcore.yaml`, restart Home Assistant,
   then `./scripts/normalize-entity-ids.sh`.
6. **Clear the v4's retained topics.** They are retained, so they survive the
   node's departure and will read as a live node forever:
   doctor has no `mosquitto_pub` on the host, and nothing sources `.env` for
   you, so both are explicit here:
   ```sh
   cd /home/sergey/iot-stack && set -a && . ./.env && set +a
   for t in battery battery_voltage status last_seen meta; do
     docker run --rm --network host eclipse-mosquitto:2.0.22 \
       mosquitto_pub -h 127.0.0.1 -u "$HA_MQTT_USER" -P "$HA_MQTT_PASSWORD" \
       -t "meshcore/044e2d/$t" -r -n
   done
   ./scripts/mqtt-watch.sh 'meshcore/#' 5      # only the new node should answer
   ```
   An empty retained payload deletes the retained message; it is not a value.
7. **Re-run the contact prune.** The T114 carries its own address book, and
   `contact_discovery_mode: data_only` does **not** stop entities for *added*
   contacts — that is exactly how 168 of them appeared last time (`DECISIONS.md`
   D-002). Dry run first, it is the default:
   ```sh
   ./scripts/prune-meshcore-entities.sh          # look at the list
   ./scripts/prune-meshcore-entities.sh --apply
   ```

### What the v4 going away should look like

This is the mirror's negative path, and it has never been exercised. When the
old node stops answering, the correct observable is that its topics **freeze and
age**:

- `meshcore/044e2d/*` keep their last values — retained messages do not expire.
- `sensor.meshcore_mirror_age` climbs.
- `binary_sensor.meshcore_mirror_stale` goes `on` past the threshold.
- The integration's own `sensor.meshcore_044e2d_*` go `unavailable`.

What must **not** happen is `unknown` or `unavailable` appearing as a payload in
an MQTT topic. The automation filters those values before publishing precisely
so that a stale-but-real number is never overwritten by a word. If a topic ever
contains one, that filter has a hole — it is a bug, not a status.

### Open questions, to answer with the hardware rather than by guessing

- **Can the phone still reach the T114 over BLE while Home Assistant holds the
  USB port?** The single-client behaviour was measured on the TCP transport
  (`DECISIONS.md` D-001). Whether the firmware serializes across *different*
  transports is unverified. Test it before relying on either answer.
- **Does the T114 report anything the v4 did not** — a pressure or temperature
  reading, in particular. If it does, the mirror's metric list is where to add
  it, and Cayenne LPP type 115 is still unmapped (see below).

## Firmware assumptions, and how far they were actually checked

The firmware repository is **documentation and release binaries only** — nine
files, no source. So nothing here is claimed from reading the firmware; it is
either measured against the node or taken from upstream MeshCore, with the
assumption flagged.

| Assumption | How it was checked | Status |
|---|---|---|
| WiFi transport listens on TCP 5000 | `meshcore` 2.3.8 `create_tcp()` connected; `DEFAULT_TCP_PORT = 5000` in meshcore-ha | **verified** |
| Frame protocol matches `meshcore_py` | full `self_info` and 168 contacts read back without a decode error | **verified** |
| One client at a time, newest wins | measured — socket A got EOF when B connected | **verified** |
| Telemetry is enabled on the node | `telemetry_mode_env / _loc / _base` all `1` in `self_info` | **verified** |
| The single-client eviction is inherited unchanged from upstream | source read in upstream `SerialWifiInterface.cpp`; the fork ships no source | **assumed, behaviour matches** |
| mDNS `meshcore-xxxx.local` works | firmware README states it; **not tested here** — we use the IP | **unverified** |
| Static IP survives reboot (`set wifi.ip`) | firmware README; **not tested** | **unverified** |

`192.168.1.93` is currently whatever DHCP handed out. If the node moves, the
integration stops with a connection error and nothing else breaks. Pinning it —
either a DHCP reservation on the router or `set wifi.ip` on the node — is a
NEXT item.

### Cayenne LPP: pressure is not mapped

`meshcore-ha` v2.9.0 maps LPP types `0, 1, 2, 3, 100, 101, 102, 103, 104, 113,
116, 117, 128, 135` — digital/analog IO, illuminance, presence, temperature,
humidity, accelerometer, voltage, current, power, colour. **Type 115,
Barometer, is absent** from both the integer table and the string table in
`telemetry_sensor.py`.

Pressure from a mesh node is therefore not lost, but it arrives through the
"unknown LPP type → generic sensor" fallback: no `device_class`, no `hPa`, no
pressure statistics. The firmware itself handles pressure fine (its display
shows BME280 readings in hPa), so this is an integration gap, not a radio one.

It does not block us — our own weather station carries pressure over its own
transport, and `weather/outdoor/pressure` is unaffected. If a MeshCore node ever
becomes the pressure source, the options are a template sensor mapping the
generic entity onto a proper one, or a patch upstream. Recorded as a NEXT item.

## Checklist

- [x] Companion reachable over TCP (`192.168.1.93:5000`)
- [x] `meshcore-ha` installed (v2.9.0, `custom_components/meshcore`)
- [x] Integration configured — TCP, `contact_discovery_mode: data_only`
- [x] Node entities visible in Home Assistant with live values
- [x] Zero integration errors in the Home Assistant log
- [x] Single-owner behaviour understood and documented (`DECISIONS.md` D-001)
- [ ] Node IP pinned (DHCP reservation or `set wifi.ip`)
- [x] Telemetry mirrored to `meshcore/<node>/…`, verified with `mqtt-watch.sh`
- [x] `last_seen` reflects the measurement, not the mirror time
- [ ] Packet feed published to `radio/raw/meshcore/…` (only when the map needs it)
