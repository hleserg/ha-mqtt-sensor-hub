# Adding a sensor

Five cases. Read `MQTT.md` first — it is the contract every one of them follows.

| You have | Read |
|---|---|
| a self-built Wi-Fi sensor (ESP32) that is *not* the weather station | **case 5** — this is the common one now |
| a device that fills the weather-station role | case 1 |
| somebody else's transmitter you picked up off the air | case 2 |
| a MeshCore node | case 3 |
| a new kind of publisher entirely | case 4 |

The numbering is historical and stays put: `SENSORS.md §2` is referenced from
`MQTT.md`, from `acl.conf`, and from the RF node's own repository. Renumbering
to put the common case first would break all three for a cosmetic gain.

---

## 1. A device of mine that publishes to `weather/outdoor/…`

The station's entities already exist in Home Assistant
(`homeassistant/config/packages/weather_outdoor.yaml`). Nothing to configure —
just publish.

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
   `{"source":"esp32-weather","sensor_id":"outdoor-01","firmware":"1.4.2"}`

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
