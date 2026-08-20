# iot-stack — home IoT and weather infrastructure on `doctor`

Local-first Home Assistant + MQTT for my own weather sensors, a RF/BLE sensor
collector, MeshCore telemetry and ESP32-S3 watches. No cloud dependency: with
the internet unplugged, the broker, Home Assistant, the watches and MeshCore all
keep working.

This repository is the actual source of a running deployment, not a template:
the addresses, ports and decisions below describe one real machine. Everything
needed to stand it up somewhere else is here — **`DEPLOY.md` is the from-scratch
path**, and `§ Adapting it to your setup` in that file lists every site-specific
value. Secrets are generated on the host and are not in git.

| | |
|---|---|
| **Home Assistant** | <http://192.168.1.51:8123> |
| **MQTT broker** | `192.168.1.51:1883` (LAN only, authenticated) |
| **Host** | `doctor` — 192.168.1.51, Ubuntu 24.04.4 LTS, x86-64 |
| **Stack directory** | `/home/sergey/iot-stack` |
| **Backups** | `/mnt/backup/iot-stack/` |
| **Versions** | Home Assistant 2026.8.2, Mosquitto 2.0.22 — verified running 2026-08-20 |

`doctor.local` does **not** resolve — avahi/mDNS is not running on doctor. Give
devices the IP address.

---

## Ports

| Port | Service | Exposure |
|---|---|---|
| 1883 | Mosquitto (MQTT) | LAN only, enforced in the `DOCKER-USER` iptables chain. **Never** port-forwarded |
| 8123 | Home Assistant | LAN, via the existing ufw rule |
| 9001 | *(reserved)* MQTT over websockets | off — commented out in `mosquitto.conf` |

Already in use on doctor by other projects, left alone: 22, 443, 3000, 3001,
5678, 8080, 8432, 8765, 8888, 8889, 9000, 51234.

This stack uses the Docker subnet `172.28.0.0/24`; `172.17`–`172.23` belong to
the existing projects.

---

## Running it

```sh
cd /home/sergey/iot-stack

docker compose up -d                    # mosquitto + home assistant + weather-engine
docker compose ps
docker compose logs -f

docker compose restart                  # restart everything
docker compose restart homeassistant    # or just one service
docker compose exec mosquitto kill -HUP 1   # reload ACL/passwords, no downtime
```

`docker compose up -d` starts all three services: the broker, Home Assistant and
`weather-engine`. The engine joined the default stack on 2026-08-20, when it
stopped being a stub — it now derives `feels_like`, `frost_risk`, `ice_risk` and
`data_quality`. Nothing in the measurement path depends on it: it reads
`weather/#` and writes `weather_state/#`, so if it dies you lose derived values
and no measurements.

One service is genuinely optional and stays off:

```sh
docker compose --profile rtl433 up -d   # RTL-SDR bridge (needs a dongle)
```

Home Assistant is pulled from Docker Hub rather than ghcr.io. Both registries
serve the identical amd64 manifest; ghcr's CDN is roughly ten times slower to
this connection, which is 2.5 hours against 16 minutes for a 625 MiB image. The
reasoning is in the comment above the `image:` line in `docker-compose.yml`.

### First run

`docker compose up -d` leaves you with a broker and a Home Assistant nobody has
onboarded yet. Two steps finish the job, and both are idempotent:

```sh
./scripts/bootstrap-homeassistant.sh    # owner account + MQTT config entry
./scripts/normalize-entity-ids.sh       # short entity ids for the YAML entities
```

The broker connection is a *config entry* in `.storage`, not YAML — Home
Assistant offers no YAML equivalent — so `bootstrap-homeassistant.sh` creates it
over the API instead of relying on someone clicking through the wizard. It
generates the owner credentials once and writes them into `.env` as
`HA_ADMIN_USER` / `HA_ADMIN_PASSWORD`.

`normalize-entity-ids.sh` then rewrites the entity registry so each entity
declared in `packages/` gets `<domain>.<unique_id>` — `sensor.outdoor_temperature`
instead of `sensor.outdoor_weather_station_outdoor_temperature`. Re-run it after
adding entities to `packages/`; entities that arrived over MQTT Discovery keep
the ids their discovery config gave them. Why this needs a script at all is in
`ARCHITECTURE.md`.

**After a reboot everything comes back by itself:** `docker.service` is enabled,
all three containers are `restart: unless-stopped`, and the firewall rule is
reinstated by `iot-stack-firewall.service`.

A container you stop by hand stays stopped across a reboot — that is what
`unless-stopped` means. `docker compose up -d` brings it back.

---

## Where the data lives

| What | Path |
|---|---|
| Home Assistant config, packages, dashboards | `homeassistant/config/` |
| Home Assistant database (weather history) | `homeassistant/config/home-assistant_v2.db` |
| HA config entries, users, entity registry | `homeassistant/config/.storage/` |
| Mosquitto config and ACL | `mosquitto/config/` |
| Mosquitto retained state | Docker volume `iot-stack_mosquitto_data` |
| Mosquitto log | `mosquitto/log/mosquitto.log` |
| Secrets | `.env` (chmod 600, gitignored) |

Everything except the Docker volume is a plain file under `/home/sergey/iot-stack`.

---

## Credentials

All MQTT passwords are in `/home/sergey/iot-stack/.env`, chmod 600, generated
on doctor and never committed. `.env.example` shows the shape.

```sh
grep WATCH /home/sergey/iot-stack/.env      # e.g. the read-only watch account
```

| Account | Purpose |
|---|---|
| `homeassistant` | Home Assistant |
| `weather_collector` | my outdoor weather sensors — writes `weather/#` |
| `rf_collector` | RF/BLE collector — writes `sensors/#` + discovery |
| `watch` | **read-only** for the ESP32-S3 watches |
| `weather_engine` | reads measurements, writes `weather_state/#` |
| `meshcore` | MeshCore telemetry |
| `rtl433` | optional SDR bridge |
| `monitor` | healthchecks only |

Full permission table in `MQTT.md` §11.

### Creating a new one

```sh
./scripts/add-mqtt-user.sh my_collector          # prints a generated password
# then add rules for it in mosquitto/config/acl.conf
docker compose exec mosquitto kill -HUP 1
```

An account with no ACL rules can connect and do nothing. That is deliberate —
see `SENSORS.md` §4.

### Rotating everything

```sh
./scripts/gen-secrets.sh --force
```

This invalidates every flashed device and Home Assistant's stored MQTT
connection. Rarely what you want.

---

## Adding a publisher

`SENSORS.md` covers all five cases; case 5 — a self-built Wi-Fi sensor under
`own/<sensor_id>/…` — is the common one now. The short version for a device
filling the weather-station role: connect as `weather_collector`, set a Last Will on
`weather/outdoor/status`, publish bare values **retained** on
`weather/outdoor/<metric>`, and publish `weather/outdoor/last_update` with the
measurement time. The Home Assistant entities already exist.

---

## Looking at MQTT traffic

```sh
./scripts/mqtt-watch.sh 'weather/#'        # my weather station
./scripts/mqtt-watch.sh 'sensors/#'        # RF/BLE collectors
./scripts/mqtt-watch.sh '#' 30             # everything, for 30 seconds
USER=monitor PASS="$MONITOR_MQTT_PASSWORD" ./scripts/mqtt-watch.sh '$SYS/#' 5
```

`mqtt-watch.sh` uses the `homeassistant` account by default, which can read
every telemetry namespace. Override with `USER=` / `PASS=` to check what a
different account is actually allowed to see — useful for verifying an ACL.

---

## Testing

```sh
./scripts/test-weather-publisher.sh              # one full retained sample
./scripts/test-weather-publisher.sh --loop 30    # a live simulated station
./scripts/test-weather-publisher.sh --offline    # mark the station offline

./scripts/sim-rf-collector.sh online
./scripts/sim-rf-collector.sh announce 42A7
./scripts/sim-rf-collector.sh discovery 42A7 "Garden 433"
./scripts/sim-rf-collector.sh publish 42A7

./scripts/healthcheck.sh                         # broker, HA, disk, loops, round-trip
```

---

## The ESP32-S3 watches

Read-only account `watch`. Subscribe to:

```
weather/#
weather_state/#
```

Every state topic is **retained**, so a watch gets the current values in the
first round-trip after connecting — no request/response, no waiting for the next
transmission.

The watch may publish only under `watch/<its-client-id>/#`, and has no access to
any telemetry namespace. It talks to the broker directly, so Home Assistant
being down or restarting does not interrupt it.

An HTTP fallback through the Home Assistant REST API is possible for the rare
case where MQTT is unavailable, but MQTT is the realtime transport inside the
LAN.

---

## Documentation

| File | |
|---|---|
| `DEPLOY.md` | standing this up from a fresh clone, in order |
| `ARCHITECTURE.md` | how it fits together and why it was built this way |
| `MQTT.md` | the topic contract — read this before writing a publisher |
| `SENSORS.md` | adding a sensor, in four flavours |
| `MESHCORE.md` | MeshCore integration, connection choice, telemetry mirroring |
| `BACKUP_RESTORE.md` | what is backed up, what is not, and how to get it back |
| `TROUBLESHOOTING.md` | symptom → cause |
| `SERVICES.md` | what runs where, what depends on what, what survives what |
| `OPERATIONS.md` | start/stop, update, backup, recovery, every script |
| `DECISIONS.md` | the decision log — choice, evidence, alternatives, reversibility |
| `TODO.md` | backlog: NOW / NEXT / LATER with dependencies |
| `ACCEPTANCE.md` | what was deployed, how it was verified, what is still manual |
| `WEATHER-STATION-CHOICE.md` | why the weather sensors are self-built rather than bought (RU) |
| `weather-engine/README.md` | the derived-weather service |

---

## Backups

```sh
./scripts/backup.sh                # config only, no credentials  (default)
./scripts/backup.sh --with-db      # + weather history
./scripts/backup.sh --full         # + secrets — treat the archive as one
```

Timestamped archives in `/mnt/backup/iot-stack/`. Nothing is scheduled
automatically; suggested cron entries are in `BACKUP_RESTORE.md`.

---

## External access

Nothing here is on the internet. Doctor's router forwards only 443, to the
unrelated `intronet-demo` nginx.

**MQTT 1883 must never be forwarded.** Not with a password, not with an ACL.
It is a plaintext protocol carrying credentials to devices that cannot rotate
them.

For reaching Home Assistant from outside later, in preference order:

1. **WireGuard on doctor.** The whole LAN becomes reachable, nothing is
   published, and it works for MQTT too if you ever need it from a phone.
   Roughly ten minutes of setup plus one router port-forward for UDP.
2. **Tailscale.** No port-forward at all, works behind CGNAT, and gives the
   watches and phone a stable address. Depends on a coordination service, but
   only for establishing the connection — not for the weather path.
3. **Cloudflare Tunnel + Cloudflare Access** in front of Home Assistant only.
   No inbound ports. Adds a cloud dependency for remote access; the local path
   is unaffected. Doctor already has a `cf` binary, though the tunnel currently
   running is an ad-hoc quick tunnel, not a named one.
4. **A reverse proxy on 443 with a subdomain.** Requires modifying the running
   `intronet-demo` nginx, which is a production service. Least attractive.

Whatever is chosen, Home Assistant must keep working with the internet
unplugged, and remote access must never become a dependency of the weather
system.

---

## Layout

```
iot-stack/
├── docker-compose.yml
├── .env                     # secrets, chmod 600, gitignored
├── .env.example
├── mosquitto/
│   ├── config/              # mosquitto.conf, acl.conf, passwd
│   └── log/
├── homeassistant/
│   └── config/
│       ├── configuration.yaml
│       ├── packages/        # weather, derived state, data quality, RF, meshcore, monitoring
│       ├── dashboards/      # weather.yaml — YAML mode, so it is backed up
│       ├── automations.yaml
│       └── scripts.yaml
├── weather-engine/          # derived values: feels_like, frost/ice risk, data quality
├── rtl_433/                 # optional SDR bridge config
├── firewall/                # DOCKER-USER LAN-only rule + systemd unit
└── scripts/                 # secrets, users, first-run bootstrap, entity ids,
                             #   publishers, health, backup, meshcore
```

---

## Licence

MIT — see `LICENSE`.

The MeshCore Home Assistant integration is **not** part of this repository. It
is fetched from [meshcore-dev/meshcore-ha](https://github.com/meshcore-dev/meshcore-ha)
by `scripts/install-meshcore-integration.sh` and carries its own licence.
