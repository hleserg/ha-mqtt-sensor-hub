# Adding a sensor

Five cases. Read `MQTT.md` first — it is the contract every one of them follows.

| You have | Read |
|---|---|
| a self-built Wi-Fi sensor (ESP32) that is *not* the weather station | **case 5** — this is the common one now |
| a device that fills the weather-station role | case 1 |
| somebody else's transmitter you picked up off the air | case 2 |
| a MeshCore node | case 3 |
| a Zigbee device you can put into pairing mode | **case 6** — the one that needs no configuration here |
| a new kind of publisher entirely | case 4 |

The numbering is historical and stays put: `SENSORS.md §2` is referenced from
`MQTT.md`, from `acl.conf`, and from the RF node's own repository. Renumbering
to put the common case first would break all three for a cosmetic gain.

---

## 1. A device of mine that publishes to `weather/outdoor/…`

The station's entities already exist in Home Assistant
(`homeassistant/config/packages/weather_outdoor.yaml`). Nothing to configure —
just publish.

**There is a worked example now.** `esphome/weather-outdoor.yaml` is a real
node filling this role: a XIAO ESP32C3 with an SHT30 for temperature and
humidity and a BME280 for pressure, both on short probe leads. It does
everything in the list below and is the shortest path to a second station —
copy it rather than re-deriving the contract. What to solder and in what order
is in `esphome/weather-outdoor-build.html`.

**Credentials:** the `weather_collector` account. Get the password with:

```sh
grep WEATHER_COLLECTOR /home/sergey/iot-stack/.env
```

**What the device must do:**

1. Connect to `192.168.1.51:1883` with that username and password.
2. Set a **Last Will**: topic `weather/outdoor/status`, payload `offline`,
   retained, QoS 1.
3. On connect, publish `online` retained to the same topic.
4. For each reading, publish the bare value **retained**, QoS 1:
   `weather/outdoor/temperature` ← `-3.8`, and so on.
5. Publish `weather/outdoor/last_update` with the ISO-8601 UTC time **of the
   measurement**.
6. Once at startup, publish `weather/outdoor/meta`:
   `{"source":"xiao-esp32c3","sensor_id":"weather-outdoor","firmware":"2026.8.2"}`

Steps 2, 3, 5 and 6 are not optional decoration: without them Home Assistant
cannot tell a silent sensor from a calm day.

**Verify:**

```sh
cd /home/sergey/iot-stack
./scripts/test-weather-publisher.sh              # a known-good reference sample
USER=homeassistant PASS="$HA_MQTT_PASSWORD" ./scripts/mqtt-watch.sh 'weather/#' 10
```

### Adding a metric that has no topic yet

1. Add the topic to `MQTT.md` §2.
2. Add an `mqtt: sensor:` block to `packages/weather_outdoor.yaml`, copying an
   existing entry — keep `availability_topic` and the `device: *outdoor_station`
   anchor so it lands on the same device.
3. If the weather engine will need it, add it to `weather-engine/config.yaml`
   under `inputs:`.
4. `docker compose restart homeassistant`
5. `./scripts/normalize-entity-ids.sh` — otherwise the new entity gets the long
   `sensor.outdoor_weather_station_*` form instead of `sensor.outdoor_*`. Give
   it a `unique_id` equal to the entity id you want; the script does the rest.

No ACL change is needed — `weather_collector` already owns all of `weather/#`.

---

## 2. A third-party RF/BLE sensor found on the air

Deliberately opt-in, and filtered at the collector rather than in Home
Assistant. An open 433 MHz band will otherwise produce hundreds of entities.

### The flow

**a. The collector announces what it hears** — not retained, at most once a
minute per device:

```
sensors/rf433/_discovered/42A7
{"source":"rf433","protocol":"example_weather","device_id":"42A7",
 "temperature_c":-3.8,"humidity_pct":84,"rssi":-71,"battery_low":false,
 "timestamp":"2026-08-20T13:05:00Z"}
```

It shows up in Home Assistant as **RF Last Discovered** on the *External
Sensors* dashboard, with the whole payload as attributes.

**b. Allow-list it.** On that dashboard fill in:

- **Collector** — `rf433`
- **Device id** — `42A7`
- **Alias** — `Garden 433`

then run **Enable this device**. That publishes a retained command to
`sensors/rf433/cmd/enable/42A7`.

Retained *per device* on purpose: the complete allow-list replays to the
collector every time it reconnects, so the collector needs no storage of its
own and no server-side component is involved.

**c. The collector publishes discovery configs** for enabled devices only,
retained, under `homeassistant/…`. Entities appear within a second or two.

**d. From then on** it publishes retained per-metric state to
`sensors/rf433/42A7/<metric>` and non-retained raw packets to
`sensors/rf433/42A7/event`.

**The gate does not own `sensors/rf433/#` alone.** The node's own firmware uses
the same prefix for its own entities — today that is a retained
`sensors/rf433/binary_sensor/node_button/state`, published by ESPHome, which
returns on the node's next connect if you delete it. It is not a decoded device
and does not fit the contract above, which is exactly right: it is the node's
own hardware, not something the node heard. Nothing collides — a rtl_433 model
slug is never `binary_sensor` — but a `sensors/rf433/#` dump shows both, so
know which is which before hunting a bug.

**And `sensors/rf433/status` is the node's too, not the gate's.** ESPHome sets
it as birth/will, which means the *broker* writes `offline` there when the node
stops answering — a death certificate no script of ours could match. The gate
used to publish a retained `online` on every run and would have overwritten it,
showing a dead node as alive. It no longer publishes that topic at all, and the
self-test fails if any topic ending in `/status` ever reappears in a batch. The
discovery configs still point `availability_topic` at it, which is right: no
node, no reception, nothing for a third-party sensor to show. The three deaths
are caught by three different things — node by the broker's will, gate by
`last_run` going stale, either by `expire_after: 3600` on the values.

That button is worth one more line, because mislabelling it would be dangerous
in both directions. It is the local rescue control for someone standing at the
node with no network working: **short press switches WiFi networks, long press
reboots. There is no factory reset.** If the entity ever lands in Home
Assistant, it must not be labelled "reset" — either the owner is afraid to
press it, or they press it expecting a wipe that never comes.

### Try the whole flow without hardware

```sh
cd /home/sergey/iot-stack
./scripts/sim-rf-collector.sh online
./scripts/sim-rf-collector.sh announce 42A7
#   ... allow-list it from the dashboard ...
./scripts/sim-rf-collector.sh check-allowlist
./scripts/sim-rf-collector.sh discovery 42A7 "Garden 433"
./scripts/sim-rf-collector.sh publish 42A7
```

`scripts/sim-rf-collector.sh` is also the reference implementation: everything
the real firmware must do is in there, and nowhere else.

### The real collector

Two halves, one pipe, and the split is deliberate — the node is ears, rtl_433 is
the brain, the gate is the passport office:

```sh
tools/rtl433.py --once --ack --json | scripts/rf433-gate.py
```

The first half lives in `~/remote_ir_rf` (the node's own repo): it pulls the
node's journal over HTTP one capture at a time with 0.15 s between requests,
synthesises `.cu8` IQ, runs it through rtl_433 in Docker, and acknowledges the
capture **after** it is on disk, never before. A crash then re-delivers the
frame instead of losing it. `scripts/rf433-gate.py` is the second half and the
subject of this section.

**The noise filter is that rtl_433 gave the decode a name.** A line without
`model` is not a signal, it is an edge coincidence. Neither `canon_len` nor
RSSI nor burst length gets a vote: at the node's −110 dBm gate they produce
false positives, and that is measured on the node rather than assumed.

**An empty pass is a result, and it is published as one.** The node's own
measurement on 2026-09-06 (an interrupt counter taken ahead of every filter,
because an empty journal and a dead receiver look identical from outside):
~1020 edges per second on both 315 and 433.92 MHz, noise floor −100…−105 dBm.
That floor sits above the node's −110 dBm gate, so the gate is permanently
open, the stream never pauses, and the overflow watchdog discards the buffer
whole — 45 times in 150 s. A real burst leaves pauses and passes the same
watchdog. So the normal state of this pipeline is *nothing decoded*, and
`status`/`last_run` are therefore stamped **before** the empty-input return,
never after it. Otherwise `sensor.rf_collector_last_run` would read "nine
hours ago" on a perfectly healthy collector — exactly the reading it exists to
make impossible.

**The gate keeps no state of its own.** The allow-list *is* the retained
`sensors/rf433/cmd/enable/#` messages, re-read at the start of every run. Not a
saving — a requirement: a cron job that loses its state file must not resurrect
entities the owner never enabled.

The device id carries the channel, not just the model and id — `Nexus-TH` id 42
on channel 3 becomes `nexus_th_42_3`. Three channels on a weather station are
three sensors in three places, and merging them into one would average a
balcony with a fridge.

**Third-party is marked in three fields at once**, because different Home
Assistant screens show different ones: `manufacturer: third-party`, the model
string carries the band, and `via_device: rf_ble_collector`.

A decode with no measurement at all but with `code`/`button`/`cmd` is a remote,
not a sensor, and becomes an **`event` entity** — the standard Home Assistant
automation trigger, so someone else's doorbell can switch on my light. There is
a transmit path too, as of the owner's 2026-09-07 decision — see "Sending one
back" below. It replaced the earlier "passive reception only", which is why
that phrase no longer appears here.

Checking it needs neither broker nor node:

```sh
./scripts/rf433-gate.py --self-test    # weather sensor, remote, and noise
./scripts/rf433-gate.py --dry-run < hits.jsonl
```

Verified end to end against the live broker on 2026-09-06: an un-enabled device
produced one non-retained announcement and nothing else; after the retained
enable it produced five discovery configs, retained per-metric state and a
non-retained `event`, and Home Assistant created all eight entities. Values
land, not just entities — a second pass with alias `Gate Value Check` put
21.75, 48 and −72 into `sensor.gate_value_check_{temperature,humidity,rssi}`,
read back out of the recorder, and an empty pass right after it moved
`sensors/rf433/last_run` from 09:06:05Z to 09:06:53Z. Every test topic was
cleared afterwards.

Two things that measurement taught, both worth knowing before the next one:

- **`rf_collector` may write `homeassistant/#` but not read it.** Subscribing as
  the collector to check your own discovery configs returns silence that looks
  exactly like "nothing was published". Read them as `homeassistant`.
- **Home Assistant names the entity after the alias, not after `object_id`.**
  Alias `Проверка ворот` gave `sensor.proverka_vorot_temperature`. The alias the
  owner types on the dashboard is the entity id, transliterated.

### Sending one back

The owner replaced "passive reception only" on 2026-09-07: *«хочу чтобы в HA
была кнопка для отправки этого сигнала»*. So an allow-listed remote now also
gets **one `button` entity per distinct code** — a four-button fob is four
buttons, because a fob has one id and four codes.

No daemon and no per-device automation. The body `POST /rf` wants rides in the
button's own `payload_press`; pressing it publishes that body to
`sensors/rf433/cmd/send/<device_id>/<code>`, and a single automation
(`rf433_send_bridge`) forwards it to the node. Home Assistant already holds
`topic write sensors/+/cmd/#`, so the ACL needed no edit. The button config is
`retain: false` deliberately — a retained press would re-fire every entity at
every Home Assistant restart.

**The button will be missing from most devices, and that is not a fault:**

- **Rolling codes cannot be replayed at all.** KeeLoq, Somfy RTS, gates, cars:
  the counter lives in the fob, not on the air, so yesterday's recording is
  rejected today. The gate keeps a model blocklist and emits no button; the
  node's own `docs/api-rf.md` says the same, "математика в брелке, не в эфире".
- **A truncated capture carries no raw data.** The node's slot holds 256
  timings; past that the tail is lost, and transmitting the stump would put a
  fragment of a repeat burst on the air. Roughly two in five captures are
  truncated, measured by the node agent on 2026-09-07.

`repeat` defaults to **1**, not more: `raw` already contains the whole repeat
burst as the node heard it, so `repeat: 3` sends three bursts — nine or twelve
presses — rather than three repeats. `RF_TX_REPEAT` overrides it for a receiver
that genuinely needs a second burst.

### What we know about a device

The owner also asked what the node knows about each third-party device. Seven
entities, all read from **one** retained topic, `sensors/rf433/<id>/usage`:

| Entity | What it says |
|---|---|
| Сигналов всего | since the device was allow-listed |
| Сигналов за неделю / за сутки | pruned by time, not by count |
| Обычное время | the busiest local hour; attributes carry all 24 buckets |
| Насколько близко | a word, not metres — see below |
| Лучший уровень | best RSSI seen, dBm |
| Впервые услышан | first capture after allow-listing |

That topic is both the gate's memory and Home Assistant's source: one MQTT
sensor can take its state by `value_template` and its attributes by
`json_attributes_topic` from the same message, so the two cannot drift apart —
they are physically one message. Counts are computed in the gate, not in a
Home Assistant template: a template re-runs on every state change of anything,
this runs once per pass, and "last day" on the broker's clock cannot disagree
with "last week" on Home Assistant's.

**Distance is a word on purpose.** RSSI depends on the transmitter's power, its
antenna, walls and weather; the same sensor behind a wall and in line of sight
differs by twenty decibels. Metres would be an invention, so the gate publishes
the level as measured plus one of *рядом / недалеко / далеко / на пределе
слышимости*.

**Hours are local, everything else is UTC.** "Usually around six in the
evening" is a fact about a person's day, and in Moscow the three-hour offset
would put an evening visitor in the afternoon. The zone comes from `TZ` in
`.env`, not from the host's settings, so the number does not depend on where
the job ran.

**This is memory, and it is still not state that can create an entity.** The
gate keeps no file and no database; usage and the replay codes live in retained
broker messages, next to the allow-list. Losing all of it resurrects nothing —
the only thing that creates an entity is still the owner's retained
`cmd/enable`. Statistics accumulate for allow-listed devices only, so a sensor
that drove past the house once leaves nothing behind.

**Grouping is done by area, not by dashboard.** Every third-party device
carries `suggested_area: "Чужие приборы"`, so Home Assistant separates them on
the devices page, in search and in voice assistants without a line of dashboard
yaml per device.

### Removing one

**Disable this device** on the dashboard clears the retained enable command.
Then remove the entities:

```sh
./scripts/sim-rf-collector.sh undiscovery 42A7
```

An empty retained payload on a config topic deletes it from the broker, which
is what stops Home Assistant from resurrecting the entity on restart.

### Discovery configs the collector should publish

Use `expire_after: 3600` for third-party sensors so the entity goes
*unavailable* when the device stops transmitting. Correct here, and wrong for my
own station — there a gap must stay visible with its age rather than blank. See
`scripts/sim-rf-collector.sh` for a complete set.

---

## 3. A MeshCore sensor node

See `MESHCORE.md`. Short version: install the supported integration, let it
create the node's entities, then mirror them into `meshcore/<node>/<metric>` —
or into `weather/outdoor/#` if that node *is* the weather station.

---

## 4. A whole new kind of publisher

1. **Create the account:**

   ```sh
   ./scripts/add-mqtt-user.sh my_collector
   ```

   It prints a generated password. Add it to `.env` if a service needs it.

2. **Give it rules** in `mosquitto/config/acl.conf` — an account with no rules
   can connect and do nothing at all:

   ```
   user my_collector
   topic write mydata/#
   topic read mydata/#
   ```

   Grant the narrowest thing that works. Write access to `homeassistant/#` only
   if it publishes its own MQTT Discovery configs.

3. **Reload the broker** — no restart, no dropped clients:

   ```sh
   docker compose exec mosquitto kill -HUP 1
   ```

4. **Document the namespace** in `MQTT.md`. An undocumented topic tree
   is one nobody can safely change later.

5. **Verify the ACL actually holds.** Under MQTT 3.1.1 a denied publish is
   acknowledged and silently dropped, so a successful-looking `mosquitto_pub`
   proves nothing. Use MQTT 5, where denial is explicit:

   ```sh
   docker run --rm --network host eclipse-mosquitto:2.0.22 \
     mosquitto_pub -V 5 -d -h 127.0.0.1 -u my_collector -P 'PASSWORD' \
     -t weather/outdoor/temperature -m 1 -q 1
   ```

   `RC:135` means Not authorized — the ACL is working. `RC:0` or `RC:16` means
   the publish was accepted.

---

## 5. A self-built Wi-Fi sensor of mine

The default case for anything built rather than bought: an ESP32 with a probe on
it, somewhere in the house or the garden, that is not *the* weather station.

Contract: `MQTT.md` §14. Reference config: `esphome/own-sensor-reference.yaml` —
copy it, change the `substitutions:` block, change nothing else.

### What makes it different from case 1

Case 1 is a **role** — `weather/outdoor/…` is what the weather engine reads, and
exactly one device plays it. This is a **sensor** — it publishes under its own
identity in `own/<sensor_id>/…` and nothing downstream is obliged to care.

A cellar hygrometer must not be able to move the number the frost rule reads.
That is the whole distinction, and it is why they are separate namespaces rather
than a naming convention inside one.

### Pick the id first

Lowercase `[a-z0-9-]`, stable for the life of the installation, named for the
**place**: `greenhouse`, `cellar`, `garage-north`. Not `esp32-c5-02` — replace
the board and the identity must not move (avoidance #5 in the brief is exactly
this failure).

### 1. Create its account

One broker account per sensor, named exactly as the id. A global pattern rule in
`acl.conf` scopes it to its own subtree, so there is **no ACL edit**:

```sh
cd /home/sergey/iot-stack
./scripts/add-mqtt-user.sh greenhouse
docker compose exec mosquitto kill -HUP 1
```

### 2. Prove the scope before trusting it

Under MQTT 3.1.1 a denied publish is acknowledged and dropped, so a
successful-looking publish proves nothing. Ask MQTT 5, and check the **negative**
first — if this is accepted, stop and fix the ACL:

```sh
docker run --rm --network host eclipse-mosquitto:2.0.22   mosquitto_pub -V 5 -d -h 127.0.0.1 -u greenhouse -P 'PASSWORD'   -t own/somebody-else/temperature -m 1 -q 1
#   RC:135  not authorized   <- correct
#   RC:16   accepted         <- the pattern rule is not working
```

### 3. Flash and watch the bus, not the device log

```sh
./scripts/mqtt-watch.sh 'own/#' 90
```

Expect, within a minute: `own/greenhouse/status` = `online`, one retained `meta`,
the metrics, and `last_update` advancing. `last_update` missing for the first
minute is correct — the node refuses to stamp a reading until SNTP has set the
clock, because a 1970 timestamp would age every value by fifty-six years.

### 4. Test the death path

```sh
# pull the power — do not reboot cleanly, a clean disconnect never fires a will
./scripts/mqtt-watch.sh 'own/greenhouse/status' 60      # must reach `offline`
```

### 5. Only then, the entities

Nothing appears in Home Assistant on its own: `discovery: false` in the node, and
no wildcard subscription on the HA side. Create
`homeassistant/config/packages/own_sensors.yaml` on the first sensor —

```yaml
mqtt:
  sensor:
    - name: "Greenhouse temperature"
      unique_id: greenhouse_temperature
      state_topic: "own/greenhouse/temperature"
      availability_topic: "own/greenhouse/status"
      unit_of_measurement: "°C"
      device_class: temperature
      state_class: measurement
      device: &greenhouse
        identifiers: ["own-greenhouse"]
        name: "Greenhouse"
        manufacturer: "self-built"
        model: "esp32-c5 + bme280"

    - name: "Greenhouse humidity"
      unique_id: greenhouse_humidity
      state_topic: "own/greenhouse/humidity"
      availability_topic: "own/greenhouse/status"
      unit_of_measurement: "%"
      device_class: humidity
      state_class: measurement
      device: *greenhouse
```

— then:

```sh
docker compose restart homeassistant
./scripts/normalize-entity-ids.sh
```

`unique_id` equal to the entity id you want is what keeps it `sensor.greenhouse_temperature`
instead of `sensor.greenhouse_greenhouse_temperature`; the script fixes what
slips through.

**No `expire_after`.** That is right for a third-party transmitter (case 2) and
wrong here: a gap in my own sensor must stay visible with its age rather than go
blank. Same rule as the weather station.

### Feeding the weather engine from an own sensor

Only if the sensor genuinely plays a weather-station part. Point the engine at
the topic in `weather-engine/config.yaml` under `inputs:`, and — this is the part
people forget — add the metric to `expected_inputs:` in the same file. That list
is what `data_quality` is graded against; a sensor the engine reads but does not
expect never lifts the grade, and one it expects but cannot reach pins it at
`partial` forever.

Building up over time is the normal case: declare only what exists today. A node
that measures temperature, humidity and pressure is **complete** and should read
`ok`. The day the anemometer goes up, `wind_speed` joins the list.

---

## 6. A Zigbee device

The odd one out, and worth its own case precisely because it needs almost
nothing from this document. No broker account, no ACL line, no YAML, no
discovery config to write: Zigbee2MQTT owns the radio and publishes both the
state and the discovery config itself (`MQTT.md` §6b, `DECISIONS.md` D-014).

The whole procedure:

1. Permit join on, in the web UI at <http://192.168.1.51:8099>.
2. Put the device into pairing mode. Physical — a long press, usually.
3. **Rename it before doing anything else.** The friendly name becomes the
   topic segment *and* the entity id, so a rename later moves the topic and
   breaks whatever referenced it. Name it for the place, by the same rule as
   case 5: `kitchen-window`, not `aqara-1`.
4. Permit join off.

Then confirm it on the bus rather than in the UI, which is the same discipline
as every other case here:

```sh
./scripts/mqtt-watch.sh 'zigbee2mqtt/#'
```

**Where it differs from case 2.** A third-party RF sensor has to be
allow-listed, because anything within radio range would otherwise become an
entity. A Zigbee device does not, because pairing *is* the allow-list — it only
joined because someone held a button on it. Do not copy case 2's gate here; it
would be ceremony with nothing to protect against.

**What it does not get.** Freshness handling of the kind in `MQTT.md` §7 is not
wired up for these. Zigbee devices announce their own availability through the
bridge, which is adequate for a switch and *not* adequate for anything feeding
the weather engine. If a Zigbee sensor ever becomes an input to a derived value,
give it the case 5 treatment first.

---

## Checklist for any new sensor

- [ ] Publishes **retained** state, **non-retained** events
- [ ] Bare numeric payloads on metric topics (no JSON, no units)
- [ ] Last Will configured on its `status` topic
- [ ] Publishes a measurement timestamp, not just a value
- [ ] Its account can write only its own namespace
- [ ] Topics documented in `MQTT.md`
- [ ] Verified with `mqtt-watch.sh` that the data is actually on the broker
- [ ] A fresh subscriber gets the last value immediately
- [ ] For a YAML-declared entity: `normalize-entity-ids.sh` re-run, entity id short
