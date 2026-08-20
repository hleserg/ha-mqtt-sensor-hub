# Acceptance report — iot-stack on `doctor`

Verified 2026-08-20 by running the commands quoted below on the host itself.
Everything stated here was observed, not assumed; where something was *not*
verified, it says so.

---

## 1. What is installed, and where

| | |
|---|---|
| Host | `doctor` — 192.168.1.51, Ubuntu 24.04.4 LTS, x86-64 |
| Stack directory | `/home/sergey/iot-stack` |
| Home Assistant | **2026.8.2** — `homeassistant/home-assistant:stable`, container `iot-homeassistant` |
| Mosquitto | **2.0.22** — `eclipse-mosquitto:2.0.22`, container `iot-mosquitto` |
| weather-engine | built from `weather-engine/`, container `iot-weather-engine` — part of the default stack since 2026-08-20 |
| Backups | `/mnt/backup/iot-stack/` |

Both versions were read back from the running containers, not from the compose
file:

```
docker inspect --format '{{.Config.Image}} => {{index .Config.Labels "org.opencontainers.image.version"}}' \
  iot-homeassistant iot-mosquitto
homeassistant/home-assistant:stable => 2026.8.2
eclipse-mosquitto:2.0.22            => 2.0.22
```

The Home Assistant image comes from Docker Hub rather than ghcr.io. Both
registries serve the identical amd64 manifest, but ghcr's CDN delivers roughly
70 KB/s to this connection against 650 KB/s from Docker Hub — 2.5 hours against
16 minutes for a 625 MiB image. The measurement is recorded in the comment above
the `image:` line in `docker-compose.yml`.

Both containers are `restart: unless-stopped`, `docker.service` is enabled, and
the firewall rule is reinstated at boot by `iot-stack-firewall.service`
(`systemctl is-enabled docker iot-stack-firewall` → `enabled enabled`).

---

## 2. Ports and exposure

| Port | Service | Exposure |
|---|---|---|
| 1883 | Mosquitto | LAN only — enforced in iptables, see below. Never port-forwarded |
| 8123 | Home Assistant | LAN, via the pre-existing ufw rule (host networking) |
| 9001 | MQTT over websockets | off — commented out in `mosquitto.conf` |

`ss -lntp` shows 1883 bound on `0.0.0.0` by `docker-proxy`. **That binding is not
the access control**, and reading it as one is the trap this deployment was
built to avoid: Docker publishes ports by writing its own rules in the FORWARD
path, where `ufw allow` / `ufw deny` never applies. What actually constrains 1883
is a chain installed as the *first* rule of `DOCKER-USER`:

```
Chain DOCKER-USER
1  IOT-MQTT-LAN  all  --  0.0.0.0/0  0.0.0.0/0

Chain IOT-MQTT-LAN
RETURN  tcp  --  192.168.1.0/24  0.0.0.0/0  tcp dpt:1883
RETURN  tcp  --  172.28.0.0/24   0.0.0.0/0  tcp dpt:1883
RETURN  tcp  --  127.0.0.0/8     0.0.0.0/0  tcp dpt:1883
RETURN  tcp  --  172.16.6.0/24   0.0.0.0/0  tcp dpt:1883
DROP    tcp  --  0.0.0.0/0       0.0.0.0/0  tcp dpt:1883
```

The fourth RETURN is the router's WireGuard tunnel subnet, read from
`firewall/allowed-subnets.conf` and reinstated at boot by
`iot-stack-firewall.service`. The DROP stays last, so the chain is still
default-deny for everything else.

Verified in both directions earlier in the deployment: a container on the
default `172.17.x` bridge times out against 1883, while `172.28.x` and a LAN
host at 192.168.1.10 connect. Nothing in this stack is exposed to the internet,
and no port forwarding was created.

---

## 3. Accounts and permissions

Anonymous access is off. Eight accounts exist, each confined to its own
namespace:

```
homeassistant  weather_collector  rf_collector  watch
weather_engine meshcore           rtl433        monitor
```

Passwords live in `.env` on doctor (chmod 600, gitignored, never committed);
only the key names appear in any document. `mosquitto/config/passwd` holds the
hashes and is rebuildable from `.env` via `scripts/gen-secrets.sh`.

A ninth account, `normalizer`, has ACL rules and no password entry — inert until
the service exists (`MQTT.md` §13). Own Wi-Fi sensors get one account each, named
as the sensor, scoped by two **global** pattern rules rather than by a block of
their own (`MQTT.md` §14). None exist yet: no sensor has been built.

**One real bug was found and fixed here.** The `homeassistant` account could not
write `sensors/+/cmd/#`, which is the topic `script.rf_enable_sensor` uses to
enable an RF device. Under MQTT 3.1.1 the broker acknowledges a denied publish
and silently drops it, so the script reported success and nothing happened. The
rule was added to the `user homeassistant` block; the symptom and the fix are
now in `TROUBLESHOOTING.md`, and the permission is listed in `MQTT.md`
§11.

---

## 4. Entities

Read from the live entity registry and the state machine:

| | |
|---|---|
| Rows in the registry | 266 |
| **Active** (not disabled) | **94** |
| Disabled | 172 — see below |
| On the `mqtt` platform | 31 |
| Still carrying a generated long id | **0** |

The state machine reports 97 entities rather than 94: template sensors declared
without a `unique_id` exist at runtime but never enter the registry. Both
numbers are correct, and they count different things.

Entity ids are normalized to `<domain>.<unique_id>` — `sensor.outdoor_temperature`
rather than `sensor.outdoor_weather_station_outdoor_temperature`. This needs
`scripts/normalize-entity-ids.sh` because a YAML-configured MQTT entity cannot
set `object_id` (it is discovery-only) and the entity registry is writable only
over the WebSocket API. The reasoning is in `ARCHITECTURE.md`.

MQTT Discovery was verified live: a simulated collector announced device `42A7`,
it was enabled through the allow-list flow, `sensor.garden_433_*` entities
appeared by themselves, and the test artifacts were removed afterwards — which
is why they are absent from the count above.

### The 172 disabled entities, and why they exist

168 of them are the MeshCore integration's per-contact diagnostic binary
sensors, one for every entry in the node's address book. `DECISIONS.md` D-002
was taken specifically to prevent this and **did not work** — the mode it sets
suppresses entities for *discovered* contacts, while every contact on this node
is an *added* one, and added contacts get an entity in every mode. All 168 read
`unavailable` permanently. They are disabled rather than deleted, because
deleting only makes the integration recreate them; they are also excluded from
the recorder, which is the part that survives a node swap. The full mechanism
and the containment are in D-002's outcome note.

The remaining four are Home Assistant's own defaults, disabled before this
deployment.

### 11 entities read `unknown` or `unavailable`

| How many | Which | Why |
|---|---|---|
| 3 | `sensor.weather_state_condition`, `sensor.weather_state_rain_risk`, `binary_sensor.weather_state_thunderstorm` | the engine deliberately does not publish these — they need precipitation type, months of history, and strike rate respectively (`DECISIONS.md` D-012) |
| 1 | `sensor.rf_last_discovered` | no RF collector is running |
| 1 | `sensor.meshcore_044e2d_node_count_beta_serega` | stays `unknown`; why was not investigated, and nothing depends on it |
| 6 | `person.*`, `conversation.*`, `tts.*`, `event.backup_*`, `sensor.backup_*_automatic_backup` | Home Assistant's own defaults, unrelated to this stack |

The six placeholder `sensor.meshcore_node_*` entities that used to sit here are
gone. Four of them expected temperature, humidity, pressure and RSSI, which this
node does not measure — see `TODO.md` X5. The live MeshCore data is 27 entities
from the integration, plus four of our own that describe the MQTT mirror.

---

## 5. Tests performed

| What | How | Result |
|---|---|---|
| Stack health | `./scripts/healthcheck.sh` | all checks passed; 0 restarts on all three containers |
| MQTT round-trip | publish → broker → subscribe, inside healthcheck | OK |
| Broker liveness | `$SYS/broker/uptime` | 4004 s at the time of the report |
| Home Assistant | HTTP `GET /manifest.json` | 200 |
| ACL, positive | `mosquitto_pub -V 5` as each account into its own namespace | accepted |
| ACL, negative | same, into a foreign namespace | `RC:135 Not authorized` |
| Firewall, negative | container on `172.17.x` → 1883 | timeout |
| Firewall, positive | `172.28.x` and LAN host 192.168.1.10 → 1883 | connect |
| Retained state | recreate both containers, re-subscribe | all `weather/outdoor/*` values survived |
| `own/` ACL, negative | `weather_collector` → `own/greenhouse/temperature`, MQTT 5 | `RC:135 Not authorized` |
| `own/` ACL, pattern | `weather_collector` → `own/weather_collector/temperature` | `RC:16` accepted — the documented cost of Mosquitto pattern rules being global |
| `own/` read, positive | `homeassistant` subscribes `own/#` | receives the value |
| `own/` read, negative | `monitor` subscribes `own/#` | SUBACK granted, nothing delivered |
| Engine grading, complete station | four inputs declared, all four present | `ok` |
| Engine grading, dead sensor | retained `weather/outdoor/wind_speed` cleared, still declaring four | `partial`, `expected_missing: ["wind_speed"]`, `feels_like_method` fell back to `air_temperature` |
| Engine grading, small station | declaration cut to three, wind still absent | `ok`, while `inputs_missing` still lists `wind_speed` |
| Engine config guard | typo in `expected_inputs:` | refuses to start, exit 1 |
| Engine config warning | expected input with no topic under `inputs:` | warning at startup naming it |
| Engine self-test | `python /app/main.py --selftest` | derive + quality selftests OK, no broker needed |
| MQTT Discovery | simulated collector, allow-list enable | entities created automatically |
| weather-engine, formulas | `docker compose run --rm --no-deps weather-engine python /app/main.py --selftest` | `derive selftest: OK` — wind chill at its 4.8 km/h boundary, the textbook 32 °C/70 % heat index, the textbook 20 °C/50 % dew point, every branch of both risk rules |
| weather-engine, stale path | engine started against a 4 h 55 m old sample | `data_quality: degraded`, `computed_at: 2026-08-20T13:33:42+00:00` — a four-hour-old stamp on a just-computed value, and no word written into any topic |
| weather-engine, fresh path | one `./scripts/test-weather-publisher.sh` sample | `data_quality: ok`, stamp 31 s old, `feels_like -13.27`, `ice_risk` flipped `likely` → `none` as the rain rate went to 0 |
| weather-engine, no churn | 3 min of static inputs | exactly one publish logged; retained topics are not rewritten when nothing changed |
| weather-engine liveness | `healthcheck.sh` subscribing to `weather_state/engine_status` as `monitor` | `online` |
| weather-engine, death | `docker kill iot-weather-engine` (SIGKILL, no clean shutdown) | last will fired within 100 s: topic `offline`, `binary_sensor.weather_state_engine_offline` `on`, `healthcheck.sh` FAIL — and the derived values froze with their old measurement stamp rather than going `unknown` |
| ACL, `monitor` scope | subscribe as `monitor` to `weather_state/feels_like` and `weather/outdoor/temperature` | SUBACK 0 in both cases, then **timeout with no message** — the grant really is one topic wide |
| Log noise | `ERROR`/`Traceback` in HA, denials in mosquitto, last 60 min | 0 and 0 |
| ACL, `meshcore/#` write | `homeassistant` accepted, `watch` and `weather_collector` refused | RC:16 / RC:135 / RC:135 |
| MeshCore mirror | `mosquitto_sub -t 'meshcore/#'` after a restart | 5 retained topics, live values |
| MeshCore telemetry cadence | 15 min sampling `sensor.meshcore_044e2d_battery_voltage_beta_serega` | 103 updates, gaps 5–50 s — the 600 s stale threshold has wide margin in practice as well as in theory |
| Docs vs deployment | md5 of every config/script/doc, local vs doctor | identical |

The weather-engine numbers above come from the live broker, not from a dry run.
Both freshness paths were exercised on purpose, because the interesting failure
is not "it publishes nothing" but "it publishes something that looks fresher
than the data behind it".

### The data currently on the broker is simulated

Every `weather/outdoor/*` value is output from `scripts/test-weather-publisher.sh`
— `weather/outdoor/meta` says `"source":"test-publisher","sensor_id":"outdoor-sim-01"`,
and its `last_update` is `2026-08-20T13:33:42Z`. No physical weather station is
connected yet.

This is worth stating because the freshness machinery is visibly doing its job
on exactly that data. At the time of the report:

```
sensor.outdoor_temperature   -2.5        <- retained value, still readable
sensor.outdoor_data_age      4518        <- seconds since the measurement
sensor.outdoor_data_state    offline     <- past the 3600 s offline threshold
sensor.outdoor_summary       no data     <- what a watch face renders
```

The raw metric keeps its last known value, and every consumer-facing entity says
the station is offline. A stale reading is never presented as current.

One naming caveat for whoever reads a dashboard later:
`binary_sensor.outdoor_data_fresh` is declared `device_class: problem`, so `on`
means *not* fresh. `on` alongside `data_state: offline` is consistent, not a
contradiction.

---

## 6. What remains manual

1. **Flash the real devices.** The ESP32 weather node, the watches and any
   collector need their credentials from `.env`. Until then the only publisher
   is the simulator.
2. **MeshCore is live and mirrored to MQTT.** A Companion node at
   `192.168.1.93:5000` is connected over TCP through `meshcore-ha`, and an
   automation republishes `battery`, `battery_voltage`, `status`, `last_seen`
   and `meta` into `meshcore/044e2d/…`, retained, once a minute
   (`DECISIONS.md` D-011). Temperature, humidity and pressure are **not**
   published, because this node has no sensor for them.

   Two operational facts about this node: a Companion serves one client at a
   time, so while HA holds the connection the phone app cannot use it over WiFi
   (`DECISIONS.md` D-001); and the node is the owner's own and is going back to
   them, with a Heltec T114 taking its place over USB.
3. **rtl_433.** Profile and config exist; no SDR dongle is present, so the
   profile has never been started against real hardware.
4. **The weather engine publishes physics only.** It derives `feels_like`,
   `frost_risk`, `ice_risk`, `data_quality` and `computed_at` from documented
   formulas and stated threshold rules, and refuses to publish `condition`,
   `rain_risk` and `thunderstorm` — those need precipitation type, months of
   local history, and strike rate, none of which exist. Those three entities
   read `unknown`, which is the answer rather than a gap (`DECISIONS.md`
   D-012). While the only publisher is the simulator, the derived values
   describe simulated weather.
5. **Backups are scheduled** — daily `--full` at 04:30 via
   `/etc/cron.d/iot-stack-backup`, keeping 14 in `/mnt/backup/iot-stack`. This
   was not a matter of adding a cron line: `--full` had **never once
   succeeded**, aborting at the last step because Home Assistant writes five
   `.storage` files as root with mode 600 and tar ran unprivileged. It now
   re-executes through sudo. Two further defects were found the same way — the
   archive was 90% a Python virtualenv, and the status publish used a
   `mosquitto_pub` option that does not exist, so failures reported nothing.
   All three are fixed and tested; `DECISIONS.md` D-010. Outcome now goes to
   `monitor/backup/status` and is visible as `binary_sensor.backup_failing`.
   The `backup-pull.sh` entry already in the crontab belongs to another project
   and is unrelated to this stack.
6. **The recorder database is small** (774 KB) because it only holds today. It
   is the one genuinely irreplaceable artifact here — it is the future training
   data, and the config-only backup mode does not contain it.

---

## 7. Deliberately left alone

`doctor` runs other people's services. Ports 22, 443, 3000, 3001, 5678, 8080,
8432, 8765, 8888, 8889, 9000 and 51234 were in use before this deployment and
were not touched; this stack took only 1883 and 8123, plus its own Docker subnet
`172.28.0.0/24`.

One pre-existing fault was found and **not** fixed: the third-party `n8n`
container is in a restart loop with a restart count around 343 000, caused by an
expired licence certificate. It has nothing to do with this stack, and touching
it was out of scope.
