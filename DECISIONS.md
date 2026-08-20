# Decision log

Engineering traceability, not an essay. One entry per decision that would be
expensive to reverse or that someone will later ask "why is it like this?"
about. Each entry states what was chosen, the evidence, what else was on the
table, and how hard it is to undo.

Format: **DECISION / EVIDENCE / ALTERNATIVES / WHY / REVERSIBILITY**.

---

## D-001 — One application owns the Companion radio. That application is `meshcore-ha`.

**DECISION.** Home Assistant, through the `meshcore-ha` integration, is the sole
client of the Companion node over TCP. RemoteTerm is not installed. No second
process connects to `192.168.1.93:5000`.

**EVIDENCE.** Two independent sources, one of them the running hardware.

1. Firmware source. MeshCore's WiFi transport keeps exactly one client and
   evicts the previous one on any new connection —
   `src/helpers/esp32/SerialWifiInterface.cpp`:

   ```cpp
   auto newClient = server.available();
   if (newClient) {
     deviceConnected = false;
     client.stop();          // the existing client is dropped
     client = newClient;
   }
   ```

2. Measured on our own node, 2026-08-20. Socket A connects and idles; socket B
   connects; A is closed by the peer immediately:

   ```
   A connected from ('192.168.1.51', 36904)
   A before B: alive (idle, no EOF)
   B connected from ('192.168.1.51', 36906)
   A after  B: closed by peer (EOF)
   B after  B: alive (idle, no EOF)
   ```

3. RemoteTerm's own README, on what it does to a radio it is given:

   > "RemoteTerm does *full* management of the radio, meaning that once a radio
   > is connected to RemoteTerm, all contacts/channels will be imported and
   > offloaded to RemoteTerm and the contacts actually synced to the device will
   > be governed by RemoteTerm."

   It also refuses to start with more than one transport configured.

4. `meshcore-ha` is not a passive reader either — it calls
   `set_manual_add_contacts(True)` on connect (`docs/docs/contacts.md`). Our node
   reported `manual_add_contacts: false` before the integration was configured.

**ALTERNATIVES.**
- *RemoteTerm owns the radio, HA is fed through MQTT Discovery.* Viable on
  paper: RemoteTerm has a richer UI, bots and packet storage. Rejected for now —
  it takes over contact/channel state on the device, its bots execute arbitrary
  Python (its own README calls the app "for trusted environments only"), and it
  would put a second large service between us and the radio for a benefit we do
  not need yet.
- *Both, simultaneously.* Not an option. Point 2 is not a race condition, it is
  the documented behaviour of the transport: the two would evict each other in a
  loop for as long as both ran.

**WHY.** The goal is HA entities and automations. `meshcore-ha` produces those
directly, and it also publishes a packet feed to MQTT, so choosing it does not
close the door on the live map. Between two stateful owners, pick the one whose
output we actually need.

**REVERSIBILITY.** Easy. Delete the config entry (or disable the integration),
and the TCP slot is free within seconds. Nothing about the choice is baked into
the topic tree or the storage layer. Switching to RemoteTerm later means
undoing one config entry, not a migration.

### Consequence you will notice

**While Home Assistant is connected, the MeshCore phone app cannot use this node
over WiFi.** It is the same single slot. If the phone connects, HA is evicted and
reconnects, which evicts the phone — the two will fight until one gives up.

To hand the radio back temporarily:

```sh
# Home Assistant → Settings → Devices & Services → MeshCore → Disable
# or, from the shell, stop HA entirely:
docker compose stop homeassistant
```

If both need to work at once, the answer is a second Companion node, not a
second client.

---

## D-002 — Contact discovery runs in `data_only` mode, not the default.

**DECISION.** The MeshCore config entry is created with
`contact_discovery_mode: data_only`.

**EVIDENCE.** Our node sees a dense mesh — the probe listed **168 contacts**
(Zelenograd-area repeaters and clients). The integration's default is
`MODE_FULL` (`const.py`), which upstream describes as:

> "every discovered contact gets its own diagnostic binary sensor… On dense
> meshes this avoids hundreds of low-utility binary sensors sitting permanently
> in the `discovered` state, and the entity-registry churn their
> create/evict/cleanup drives."

**ALTERNATIVES.** `full` — rejected, 168+ permanently-"discovered" entities is
exactly the failure mode this project set out to avoid. `off` — rejected, it
discards the data as well as the entities, and we want the aggregate view.

**WHY.** Discovered contacts stay inspectable as data (the summary sensor and
the `get_discovered_contact` service still work); contacts deliberately *added*
to the node keep full entities. That matches the firmware's own two-tier model.

**REVERSIBILITY.** Trivial — one dropdown in the integration's global settings.

**OUTCOME, 2026-08-20 — the decision did not achieve what it was taken for.**
After a reload the registry held **171** per-contact entities anyway. The
reasoning above has the mechanism right but the population wrong: `data_only`
suppresses entities for *discovered* contacts, while contacts *added* to the
node get one in every mode (`binary_sensor.py`, `create_contact_binary_sensor`,
which returns early only when the public key is absent from
`coordinator._contacts`). On this node essentially every contact is added — the
firmware adds what it hears — so the gate never closed. All 168 read
`unavailable` permanently, because `available` is `bool(self._contact_data)` and
the coordinator's map does not carry the entry the sensor looks up.

Containment, in `scripts/prune-meshcore-entities.sh` and the recorder config:

- the 168 are **disabled** in the entity registry, not deleted — deleting only
  makes the integration recreate them on the next reload, while a disabled
  entity stays disabled and produces no state;
- `binary_sensor.meshcore_*_contact` is excluded from the recorder, which is
  the part that survives a node swap and any future growth of the address book;
- the node's own contact list is **not** touched. It is the owner's address
  book on hardware they are taking back, not ours to prune.

Registry after: 265 entities down to 95, of which 27 are MeshCore. The three
`select.meshcore_*_contact` recipient pickers match the same `_contact_`
substring and are explicitly excluded from the filter — disabling those would
break sending messages, which is the opposite of a cleanup.
Switching modes leaves old entities behind until the next cleanup, so run the
"Clear Discovered Contacts" service after changing it.

---

## D-003 — MQTT keeps two layers: raw radio observations and normalized sensor state.

**DECISION.** Radio-level and measurement-level data live in separate namespaces
and are never mixed:

```
radio/raw/<collector>/<protocol>/...      transport-level, what a receiver heard
sensors/observed/<identity>/<metric>      one logical sensor, after dedup
weather/outdoor/<metric>                  our own station (unchanged)
weather_state/<derived>                   computed state (unchanged)
meshcore/<iata>/<pubkey>/packets          mesh transport, produced by meshcore-ha
```

**EVIDENCE.** The concrete case that forces it: one outdoor thermometer heard by
three receivers is three *observations* and one *temperature*. If both live in
the same namespace there is nowhere to put the dedup, and `sensors/x/temperature`
has to mean two different things depending on who wrote it. The same split is
already visible upstream — `meshcore-ha` publishes packet-level data
(`raw`, `RSSI`, `SNR`, `hash`, `path`) under `meshcore/…/packets` and never
pretends a packet is a measurement.

**ALTERNATIVES.** One flat namespace per collector — simpler today, but every
consumer would have to know which collectors are duplicates of each other, and
that knowledge would end up copy-pasted into dashboards and scripts.

**WHY.** Retention and semantics differ. Raw observations are transient, noisy,
high-volume and interesting mostly in aggregate; normalized state is retained,
low-volume and is what a watch face or an automation reads. Different lifetimes
belong in different namespaces.

**REVERSIBILITY.** Moderate. Changing it later means republishing retained
topics and updating every subscriber — cheap now while the only producers are
our own scripts, expensive after real devices are flashed. That is precisely why
it is being decided now rather than later.

---

## D-004 — No `meshcoretomqtt`. It does not fit this hardware.

**DECISION.** `Cisien/meshcoretomqtt` is not installed.

**EVIDENCE.** From its README and tree: it reads a **serial** port
(`bridge/serial_connection.py`, `serialPorts = ["/dev/ttyUSB0"]`), expects a
**repeater physically connected over USB**, and requires a **custom firmware
build** with `-D MESH_PACKET_LOGGING=1` / `-D MESH_DEBUG=1`. Our node is a
*Companion* on *WiFi/TCP*, running a released binary from the low-power firmware
project — none of the three preconditions hold.

**ALTERNATIVES.** Flash a spare board as a logging repeater and wire it to
doctor by USB. That is a hardware task, not a software one, and it buys radio
observability we have no use for until there is a mesh problem to debug.

**WHY.** Installing a service that cannot connect to anything is not
preparation, it is clutter.

**REVERSIBILITY.** Trivial — nothing was done.

---

## D-005 — rtl_433 publishes to MQTT itself. We are not writing a parser.

**DECISION.** When an SDR appears, RF sensor ingestion uses rtl_433's built-in
MQTT output. No custom stdout parser.

**EVIDENCE.** rtl_433 ships `src/output_mqtt.c` and an official
`examples/rtl_433_mqtt_hass.py` for Home Assistant discovery. Our
`rtl_433/rtl_433.conf` already uses it:

```
output mqtt://mosquitto:1883,user=rtl433,pass=…,retain=0,devices=rtl_433[/model][/id]
report_meta level      # RSSI/SNR come along for free
convert si
```

**ALTERNATIVES.** `WatchDogsGo/watchdogs/sdr_manager.py` runs `rtl_433 -F json`
as a subprocess and parses stdout into a `Sensor433` dataclass. Rejected as an
implementation — it re-solves what upstream already does, and would be one more
homegrown script in the data path.

**WHY.** Upstream's MQTT output is maintained, handles reconnection, and already
emits the `[/model][/id]` topic structure we want for identity.

**What we did take from WatchDogsGo** — the data model, not the code:
`sid = model + id` as the stable composite identity, plus `last_seen` and
`count` (how many times a transmitter has been heard) as cheap confidence
signals. Those three fields answer "is this the same sensor as yesterday?"
without any protocol work.

**REVERSIBILITY.** Easy — it is a config line, and no SDR is attached yet.

---

## D-006 — Long-term storage stays on HA Recorder for now.

**DECISION.** No InfluxDB, no Prometheus, no TimescaleDB. The HA recorder
database is the archive, and the export path is a documented backlog item rather
than a running service.

**EVIDENCE.** The recorder database is currently **774 KB** — it holds today.
The only continuous producer is a simulator. doctor has 806 GB free, so storage
pressure is not the constraint; there is simply no data yet to warrant a second
storage engine.

**ALTERNATIVES.** Stand up InfluxDB now "because we will need it". Rejected —
that is avoidance #7 from the brief ("огромный distributed stack появляется до
появления первого датчика"), and an empty time-series database still has to be
backed up, upgraded and monitored.

**WHY.** The decision that actually matters for the future is *naming and
retention*, not the engine. Stable topic identity and a stable entity id mean a
later export into any store is a straight copy. Recorder's own purge settings
can be tuned long before it becomes a problem.

**REVERSIBILITY.** Easy in one direction: adding a long-term store later loses
nothing, because recorder keeps the history in the meantime. The irreversible
mistake would be *renaming entities* later, which is why D-003 and entity-id
normalization come first.

---

## D-007 — The live map is deferred, and its compatibility is not assumed.

**DECISION.** `meshcore-mqtt-live-map` is not installed yet. It is a NEXT item,
gated on one verification.

**EVIDENCE.** The pieces do line up: it subscribes to `meshcore/#` by default
(`MQTT_TOPIC = os.getenv("MQTT_TOPIC", "meshcore/#")`), which matches what
`meshcore-ha` publishes (`meshcore/{IATA}/{PUBLIC_KEY}/packets`), and its decoder
accepts a hex packet under any of `hex, raw, packet, …` — `meshcore-ha` sends
`raw`. So the main path should work.

**One mismatch found, and not hand-waved.** The map reads signal metrics as
`obj.get("rssi")` / `obj.get("snr")` — lowercase, with no key-case
normalization anywhere in `decoder.py`. `meshcore-ha` publishes `"RSSI"` and
`"SNR"`. On the JSON-coordinate path those fields will come through as `None`.
Whether that matters for the hex-packet path is **unverified** — it needs a real
feed to answer, not a reading of the source.

**ALTERNATIVES.** Write a topic-rewriting shim between the two. Rejected for
now: gluing two projects together before either is running is how you end up
maintaining the glue forever.

**WHY.** The map is a dashboard. Avoidance #8 in the brief — a broken optional
dashboard must not touch data collection — argues for turning it on only after
the feed it consumes is proven to exist.

**REVERSIBILITY.** Trivial; it is a separate container reading MQTT read-only.

---

## D-008 — The firmware repository is documentation, and is treated as such.

**DECISION.** Compatibility claims about the Companion firmware come from
measurements against the node, not from the firmware repository.

**EVIDENCE.** `dt267/MeshCore-Low-Power-Firmware` contains no source — the whole
tree is nine files: `README.md`, four guides, `LICENSE`, and release binaries
(48 assets per release, current `v1.17.dev_0816`). The transport internals cited
in D-001 come from upstream `meshcore-dev/MeshCore`, which the fork is built
from; **that the fork did not change them is an assumption**, and the reason
D-001 also carries a live measurement.

**What the firmware docs do state, and we rely on:** WiFi is one of three
companion transports selected with `set conn.mode wifi|ble|usb`, the node joins
as a STA and shows its IP and port on the home screen, static IP is supported
(`set wifi.ip`), and mDNS exposes it as `meshcore-xxxx.local` where `xxxx` is the
first two bytes of the public key.

**WHY.** "Не выдумывай совместимость" — the only honest way to make claims about
a binary is to talk to it.

**REVERSIBILITY.** n/a — this is a working practice, not a deployment.

## D-009 — Deduplication keys on transmitter bytes only, never on reception metadata.

**DECISION.** When the normalizer is built, the dedup key is derived
exclusively from what the transmitter emitted. Everything the *receiver* adds —
RSSI, SNR, hop count, path, receive timestamp, which collector heard it — is
excluded from the key and kept only as metadata.

**EVIDENCE.** `WatchDogsGo/watchdogs/lora_manager.py` solves exactly this
problem for a mesh where every node rebroadcasts. Its key is the header byte
plus the payload, and it explicitly skips the path field, which grows by one
hash on every hop:

```python
path_byte = data[offset]
hash_count = path_byte & 0x3F          # hops
offset += 1 + hash_count * hash_size   # skip path data
payload = data[offset:]
dedup_key = bytes(data[:1]) + bytes(payload)   # header + payload, no path
```

with a 30-second window and pruning above 50 entries. It also pre-seeds the
cache with its own transmissions (`_preseed_dedup`), so hearing your own packet
come back is recognised as a delivery confirmation rather than counted as a new
observation.

**ALTERNATIVES.** Keying on the whole received frame — the obvious first
implementation — is wrong in a way that only shows up in production: the same
transmission heard by two collectors, or relayed one extra hop, produces two
different keys and therefore two "observations" of one event. Keying on decoded
values instead is also wrong: a thermometer that legitimately reports the same
temperature twice would have the second reading discarded.

**WHY.** This is avoidance #4 from the brief — raw packets must not become
entities without identity and deduplication. The rule generalises past LoRa: an
rtl_433 device heard by two dongles has the same relationship between what was
transmitted and what was measured about the reception.

**REVERSIBILITY.** Free today — the normalizer does not exist yet, and this
fixes its contract before it is written. That is the point: `MQTT.md` §13
already reserves `repeat_count` and `best_rssi` for exactly the metadata this
rule pushes out of the key.

---

## D-010 — Backups are scheduled, privileged, and report their own failure.

**DECISION.** A daily `--full` archive at 04:30 into `/mnt/backup/iot-stack`,
keeping 14. The `--full` mode re-executes itself through sudo. Every run, pass
or fail, publishes a retained status message to `monitor/backup/status`.

**EVIDENCE.** All three properties come from things that were measured, not
anticipated:

- `--full` had never once succeeded. Home Assistant writes `.storage/auth`,
  `auth_provider.homeassistant`, `http`, `onboarding` and `core.uuid` as
  `-rw------- root root`; tar running as `sergey` aborts with exit 2 on the
  first of them. The mode whose entire purpose is capturing credentials could
  not read the credentials.
- The archive was 5.5 MB, of which 4.9 MB was `tools/meshcore-venv` — 1170
  files of reinstallable dependencies. After excluding it: 864 KB, 141 files,
  still containing `.env`, the broker password file, all five root-owned
  `.storage` files and the 1.8 MB recorder database.
- The first status publish used `mosquitto_pub -W`. That option belongs to
  `mosquitto_sub`; the command was invalid and the `|| true` guard hid it.

**ALTERNATIVES.** Running the whole cron entry as root would also solve the
permission problem, but leaves a root-owned archive that the operator cannot
copy off the box without sudo — and the file's protection is its mode, not its
owner. Running only `--config` on a schedule was rejected because it omits the
recorder database, which is the one irreplaceable artifact here. Reporting into
a log file only was rejected because nobody reads a log that is usually empty.

**WHY.** A scheduled backup that fails quietly is worse than no backup, because
it converts "I have no backups" into "I believe I have backups". The three
defects above would each have produced exactly that. The status topic reuses
the existing `monitor/#` grant, so this added no ACL surface.

**REVERSIBILITY.** Trivial: delete `/etc/cron.d/iot-stack-backup`. The script
remains usable by hand, and the status publish is best-effort — it can never
fail a backup.

---

---

## D-011 — Home Assistant is the publisher for `meshcore/`, and the namespace has two writers.

**DECISION.** The `homeassistant` account is granted `topic write meshcore/#`,
and an automation republishes an allow-list of node metrics from the
integration's entities into `meshcore/<node_id>/<metric>`, retained.

**EVIDENCE.** The MeshCore radio is owned by `meshcore-ha`, which runs inside
Home Assistant, and a Companion serves exactly one client (D-001). So no other
process on this host can read mesh telemetry while HA is connected. Before this
change, mesh data existed only as HA entities: `sensor.meshcore_mirror_*` had
nothing to read, and a watch or a shell script could not see the mesh at all
without a Home Assistant account.

**ALTERNATIVES.**

- *MQTT Statestream* (a core integration, so the obvious first candidate) —
  rejected on topic shape. It publishes `<base>/<domain>/<entity_id>/state`,
  which would make the bus contract a function of Home Assistant's entity
  naming. MQTT is supposed to be the layer that outlives HA, not the layer that
  mirrors its id generator.
- *The integration's own `mqtt_uploader.py`* — read, and it is not this. It is
  a LetsMesh packet-and-status feed: IATA-keyed topics, an external decoder
  command, signed auth tokens. That is the `radio/raw/…` layer (L2), not
  measurements.
- *A generic loop over `sensor.meshcore_*`* — rejected. The integration exposes
  ~190 entities on this mesh; mirroring all of them moves an entity explosion
  into topic space, which is the same failure with extra steps.
- *Publishing under `ha/…` instead*, where HA already had a write grant and no
  ACL change would have been needed — rejected. Consumers should not have to
  know which process happened to relay a measurement.

**WHY.** The mirror keeps MQTT the interface every consumer reads, even for the
one source whose data physically arrives through Home Assistant. Nodes are
discovered from the entity ids rather than configured, so the T114 handover
needs no edit: a new public key simply starts publishing under its own node id.

The cost is honest and worth naming: `meshcore/#` now has two accounts that may
write it, `homeassistant` and `meshcore`. That breaks the single-owner rule
everywhere else in this stack. It is bounded by the fact that the two can never
run at once — the `meshcore` account exists for a standalone publisher fed by a
USB repeater, which would require the radio HA is holding.

**REVERSIBILITY.** Delete the automation and the ACL line, HUP the broker, and
clear the retained topics with an empty retained publish. The integration's own
entities are untouched either way.

---

## D-012 — The engine publishes physics, not probabilities, and risk levels are categorical.

*2026-08-20*

**DECISION.** `weather-engine` stopped being a stub and now publishes
`feels_like`, `frost_risk`, `ice_risk`, `data_quality`, `computed_at` and
`meta`, retained, under `weather_state/`. It is part of the default stack
rather than an optional profile. Three of the entities that already existed —
`condition`, `rain_risk`, `thunderstorm` — stay unpublished and keep reading
`unknown`.

Two shape decisions came with it:

1. `frost_risk` and `ice_risk` changed from `%` with `state_class: measurement`
   to the categorical `none` / `watch` / `likely`.
2. `computed_at` carries the **measurement** timestamp of the inputs, not the
   time the engine ran.

**EVIDENCE.**

- Every published quantity has a closed form or a stated threshold rule:
  Environment Canada / JAG-TI wind chill (valid ≤ 10 °C and ≥ 4.8 km/h),
  Rothfusz heat index (valid ≥ 26.7 °C and ≥ 40 % RH), Magnus-Tetens dew point
  as an internal fallback. All of them are unit-tested against published
  reference values in `derive.py`, runnable as
  `docker compose run --rm weather-engine python /app/main.py --selftest`.
- The unpublished three are not a matter of effort. `condition` needs
  precipitation *type*, which a tipping-bucket gauge cannot report. `rain_risk`
  is a probability, and fitting one needs months of local history against which
  the recorder currently holds days. `thunderstorm` needs strike *rate*;
  `lightning_distance` alone cannot separate an approaching cell from a
  departing one.
- The categorical change was free to make: `statistics_meta` in the recorder
  database held no row for any `weather_state` entity, so no long-term
  statistics existed to be broken by dropping the unit.
- Both freshness paths were exercised on the live broker rather than reasoned
  about. With the simulator's last sample 4 h 55 m old the engine published
  `data_quality: degraded`, `computed_at: 2026-08-20T13:33:42+00:00` — a
  four-hour-old stamp on a value it had just computed. After one fresh sample
  it published `ok` with a stamp 31 s old.

**ALTERNATIVES.**

- *Publish `rain_risk` from a simple rule* (falling pressure + high humidity) —
  rejected. It would be an if-statement presented as a probability on a
  dashboard, which is exactly the "фиктивная ML-модель" that was ruled out at
  the start. Rules can be argued with; a percentage invites trust it has not
  earned.
- *Keep the `%` contract and emit 0 / 50 / 100* — rejected for the same reason,
  with the added problem that `state_class: measurement` would have fed those
  three numbers into long-term statistics as if they were measurements.
- *Compute everything inside Home Assistant templates* — rejected. It works
  today and stops working the moment any of this needs history, a fitted model
  or a library. The container also gives the derivation its own restart
  boundary and its own log.
- *Republish dew point under `weather_state/`* — rejected. It is a station
  measurement; two topics for one fact is exactly the ambiguity the namespace
  design exists to prevent. The engine computes it internally when the station
  omits it and reports which source it used in `meta`.
- *Move pressure tendency out of Home Assistant into the engine* — rejected.
  Home Assistant already holds the pressure history; the engine would need its
  own store to do worse.
- *Gate publishing on freshness — publish nothing while inputs are stale* —
  rejected in favour of stamping. A consumer that reads a value and its
  measurement time can decide for itself; a consumer that sees nothing cannot
  tell "stale" from "engine dead". `engine_status` answers the second question
  separately, over the last-will.

**WHY.** The rule that makes this stack trustworthy is that a number never
appears without a way to tell how old it is and where it came from. Extending
that to derived values means the derivation inherits the age of its inputs
instead of resetting it — which is why `computed_at` is the measurement time,
and why the freeze-and-age behaviour is the same one the MeshCore mirror has.
Refusing to publish the three that would need a model is the same rule applied
to provenance rather than to age.

Promoting the container out of the `engine` profile follows from it now doing
something. Nothing in the data path depends on it: it reads `weather/#` and
writes `weather_state/#`, so if it dies, derived values freeze and no
measurement is lost — which is the ordering the whole stack is built around.

**REVERSIBILITY.** Stop the container and its outputs freeze with their last
values and stamps; `weather_state/engine_status` flips to `offline` through the
last will within the keepalive. To remove it entirely: `docker compose stop
weather-engine`, clear the seven retained topics with empty retained publishes,
and the Home Assistant entities go back to `unknown`. The `%` contract for the
two risk entities is three lines in `packages/weather_state.yaml`.

---

## D-013 — Own Wi-Fi sensors get a top-level namespace, and data quality is graded against a declared set.

*2026-08-20*

**DECISION.** Two changes, driven by one change of plan on the owner's side.

1. A new top-level MQTT namespace `own/<sensor_id>/<metric>` for the owner's own
   Wi-Fi sensors, symmetric with `observed/`. Write access is granted by two
   **global** pattern rules — `pattern write own/%u/#` and `pattern read own/%u/#`
   — so each sensor gets a broker account named as its `sensor_id` and reaches
   its own subtree and nothing else. `weather/outdoor/…` is unchanged and is
   restated as a *role* — the reading the derivations consume — rather than a
   device.
2. `weather_state/data_quality` is graded against a config-declared
   `expected_inputs:` list instead of a hard-coded core set. `temperature`
   remains mandatory in code, because nothing derives without it.

**EVIDENCE.**

The owner will not buy a commercial weather station; he is building sensors
himself, incrementally, on **ESP32-C5**. Three facts follow from the hardware and
they are what forced both changes:

- The C5 has 2.4/5 GHz Wi-Fi 6, BLE 5 and 802.15.4, and **no sub-GHz radio**
  (Espressif product page). Own sensors therefore arrive over Wi-Fi, directly on
  the broker. They cannot and will not pass through the 433 MHz node at
  `192.168.1.48` — that node is the `observed` half and nothing else.
- ESPHome supports the C5 (`VARIANT_ESP32C5` is in `esp32/const.py`, board key
  `esp32-c5-devkitc-1` is in `boards.py`) but **ESP-IDF only** — it is absent
  from `ARDUINO_ALLOWED_VARIANTS`. C5 v1.0 silicon needs IDF ≥ 5.5.2.
- Incremental building is the normal case, and it broke the grader. With a
  hard-coded core set of temperature/humidity/pressure/wind_speed, a node that
  measures the first three would read `partial` for however many months pass
  before an anemometer exists. That is the identical failure already fixed once
  for `surface_temperature` on 2026-08-20: a badge that is permanently yellow is
  wallpaper, and stops being read.

On the ACL mechanism, from `mosquitto.conf(5)`: *"Pattern ACLs apply to all users
even if the `user` keyword has previously been given."* The manual's own example
is `pattern write sensor/%u/data`, which is this case exactly. Verified
functionally rather than by reading, under MQTT 5:

| Test | Result |
|---|---|
| `weather_collector` → `own/greenhouse/temperature` | `RC:135` not authorized |
| `weather_collector` → `own/weather_collector/temperature` | `RC:16` accepted |
| `homeassistant` subscribes `own/#` | receives |
| `monitor` subscribes `own/#` | receives nothing |

The grader was verified live in all three states it can now reach: a four-input
declaration with wind present → `ok`; the retained `weather/outdoor/wind_speed`
cleared and the engine restarted, still declaring four → `partial`, with
`expected_missing: ["wind_speed"]` and `feels_like_method` correctly falling back
from `wind_chill_jag_ti` to `air_temperature`; the declaration cut to three →
`ok`, while `inputs_missing` still honestly lists `wind_speed`. A typo in the
list makes the engine refuse to start (exit 1), and an expected input with no
topic under `inputs:` logs a warning at startup.

**ALTERNATIVES.**

*Put own sensors under `weather/<place>/…`.* Rejected. `weather/#` is one grant;
every sensor sharing it could overwrite the weather station's numbers, and a
cellar hygrometer would be one topic away from the frost rule. The `observed/`
precedent decided this: a separate top-level namespace is a separate broker-
enforced grant, not a convention in a document.

*One explicit `user` block per sensor instead of a pattern.* Rejected, and it is
the closer call. An explicit block is narrower — the pattern's global scope means
`rf_collector` can write `own/rf_collector/...`, a stray topic nothing reads.
Against that, a hand-written grant per sensor is a hand-written mistake per
sensor, and the mistake available is pasting `own/#`. The residual cost is
recorded in `acl.conf` rather than left to be discovered.

*Use ESPHome's native API instead of MQTT.* Rejected — it makes Home Assistant
the entry point and MQTT a mirror of it, which is D-011's inversion repeated on
purpose after having been paid for once.

*Use ESPHome's default MQTT topic layout* (`<prefix>/sensor/<name>/state`).
Rejected: it carries no measurement timestamp and no `meta`, and the topic
encodes the entity name inside one firmware, so renaming a sensor in YAML moves
its topic. Per-entity `state_topic:` overrides cost four lines and remove both
problems.

*Make `data_quality` grade everything the engine can read.* That is what it did,
and it is what produced the permanent `partial`. Rejected for the second time.

**WHY.** Ownership is the axis that decides how a reading is treated — whether
silence is a fault, whether the value is trusted, how long it is kept (§12). It
already had a vocabulary and no namespace. Giving it one costs two lines of ACL
and makes the broker, rather than a convention, the thing that stops a sensor
writing outside itself.

The grader change is the same principle in a different place: quality must answer
*did I get what this station promises*, not *did I get everything imaginable*. The
first question has a useful answer on a station being built one sensor at a time;
the second answers `partial` forever and teaches the owner to ignore it.

**REVERSIBILITY.** High for both. The namespace is additive — nothing published
today moves, no entity changes, and deleting the two pattern lines revokes it
entirely. `expected_inputs:` defaults to the previous hard-coded set when absent,
so removing the key restores the old behaviour exactly; `meta` gained
`expected_inputs` and `expected_missing` and lost `core_missing`, which nothing
outside the engine consumed. Not reversible for free: broker accounts already
created, which is `scripts/add-mqtt-user.sh` in reverse.
