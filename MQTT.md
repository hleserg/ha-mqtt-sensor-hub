# MQTT topic contract

Broker: `192.168.1.51:1883` (doctor). Anonymous access is off; every account is
confined by `mosquitto/config/acl.conf`.

This document is the contract. A device that follows it needs no server-side
code to join the system.

---

## 1. Rules that apply everywhere

**Retain state, do not retain events.**

| Kind | Retained | Why |
|---|---|---|
| Last valid value of a sensor metric | **yes** | A watch that connects at 03:00 must get the current temperature immediately, not at the next transmission |
| Availability / LWT | **yes** | The broker must be able to answer "was this thing online" without the thing |
| Allow-list commands | **yes** | The list replays to a collector on every reconnect, so the collector needs no storage |
| MQTT Discovery configs | **yes** | Home Assistant must find them again after a restart |
| Raw packets, decoded events, "I heard something" announcements | **no** | They describe a moment, not a state. Retaining them resurrects devices that are long gone |

**Payloads.** Metric topics carry a bare value (`-3.8`, `84`, `1008.2`) — no
JSON, no units. An ESP32 can `atof()` the payload directly. JSON is used only
where a message is genuinely a record: `.../meta`, `.../event`,
`_discovered/...`, discovery configs.

**Timestamps** are ISO-8601 UTC with an explicit `Z`: `2026-08-20T13:05:00Z`.

**Every physical source publishes four things** — measurement timestamp,
source, sensor id, and availability. Reception time is added by Home Assistant.
Without these a stale reading is indistinguishable from a current one; see
§7.

**QoS 1** for state and commands, QoS 0 is acceptable for high-rate raw events.

**Before adding a collector, read §12 and §13.** They define who owns a reading,
how a transmitter is identified across reboots and receivers, and why a packet
someone heard is not the same thing as a temperature.

---

## 2. `weather/` — my own outdoor weather station

Authoritative source. Published by the weather collector (`weather_collector`
account). All retained.

**This namespace is a role, not a device.** It means "whatever is currently
playing the part of *the* outdoor weather station", and the weather engine reads
it by that name. Which hardware fills it may change — a bought station, a
self-built ESP32 node, several nodes each contributing the metric it measures —
without anything downstream being edited. Every other sensor of mine, including
a second outdoor thermometer, belongs in `own/` (§14) instead; the difference is
that §14 is "a sensor I own" and this is "the reading the derivations use".

| Topic | Unit | Example |
|---|---|---|
| `weather/outdoor/temperature` | °C | `-3.8` |
| `weather/outdoor/humidity` | % | `84` |
| `weather/outdoor/pressure` | hPa | `1008.2` |
| `weather/outdoor/dew_point` | °C | `-6.1` |
| `weather/outdoor/wind_speed` | m/s | `3.4` |
| `weather/outdoor/wind_gust` | m/s | `5.8` |
| `weather/outdoor/rain` | mm, cumulative | `12.40` |
| `weather/outdoor/rain_rate` | mm/h | `0.8` |
| `weather/outdoor/cloud_index` | % (0 clear … 100 overcast) | `62` |
| `weather/outdoor/lightning_distance` | km | `18` |
| `weather/outdoor/last_update` | ISO-8601 UTC | `2026-08-20T13:05:00Z` |

Reserved and already wired into the weather-engine contract, publish when the
hardware exists:

`weather/outdoor/illuminance` (lx) · `weather/outdoor/surface_temperature` (°C)

### Station housekeeping

| Topic | Payload | Retained |
|---|---|---|
| `weather/outdoor/status` | `online` / `offline` | yes — set as the MQTT **Last Will** so the broker publishes `offline` if the node drops |
| `weather/outdoor/meta` | JSON | yes |

`meta` example:

```json
{"source":"esp32-weather","sensor_id":"outdoor-01","firmware":"1.4.2","battery_pct":92}
```

`rain` is a **cumulative counter** (Home Assistant `state_class:
total_increasing`), which is what lets HA derive rain per hour and per day and
handle a counter reset. Do not publish a per-interval delta on this topic.

---

## 3. `weather_state/` — derived values

Published by the weather-engine (`weather_engine` account), retained. Nothing
except the engine writes here, and the ACL enforces that.

### Published today

| Topic | Payload |
|---|---|
| `weather_state/feels_like` | °C — wind chill, heat index or plain air temperature, whichever applies |
| `weather_state/frost_risk` | `none` / `watch` / `likely` |
| `weather_state/ice_risk` | `none` / `watch` / `likely` |
| `weather_state/data_quality` | `ok` / `partial` / `degraded` / `no_data` |
| `weather_state/computed_at` | ISO 8601 — the **measurement** time the derivation used, never the time it ran |
| `weather_state/meta` | JSON: which inputs were present, which formula produced `feels_like`, where the dew point came from |
| `weather_state/engine_status` | `online` / `offline` (LWT) |

`computed_at` is what makes the rest of this namespace safe to consume. A
derived value from a four-hour-old measurement is stamped four hours old, so a
consumer that checks age gets the truth without having to know which station,
which transport, or how many hops were involved.

Risk levels are categorical, not percentages. The rules behind them are
threshold comparisons over temperature, dew point, humidity and rain rate —
rendering that as `70 %` would claim a probability nobody computed. The rules
are written out in `weather-engine/app/derive.py` and summarized in
`DECISIONS.md` D-012.

`data_quality` grades the **inputs**, not the derivation, so it keeps updating
while the station is silent — that is the situation it exists to report. It says
nothing about the engine: if the engine itself dies, `data_quality` freezes at
whatever it last said, possibly `ok`. That is what `engine_status` is for, and
why the two are separate topics.

| Value | Meaning |
|---|---|
| `ok` | every core input present, measurement newer than `stale_after_seconds` (900) |
| `partial` | measurement older than 900 s, or a core input missing |
| `degraded` | measurement older than `offline_after_seconds` (3600), or no timestamp at all |
| `no_data` | no temperature — nothing downstream of it can be computed |

Core inputs are temperature, humidity, pressure and wind speed. Dew point, rain
rate and surface temperature are optional: they each sharpen one rule, and a
station without them still grades `ok`.

### Reserved, and deliberately not published

| Topic | Waiting for |
|---|---|
| `weather_state/condition` | cloud cover and precipitation *type*; a rain gauge cannot tell rain from snow |
| `weather_state/rain_risk` | months of local history to fit a probability against — the recorder holds days |
| `weather_state/thunderstorm` | a lightning sensor reporting strike *rate*; distance-to-last-strike cannot tell an approaching cell from a departing one |

Their Home Assistant entities exist and read `unknown`. That is the intended
state, not a bug: an empty field is honest and a fabricated forecast is not.

### Not here on purpose

- **Dew point** stays in `weather/outdoor/dew_point`. It is a measurement (or a
  direct restatement of one), it belongs to the station's namespace, and the
  engine has no write grant there. When the station omits it, the engine
  computes it internally with the Magnus formula and uses it — it does not
  republish it under a second name.
- **Pressure tendency** is computed inside Home Assistant
  (`sensor.outdoor_pressure_tendency`), because Home Assistant already holds the
  pressure history. Giving the engine its own history store to duplicate that
  would be work in exchange for a second owner of one fact. If the watches ever
  need it over MQTT, mirror it the way `meshcore/` is mirrored (D-011).

---

## 4. `sensors/` — RF/BLE collectors

`sensors/<collector>/<sensor_id>/<metric>`

`<collector>` is the collector's own name (`rf433`, `ble`, …); `<sensor_id>` is
the device id as decoded off the air (`42A7`).

| Topic | Payload | Retained |
|---|---|---|
| `sensors/<collector>/<id>/temperature` | °C | yes |
| `sensors/<collector>/<id>/humidity` | % | yes |
| `sensors/<collector>/<id>/rssi` | dBm | yes |
| `sensors/<collector>/<id>/battery_low` | `true` / `false` | yes |
| `sensors/<collector>/<id>/last_seen` | ISO-8601 UTC | yes |
| `sensors/<collector>/<id>/event` | full decoded packet, JSON | **no** |
| `sensors/<collector>/status` | `online` / `offline` (LWT) | yes |

`event` payload — the raw decode, exactly as the collector saw it:

```json
{
  "source": "rf433",
  "protocol": "example_weather",
  "device_id": "42A7",
  "temperature_c": -3.8,
  "humidity_pct": 84,
  "rssi": -71,
  "battery_low": false,
  "timestamp": "2026-08-20T13:05:00Z"
}
```

### Allow-list flow

An open 433 MHz band contains dozens of strangers' sensors. Creating an entity
for each one would make Home Assistant useless. So a device produces entities
only after being explicitly enabled, and the filtering happens **at the
collector**, not in Home Assistant:

| Stage | Topic | Retained | Who publishes |
|---|---|---|---|
| 1. announce | `sensors/<collector>/_discovered/<id>` | **no** | collector, at most once a minute per device |
| 2. enable | `sensors/<collector>/cmd/enable/<id>` | **yes** | Home Assistant (`script.rf_enable_sensor`) |
| 3. describe | `homeassistant/.../config` | yes | collector, only for enabled devices |
| 4. report | `sensors/<collector>/<id>/<metric>` | yes | collector, only for enabled devices |

The enable command is retained **per device**, so the whole allow-list replays
to the collector every time it reconnects. Disabling means publishing an empty
retained payload to the same topic, which deletes the message from the broker
outright.

Enable payload:

```json
{"device_id": "42A7", "enabled": true, "alias": "Garden 433"}
```

---

## 5. `meshcore/` — MeshCore telemetry

`meshcore/<node_id>/<metric>`, retained.

`<node_id>` is the first six hex digits of the node's public key — `044e2d`
today. It is derived from the key, so it survives reboots and changes only when
the physical node changes.

| Topic | Payload | Source |
|---|---|---|
| `meshcore/<node>/battery` | % | node self-telemetry |
| `meshcore/<node>/battery_voltage` | V | node self-telemetry |
| `meshcore/<node>/status` | `online` / `offline` | integration |
| `meshcore/<node>/last_seen` | ISO-8601 UTC | when the node last reported, **not** when the mirror ran |
| `meshcore/<node>/meta` | JSON: `node_id`, `name`, `source`, `transport`, `frequency_mhz`, `spreading_factor`, `bandwidth_khz`, `tx_power_dbm` | integration |

These are written by the `MeshCore -> MQTT mirror` automation in
`automations.yaml`, retained, at most once a minute. A metric that reads
`unknown` or `unavailable` is skipped rather than published — writing "unknown"
into a retained topic would destroy the last good value a consumer depends on.

**Not published, deliberately.** `temperature`, `humidity` and `pressure`: this
node carries no environmental sensor, and reserving empty topics for readings
that do not exist is how a bus starts lying. They come back the moment a node
with a BME280 is attached, under the same names. `rssi`: it describes one
received packet, not a node, so it belongs with the packet in
`radio/raw/meshcore/…` (§12). `latitude`/`longitude`: in Home Assistant via
`device_tracker`; nothing on the bus needs the house coordinates retained.

A MeshCore node that **is** the outdoor weather station should be mirrored into
`weather/outdoor/#` instead, so the transport can change without any consumer
noticing. See `MESHCORE.md`.

---

## 6. `rtl_433/` — optional SDR bridge

`rtl_433/<model>/<id>/<metric>`, **not** retained (raw decodes are events).

Reserved now so that plugging in an RTL-SDR later needs no renaming. The
service is off by default; see `README.md`.

---

## 7. Freshness — `fresh` / `stale` / `offline`

A value is never presented as current without its age.

Age is measured from `weather/outdoor/last_update`, the **measurement**
timestamp — not from when the message arrived. A node that buffers readings
while it has no link will deliver an hour-old measurement that, judged by
arrival time, looks perfectly fresh.

| State | Condition | Meaning |
|---|---|---|
| `fresh` | age ≤ `input_number.outdoor_stale_after` (default 900 s) | may be shown as the current weather |
| `stale` | age ≤ `input_number.outdoor_offline_after` (default 3600 s) | must be shown with its age |
| `offline` | older, or no timestamp at all | must not be shown as the current weather |

Both thresholds are adjustable from the dashboard without restarting anything.

Implemented in `homeassistant/config/packages/data_quality.yaml`:
`sensor.outdoor_data_age`, `sensor.outdoor_data_state`,
`binary_sensor.outdoor_data_fresh`, `sensor.outdoor_summary`.

External RF sensors use a different mechanism — `expire_after: 3600` in their
discovery config, which makes the entity go *unavailable* when the device stops
transmitting. That is right for a disposable third-party sensor and wrong for
my own station, where a gap must stay visible rather than blank.

---

## 8. `homeassistant/` — MQTT Discovery

Standard prefix, unchanged: `homeassistant/<component>/<node_id>/<object_id>/config`.

Used by the RF/BLE collector and by rtl_433. My own weather station is
**not** discovered — it is declared in YAML, so its entity ids are stable
forever and it exists in Home Assistant whether or not the collector is running.

---

## 9. `monitor/` — health

| Topic | Payload | Retained |
|---|---|---|
| `monitor/roundtrip` | ISO-8601 timestamp published by HA every minute | no |
| `monitor/healthcheck` | token used by `scripts/healthcheck.sh` | no |
| `monitor/backup/status` | outcome of the last backup run, JSON | **yes** |

`monitor/backup/status` is retained on purpose: the question it answers is "when
did a backup last succeed?", and that has to survive a broker restart and be
answerable by a client that has only just connected.

```json
{
  "result": "ok",
  "mode": "full",
  "archive": "iot-stack-20260820-201153-full.tar.gz",
  "size_bytes": 882701,
  "exit_code": 0,
  "finished_at": "2026-08-20T20:11:53+03:00"
}
```

`result` is `ok` or `failed`; a failed run still publishes, carrying the exit
code and `size_bytes: 0`. Home Assistant turns this into
`sensor.backup_last_run`, `sensor.backup_last_result`, `sensor.backup_age` and
`binary_sensor.backup_failing` (`DECISIONS.md` D-010). It uses the existing
`monitor` grant, so no ACL change was needed.

---

## 10. `watch/` — ESP32-S3 watches

`watch/<client_id>/#` — a watch may publish its own status here and nothing
else. It has no write access to any telemetry namespace.

---

## 11. Accounts and permissions

| Account | May read | May write |
|---|---|---|
| `homeassistant` | `weather/#`, `weather_state/#`, `sensors/#`, `own/#`, `observed/#`, `radio/#`, `meshcore/#`, `rtl_433/#`, `homeassistant/#`, `ha/#`, `monitor/#`, `$SYS/#` | `homeassistant/#`, `ha/#`, `weather_state/#`, `monitor/#`, `sensors/+/cmd/#`, `meshcore/#` |
| `weather_collector` | `weather/#` | `weather/#` |
| `rf_collector` | `sensors/#` | `sensors/#`, `homeassistant/#` |
| `watch` | `weather/#`, `weather_state/#` | `watch/<own-client-id>/#` |
| `weather_engine` | `weather/#`, `weather_state/#`, `sensors/#`, `own/#`, `observed/#`, `meshcore/#`, `rtl_433/#` | `weather_state/#` |
| `meshcore` | `meshcore/#` | `meshcore/#`, `homeassistant/#` |
| `rtl433` | `rtl_433/#` | `rtl_433/#`, `homeassistant/#` |
| `monitor` | `monitor/#`, `$SYS/#`, `weather_state/engine_status` | `monitor/#` |
| `normalizer` *(inert)* | `radio/#`, `sensors/#`, `meshcore/#`, `rtl_433/#` | `observed/#`, `homeassistant/#` |
| one per own sensor, named as its `sensor_id` | `own/<its-own-name>/#` | `own/<its-own-name>/#` |

`meshcore/#` has two accounts that may write it, which is the one place this
stack breaks its own single-owner rule. It is deliberate and narrow: the radio
is owned by the integration *inside* Home Assistant, so HA is the only process
that can see mesh telemetry at all, while the `meshcore` account stays reserved
for the standalone publisher a USB repeater would need. They cannot both run —
a Companion serves one client. `DECISIONS.md` D-011.

`monitor` is otherwise kept away from telemetry entirely, and
`weather_state/engine_status` is the single deliberate exception: it is a
liveness flag published by the engine's last will, and `healthcheck.sh` needs
it because a wedged process still looks healthy to `docker ps`. It is granted
as one topic, not as `weather_state/#`.

`sensors/+/cmd/#` is the one place Home Assistant writes into a collector's
namespace, and it is required rather than optional: `script.rf_enable_sensor`
publishes the retained enable/disable commands of the allow-list flow above.
Without it the script appears to work — under MQTT 3.1.1 the broker
acknowledges the publish and drops it — and no device ever gets enabled. It was
missing from the first deployment and found exactly that way.

Anything not listed is denied. A denied publish is answered with MQTT 5 reason
code `135 Not authorized`; under MQTT 3.1.1 the broker acknowledges and
silently drops it, so test with `mosquitto_pub -V 5 -d` when verifying an ACL.

**A granted SUBACK does not mean read access.** Mosquitto accepts the
subscription — SUBACK 0 — and then filters at delivery, so the only honest test
of a read rule is whether a message actually arrives. Subscribing as `monitor`
to `weather_state/feels_like` returns SUBACK 0 and then times out with nothing;
the same subscribe to `weather_state/engine_status` returns the value.

The last two rows are not `user` blocks. `normalizer` has rules and no password
entry, which is inert — a service that does not exist yet, with its grants
written in advance so standing it up is one command. The own-sensor accounts are
covered by two **global** pattern rules (§14); they need no rules of their own,
which is the point.

Passwords are in `.env` on doctor (chmod 600, never committed).
Adding an account: `scripts/add-mqtt-user.sh <name>`, then add its rules to
`mosquitto/config/acl.conf`, then `docker compose exec mosquitto kill -HUP 1`.
An own sensor skips the middle step.


---

## 12. Source model — ownership, type, identity

Three orthogonal facts about every reading. They are separate because they
change independently: a sensor can move from `rf433` to `meshcore` without
changing owner, and an `observed` sensor can become `own` if you buy it.

### Ownership — `own` | `observed`

| | |
|---|---|
| `own` | Hardware I control. I choose its id, its transmit interval, its firmware. If it goes silent, that is a fault worth an alert. |
| `observed` | Someone else's transmitter, received passively off the air. I control nothing. It may vanish, change id after a battery change, or lie. Silence is not a fault. |

The distinction is not bookkeeping — it decides alerting, retention and how much
a value is trusted. Never alert on an `observed` sensor going quiet.

### Source type

`weather_station` · `rf433` · `rf868` · `ble` · `meshcore` · `zigbee` ·
`thread` · `wifi` · `manual` · `internet` · `derived`

The type describes **how the reading arrived**, not what it measures. It exists
so that "all my 433 MHz sensors" and "everything that came in over the mesh" are
answerable questions.

### Identity

A logical sensor keeps one identity for its whole life. The identity must be
derivable from what is on the air, with no server-side registry:

```
<source_type>_<protocol>_<model>_<transmitter_id>
rf433_acurite_tower_42a7
meshcore_lpp_bme280_044e2d
ble_atc_thermometer_a4c1382e1b9f
```

Lowercase, `[a-z0-9_]` only, and **stable across reboots of the receiver**. The
transmitter id comes from the air (rtl_433's `id`, a BLE address where it is
static, a MeshCore public-key prefix), never from an autoincrement on our side —
avoidance #5 in the brief is precisely a receiver restart inventing a new id.

Where a transmitter rerolls its id on battery change (many cheap 433 sensors
do), that is a property of the device, not something to paper over: the old
identity goes stale and a new one appears. A human decides they are the same
thing by setting an alias.

---

## 13. Raw observations vs normalized state

The rule: **a packet someone heard is not a measurement.** They are different
kinds of fact, they have different lifetimes, and they live in different
namespaces.

```
radio/raw/<collector>/<protocol>/…      what a receiver heard   — NOT retained
        │
        └── decode ─► sensors/<collector>/<id>/<metric>   per-receiver decode — retained
                            │
                            └── normalize + dedup ─► observed/<identity>/<metric>
                                                                     one logical sensor — retained
```

### Why the middle layer exists

Three receivers hearing one outdoor thermometer is **three observations of one
temperature**, not three temperatures. Without a layer where the dedup happens,
every consumer — dashboard, watch, automation, export — has to know which
collectors are duplicates of each other, and that knowledge gets copy-pasted
until it rots.

| Layer | Topic | Retained | Volume | Who reads it |
|---|---|---|---|---|
| Raw | `radio/raw/…` | no | high | debugging, propagation analysis, nothing user-facing |
| Per-collector | `sensors/<collector>/<id>/…` | yes | medium | "did *this* receiver hear it?" |
| Normalized | `observed/<identity>/…` | yes | low | dashboards, watches, automations, history |

> **Why `observed/` is top-level and not `sensors/observed/`.** It was the
> latter first, and a functional ACL test killed it: `rf_collector` holds
> `topic write sensors/#`, Mosquitto has no deny rules, and a subtree of an
> allowed tree therefore cannot be taken away — the collector could write into
> the normalized layer and the broker would accept it (`RC:16`, verified). A
> separate top-level namespace is a separate grant, so the guarantee is enforced
> by the broker rather than by a convention in this document.

### Normalized metadata

Each identity carries one retained JSON document alongside its metrics:

`observed/<identity>/meta`

```json
{
  "ownership": "observed",
  "source_type": "rf433",
  "protocol": "acurite_tower",
  "model": "Acurite-Tower",
  "transmitter_id": "42A7",
  "alias": "neighbour's garden",
  "collectors": ["rf433-balcony", "rf433-attic"],
  "decoder": "rtl_433",
  "decoder_version": "24.10",
  "observed_at": "2026-08-20T13:05:00Z",
  "transmitted_at": null,
  "repeat_count": 3,
  "best_rssi": -71,
  "confidence": 0.9
}
```

`repeat_count` is how many receivers contributed to this reading, and
`best_rssi` comes from whichever heard it loudest. Together with `observed_at`
they are the cheap confidence signals: a reading heard once at −110 dBm and a
reading heard by three receivers at −60 dBm are not equally trustworthy, and the
consumer should be able to tell without re-deriving it.

### What is not built yet

The normalizer itself. Today nothing writes `observed/…` — there is one
collector, so there is nothing to deduplicate, and a dedup service with a single
input would be theatre. The namespace, the identity format and the metadata
contract are fixed now because they are what is expensive to change later; the
service is a NEXT item in `TODO.md`.

### Where MeshCore packets go

`meshcore-ha` can publish a packet feed to MQTT. When that is turned on it
publishes to a configurable template, and it will be pointed at
`radio/raw/meshcore/<pubkey>/packets` — the raw layer, not `meshcore/…`. The
`meshcore/<node>/<metric>` namespace in §5 stays what it is: *measurements* that
happened to arrive over the mesh. Same rule as everywhere else — transport and
measurement do not share a namespace.

---

## 14. `own/` — my own Wi-Fi sensors

Symmetric with `observed/` (§13), and top-level for the same reason: ownership
is the axis that decides how a reading is treated, so it deserves a grant of its
own rather than a convention inside somebody else's tree.

```
own/<sensor_id>/<metric>
```

`<sensor_id>` is lowercase `[a-z0-9-]`, stable for the life of the hardware, and
descriptive of *where the thing is* rather than what it is made of —
`greenhouse`, `cellar`, `garage-north`. `esp32-c5-02` is a bad id: replace the
board and the identity should not move.

| Topic | Payload | Retained |
|---|---|---|
| `own/<id>/<metric>` | bare number, no unit | yes |
| `own/<id>/last_update` | ISO-8601 UTC of the **measurement** | yes |
| `own/<id>/status` | `online` / `offline` — the MQTT Last Will | yes |
| `own/<id>/meta` | JSON, published once on connect | yes |
| `own/<id>/event` | JSON, one-off occurrences | **no** |

Metric names come from the same vocabulary as everywhere else in this file
(`temperature`, `humidity`, `pressure`, `illuminance`, `co2`, `soil_moisture`,
…). A new metric name goes in this table before it goes on the air — avoidance
#3 in the brief is every sensor inventing its own format, and it starts with one
node calling it `temp`.

`meta` example:

```json
{"sensor_id":"greenhouse","board":"esp32-c5","firmware":"2026.7.4",
 "metrics":["temperature","humidity","soil_moisture"],"battery_pct":92}
```

### Why not `weather/`, and why not the ESPHome default layout

`weather/outdoor/…` is a single role with a fixed metric list that the engine
consumes (§2). A cellar hygrometer is not a weather station and must not be able
to move the number the frost rule reads.

The ESPHome `mqtt:` component has its own topic layout,
`<prefix>/sensor/<name>/state`, and it would work — the payloads are bare and
retained and the availability topic has the right shape. It is not used because
it carries no measurement timestamp and no `meta`, and because the topic then
encodes the *entity name inside one firmware* rather than the sensor's identity.
Rename a sensor in the YAML and the topic moves. Per-entity `state_topic:`
overrides cost four lines and remove both problems — see
`esphome/own-sensor-reference.yaml`.

### One account per sensor, enforced by a pattern rule

Each sensor gets a broker account named exactly as its `<sensor_id>`, and a
single global pattern rule scopes every one of them:

```
pattern write own/%u/#
pattern read  own/%u/#
```

Adding a sensor is therefore one command and a reload, with no ACL edit and no
opportunity to paste `own/#` into a node by accident:

```sh
./scripts/add-mqtt-user.sh greenhouse
docker compose exec mosquitto kill -HUP 1
```

Verified functionally, not by reading the file — under MQTT 5, where a refusal
is explicit:

| Test | Result |
|---|---|
| `weather_collector` → `own/greenhouse/temperature` | `RC:135` not authorized |
| `weather_collector` → `own/weather_collector/temperature` | `RC:16` accepted — the pattern matches its own name |
| `homeassistant` subscribes `own/#` | receives |
| `monitor` subscribes `own/#` | receives nothing |

The second row is the documented cost of Mosquitto pattern rules being global
(`mosquitto.conf(5)`: "Pattern ACLs apply to all users even if the `user`
keyword has previously been given"). A service account can write a topic under
its own name that nothing reads. It is recorded in `acl.conf` rather than left
to be discovered.

### Entities

No entity is created from `own/#` automatically. Each sensor gets a block in
`homeassistant/config/packages/own_sensors.yaml` — explicit, reviewable, and the
same gate that keeps `sensors/#` and `rtl_433/#` from filling Home Assistant
with hundreds of rows.
