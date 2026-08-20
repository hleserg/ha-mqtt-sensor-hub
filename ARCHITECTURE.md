# Architecture

## The shape of it

```text
  Outdoor weather sensors            Third-party 433/868 MHz + BLE sensors
            │                                        │
            ▼                                        │
      T114 / MeshCore                                │
            │                                        │
            ▼                                        ▼
    MeshCore Companion                    RF/BLE Sensor Collector
     (USB / BLE / TCP)                    (repurposed Tuya IR/RF remote)
            │                                        │
            │  meshcore-ha over TCP                  │  normalized telemetry
            │  (single owner — D-001)                │  sensors/…  +  discovery
            ▼                                        ▼
   ┌───────────────────────────────────────────────────────────────┐
   │                    Mosquitto  ·  doctor:1883                  │
   │   auth + ACL, retained state, persistent across restarts      │
   └───────────────────────────────────────────────────────────────┘
        │                    │                       │
        │                    │                       │
        ▼                    ▼                       ▼
  Home Assistant      ESP32-S3 watches        weather-engine
  doctor:8123          (read-only)             derives + publishes
   ├── dashboards      subscribes:              reads weather/…, sensors/…
   ├── automations       weather/#              publishes weather_state/…
   └── history           weather_state/#          feels_like, frost/ice risk,
        │                                          data_quality, computed_at
        │
        └── (later) InfluxDB / TimescaleDB / VictoriaMetrics
             — another subscriber, not a rewrite
```

Doctor (192.168.1.51) is the centre. Nothing in the weather path leaves the
house.

---

## The load-bearing decision: MQTT is the system of record

Every measurement becomes a retained MQTT message **first**. Home Assistant is
a consumer of that stream, not the origin of it.

This is what makes the rest cheap:

- **A new consumer costs nothing.** InfluxDB, TimescaleDB or VictoriaMetrics
  can be added later by subscribing to the same topics. Not one publisher
  changes, not one sensor is reflashed. The alternative — history living only
  inside Home Assistant's recorder — would mean that changing the database
  means re-plumbing every source.
- **The watches do not depend on Home Assistant.** They subscribe to the broker
  directly. If Home Assistant is down, restarting or being upgraded, the
  watches keep receiving weather.
- **Retained messages are the state.** A device that connects gets the current
  values in the first round-trip, with no request/response protocol and no
  cache to invalidate.
- **The transport is replaceable.** A sensor reading that arrives by LoRa mesh,
  by 433 MHz or over Wi-Fi lands on the same topic in the same format. Swapping
  MeshCore for something else later is a collector change, not an architecture
  change.

Corollary: the ESP32-S3 watches and the weather engine are peers of Home
Assistant, not plugins of it.

---

## Why Docker Compose, and not something native

Doctor already runs 21 containers across five Compose projects, with
`docker.service` enabled at boot and Docker 29.3 / Compose v5.1 installed and in
daily use. Introducing a second deployment paradigm — a native `mosquitto`
package plus a Python venv for Home Assistant — would mean two upgrade stories,
two backup stories and two ways to be broken, on a machine whose value is that
it currently works.

Home Assistant OS / Supervised was not an option either: it wants the whole
machine, and this machine is already busy.

The stack is deliberately additive: its own Compose project (`iot-stack`), its
own bridge network (`172.28.0.0/24`, chosen because `172.17`–`172.23` were
taken), its own directory. It shares nothing with the existing projects except
the Docker daemon.

---

## Component decisions

### Home Assistant runs with `network_mode: host`

The documented deployment mode, and here it earns its keep three times:

1. Local discovery (mDNS/SSDP) works, which a bridge network breaks.
2. Port 8123 is a real host port, so the **existing ufw rule** governs it.
   Published Docker ports would bypass ufw entirely (see below).
3. The MeshCore integration needs direct access to a USB serial device or the
   host's Bluetooth adapter. Neither survives a bridge network.

### Mosquitto keeps its own persistence volume

`iot-stack_mosquitto_data` holds retained messages and queued QoS-1 traffic. It
is a named volume rather than a bind mount so the container's own uid owns it
without any host-side chown, and it survives `docker compose down`.

### Entity ids live in the registry, not in YAML

Home Assistant composes an entity id from the device name plus the entity name,
so "Outdoor Weather Station" + "Outdoor Temperature" becomes
`sensor.outdoor_weather_station_outdoor_temperature`. A manually configured MQTT
entity cannot override that: `object_id` is a discovery-only option and the
integration rejects it outright.

Entity ids are part of the contract — dashboards, automations, the watch API and
every template reference them — so leaving them at whatever the device name
happens to produce is not neutral. The entity registry is the supported place to
change them, and it is writable only over the WebSocket API.

`scripts/normalize-entity-ids.sh` therefore does deterministically what renaming
in the UI does by hand: every entity id becomes `<domain>.<unique_id>`, and each
`unique_id` in `packages/` was chosen to be the id we actually want. An
allow-list of unique_ids keeps it to this stack's own YAML entities; anything
that arrived over MQTT Discovery keeps the id its discovery config gave it.

### Firewall: `DOCKER-USER`, not ufw

Docker publishes ports by writing its own iptables rules in the FORWARD path.
`ufw allow 1883` and `ufw deny 1883` are both equally ineffective against a
published container port — this surprises people, and it is exactly the trap
that turns "MQTT is firewalled" into "MQTT is open".

So the restriction lives in `DOCKER-USER`, which is the chain Docker guarantees
to consult first and never to overwrite:

```
DOCKER-USER → IOT-MQTT-LAN
                ├── 192.168.1.0/24  → RETURN   (the LAN)
                ├── 172.28.0.0/24   → RETURN   (this stack's containers)
                ├── 127.0.0.0/8     → RETURN   (doctor itself)
                └── anything else   → DROP
```

Everything is confined to one dedicated chain plus one jump, so it can be
removed without touching a rule anyone else depends on. `DOCKER-USER` was empty
before this stack was installed. Persisted by `iot-stack-firewall.service`.

### The recorder is not the archive

`recorder` keeps 400 days and records only `sensor`, `binary_sensor` and
`weather`, with the derived per-second entities excluded. That is enough for
dashboards and for a first pass at model training.

But the recorder is a convenience, not the archive. The archive is the MQTT
stream plus whatever time-series database gets attached to it. A commented
`influxdb:` block sits in `configuration.yaml` for the day that happens.

---

## Data quality is part of the architecture, not a feature

Every physical source carries: measurement timestamp, reception timestamp,
source, sensor id, last seen. The measurement timestamp is the one that matters
— a node that buffers readings during a link outage will deliver an hour-old
value that, judged by arrival time, looks perfectly fresh.

From that, three states: `fresh`, `stale`, `offline` (§7 of `MQTT.md`).
Nothing in the UI shows a value as current without its age.

The two populations are kept apart on purpose. My own station is authoritative,
declared in YAML, with stable entity ids, and a gap in its data stays visible.
Third-party RF sensors are opportunistic, arrive by MQTT Discovery, are
allow-listed one at a time, and simply go unavailable when they stop
transmitting. Merging them would let a stranger's uncalibrated sensor read as if
it were mine.

---

## External access

The tunnel terminates on the router, not on this host. WireGuard runs on the
router (udp/41495, tunnel subnet `172.16.6.0/24`), which means doctor runs no
VPN daemon at all and every device on the LAN is reachable through one tunnel
rather than one host being reachable through one agent.

That choice costs this stack exactly one rule. Home Assistant listens with host
networking, so ufw governs 8123 and already accepts it from any source that can
route to the host — a tunnelled client included. The broker is different: 1883
is a *published Docker port*, and Docker publishes ports by writing rules in the
FORWARD path where ufw does not apply. Traffic arriving from the tunnel would
reach the host and then be dropped by `DOCKER-USER`. So the tunnel's range is
listed in `firewall/allowed-subnets.conf`, which the boot-time unit re-reads.

Verified end-to-end on 2026-08-20: Home Assistant opened from a phone on mobile
data through the tunnel.

Nothing is port-forwarded from the internet. No reverse proxy exists, because
with the tunnel in place there is nothing to proxy: HA is reached at its
ordinary LAN address. `TODO.md` X2 records what remains — the acceptance test
itself, which requires a phone off WiFi.

---

## Ownership and the two-layer observation model

Two questions decide how a reading is allowed into the system, and both are
answered before it becomes an entity.

**Who owns the transmitter.** A device we flashed and control is `own`; a
neighbour's 433 MHz thermometer that happens to be audible is `observed`. The
difference is not cosmetic — an `observed` reading may vanish without that being
a fault, must never drive an automation that assumes reliability, and carries no
implied right to write anything back. Reception is passive throughout: nothing
here transmits toward a device we do not own.

**Which layer it belongs in.** Radio decoders publish what they heard; they do
not publish state.

```
radio/raw/<collector>/…    what a receiver heard, not retained
        └─ decode ─► sensors/<collector>/<id>/<metric>    our devices, retained
                └─ normalize + dedup ─► observed/<identity>/<metric>
```

The identity `<source_type>_<protocol>_<model>_<transmitter_id>` is derived from
what the transmitter emits, so it survives a reboot of the receiver and stays
stable when a second collector hears the same device. Deduplication must key on
transmitter-emitted bytes only — never on RSSI, hop count, path or receive
time, which differ per receiver by definition.

`observed/…` is a separate top-level tree rather than a branch of `sensors/…`
for a reason that cost a rewrite to learn: Mosquitto has no deny rules, so a
subtree of a granted tree cannot be taken back. A collector holding
`write sensors/#` could write normalized state no matter what the documentation
claimed. Separate tree, separate grant. Details in `MQTT.md` §12–13, reasoning
in `DECISIONS.md` D-003 and D-005.

---

## What is deliberately not here yet

- **The weather engine.** Directory, container, config, MQTT account, topic
  contract and Home Assistant entities all exist. The model does not, and there
  is no placeholder output — an invented rain risk on a dashboard is worse than
  an empty field, because it looks like an answer.
- **rtl_433.** Behind a Compose profile, off by default, no SDR attached.
- **A time-series database.** The seam is prepared; adding one before there is
  data to put in it would be building the wrong thing first — `DECISIONS.md`
  D-006.
- **The normalizer.** `observed/…`, the identity format and the metadata
  contract are fixed, because those are the expensive things to change later.
  The process that fills the tree waits for a second collector: with one
  receiver, deduplication has nothing to deduplicate.
- **Any route in from outside the house.** Not a VPN, not a proxy, not a
  forwarded port. Open decision, `TODO.md` X2.
