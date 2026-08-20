# Troubleshooting

Start here:

```sh
cd /home/sergey/iot-stack
./scripts/healthcheck.sh
```

It checks the broker, Home Assistant, disk, restart loops and a full MQTT
round-trip, and tells you which one failed.

---

## MQTT

### A client cannot connect

```sh
docker compose logs --tail=50 mosquitto | grep -i "new connection\|not authorised"
```

| Log line | Cause |
|---|---|
| `Connection Refused: not authorised` | wrong username/password, or the account does not exist |
| nothing at all in the log | the packet never arrived — firewall, wrong IP, wrong port |

Check the account exists and test it:

```sh
sudo cut -d: -f1 mosquitto/config/passwd
set -a; . ./.env; set +a
docker run --rm --network host eclipse-mosquitto:2.0.22 \
  mosquitto_sub -h 127.0.0.1 -u "$HA_MQTT_USER" -P "$HA_MQTT_PASSWORD" -t '$SYS/broker/uptime' -C 1 -W 5
```

Changed a password in `.env` by hand? The broker reads hashes from
`mosquitto/config/passwd`, not from `.env`. Rebuild it:
`./scripts/add-mqtt-user.sh <user> <newpassword>` then
`docker compose exec mosquitto kill -HUP 1`.

### Publishing "succeeds" but nothing arrives

Almost always the ACL. Under MQTT 3.1.1 the broker acknowledges a denied
publish and silently drops it, so `mosquitto_pub` reports success. Ask MQTT 5
instead, where denial is explicit:

```sh
docker run --rm --network host eclipse-mosquitto:2.0.22 \
  mosquitto_pub -V 5 -d -h 127.0.0.1 -u USER -P 'PASS' -t 'some/topic' -m 1 -q 1
```

- `RC:135` — Not authorized. Add rules in `mosquitto/config/acl.conf`, then
  `docker compose exec mosquitto kill -HUP 1`.
- `RC:16` — accepted, but nobody is subscribed. Normal.
- `RC:0` — accepted and delivered.

### A device on the LAN cannot reach the broker

```sh
sudo ./firewall/docker-user-lan-only.sh status
```

Only `192.168.1.0/24`, `172.28.0.0/24` and localhost may reach 1883. A device on
a guest Wi-Fi or a different subnet is dropped by design. If your LAN is not
`192.168.1.0/24`, set `LAN_SUBNET` in `.env` and re-run
`sudo ./firewall/docker-user-lan-only.sh apply`.

Note that `ufw allow 1883` will **not** help — Docker publishes ports below
ufw, which is exactly why the rule lives in `DOCKER-USER`.

### Retained values are missing after a restart

```sh
docker compose exec mosquitto ls -l /mosquitto/data/
```

`mosquitto.db` should exist and be recent. If the directory is empty, the
container could not write to it:

```sh
sudo chown -R 1883:1883 mosquitto/data mosquitto/log
docker compose restart mosquitto
```

### An ACL or config change had no effect after `kill -HUP 1`

The broker reloaded a file nobody is editing any more. `acl.conf`,
`mosquitto.conf` and `passwd` are bind-mounted **per file**, and Docker resolves
each to an inode when the container starts. Any write that replaces the inode
instead of truncating it — `cp` sometimes does, editors that write-and-rename
always do — silently detaches the container's view from the host path.

```sh
sudo md5sum mosquitto/config/acl.conf
docker compose exec -T mosquitto md5sum /mosquitto/config/acl.conf
```

Different sums confirm it. Re-attach:

```sh
docker compose up -d --force-recreate mosquitto
```

Retained messages survive that — they are in the `mosquitto_data` volume, not
in the container. Avoid it next time by writing in place:
`sudo sh -c 'cat new > mosquitto/config/acl.conf'`. Full procedure in
`OPERATIONS.md`.

### `Warning: File ... group is not mosquitto`

Cosmetic on 2.0.22. The config files are owned `1883:sergey` deliberately, so
they can be read and edited without root. The important warning — *world
readable* — is already resolved by mode `0640`.

---

## Home Assistant

### It will not start

```sh
docker compose logs --tail=80 homeassistant
```

Validate the configuration without starting it:

```sh
docker compose run --rm --no-deps homeassistant python -m homeassistant --script check_config --config /config
```

A YAML error in `packages/` stops the whole thing. Bisect by moving one package
file out of `homeassistant/config/packages/` and restarting.

### Entities are `unavailable`

Weather entities carry `availability_topic: weather/outdoor/status`, so they are
unavailable until something publishes `online` there. That is intentional — an
entity with no station behind it should not display a number.

```sh
USER=homeassistant PASS="$HA_MQTT_PASSWORD" ./scripts/mqtt-watch.sh 'weather/outdoor/status' 5
./scripts/test-weather-publisher.sh     # publishes online + a full sample
```

### Entities are `unknown` rather than `unavailable`

Nothing has ever been published on the topic. Expected for `weather_state/…`
until the weather engine exists, and for `meshcore/…` until a node is attached.

### The MQTT integration is missing

It is a config entry, not YAML, so no amount of editing `configuration.yaml`
brings it back. On a fresh deployment this is expected — run
`./scripts/bootstrap-homeassistant.sh`, which creates the owner account if it is
missing and adds the config entry over the API. It is idempotent and does
nothing when MQTT is already configured.

By hand: **Settings → Devices & Services → + Add Integration → MQTT**, broker
`127.0.0.1`, port `1883`, username `homeassistant`, password from `.env`.

### Enabling an RF sensor does nothing

`script.rf_enable_sensor` runs, reports success, and the device never starts
reporting. Home Assistant needs write access to the collector's command
namespace, and without it MQTT 3.1.1 hides the denial:

```sh
grep -n 'sensors/+/cmd' mosquitto/config/acl.conf
```

Nothing there → add `topic write sensors/+/cmd/#` to the `user homeassistant`
block, then `docker compose exec mosquitto kill -HUP 1`. This was missing from
the first deployment; see `MQTT.md` §11.

### Entity ids are long, like `sensor.outdoor_weather_station_outdoor_temperature`

That is what Home Assistant builds from the device name plus the entity name,
and a YAML-configured MQTT entity cannot override it (`object_id` is
discovery-only). Run `./scripts/normalize-entity-ids.sh` — it rewrites the
registry so each entity id becomes `<domain>.<unique_id>`. Dashboards and
automations reference the short form, so an entity added to `packages/` without
re-running it will show up as missing.

### Discovered RF sensors do not appear

```sh
USER=homeassistant PASS="$HA_MQTT_PASSWORD" ./scripts/mqtt-watch.sh 'homeassistant/#' 10
```

- Nothing there → the collector never published a discovery config. It should
  only do that for allow-listed devices; check with
  `./scripts/sim-rf-collector.sh check-allowlist`.
- Configs there but no entities → the JSON is malformed, or `unique_id` is
  missing. Home Assistant logs the reason:
  `docker compose logs homeassistant | grep -i discovery`.

### An entity will not go away

Its discovery config is still retained on the broker. Delete it with an empty
retained payload:

```sh
./scripts/sim-rf-collector.sh undiscovery 42A7
```

### History disappeared

`recorder` keeps 400 days but only records `sensor`, `binary_sensor` and
`weather`. Check `configuration.yaml` if something you expected is missing —
and remember the recorder is not the archive. MQTT is.

---

## Containers

### Restart loop

```sh
docker inspect -f '{{.RestartCount}} restarts, up since {{.State.StartedAt}}' iot-homeassistant
docker compose logs --tail=100 homeassistant
```

`healthcheck.sh` flags this automatically: more than three restarts with under
ten minutes of uptime.

### Nothing came back after a reboot

```sh
systemctl is-enabled docker            # must be: enabled
docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' iot-mosquitto iot-homeassistant
systemctl is-enabled iot-stack-firewall
```

All three must be in place: Docker starting at boot, `unless-stopped` on the
containers, and the firewall unit enabled.

A container stopped by hand with `docker compose stop` stays stopped across a
reboot — that is what `unless-stopped` means. Use `docker compose up -d` to
bring it back.

### Port conflict

Doctor already runs 21 containers. Before publishing a new port:

```sh
sudo ss -tulnp | grep LISTEN
```

In use: 22, 443, 1883, 3000, 3001, 5678, 8080, 8123, 8432, 8765, 8888, 8889,
9000, 51234.

---

## Data quality

### "Outdoor Data State" says `offline` but values are shown

Correct behaviour, and the point of the whole mechanism: the values are the last
known ones, and the state tells you not to trust them as current. Check the age:

```
sensor.outdoor_data_age        seconds since the *measurement*
sensor.outdoor_last_update     the measurement timestamp itself
```

If `last_update` is missing or unparseable, age cannot be computed and the state
is `offline` by design. Publish ISO-8601 UTC: `2026-08-20T13:05:00Z`.

### The state flaps between `fresh` and `stale`

The station transmits less often than `input_number.outdoor_stale_after`. Raise
the threshold from the dashboard — no restart needed.

---

## Things that are not broken

- **`chown: /mosquitto/config/...: Read-only file system`** at startup — the
  entrypoint trying to chown a read-only mount. Harmless.
- **`weather_state/…` entities are `unknown`** — the engine is a skeleton and
  publishes nothing on purpose.
- **rtl_433 is not running** — it is behind a Compose profile and there is no
  SDR attached.
- **`sensor.outdoor_pressure_trend` is `unknown` for the first 3 hours** — it
  averages over a 3-hour window and needs history first.

---

## Getting help from the logs

```sh
docker compose logs -f                             # everything, live
docker compose logs --tail=100 mosquitto
docker compose logs --tail=100 homeassistant
docker compose logs --since 10m homeassistant
tail -f mosquitto/log/mosquitto.log                # broker history across restarts
```

Doctor also runs **dozzle** on <http://192.168.1.51:8888> for browsing container
logs, and **uptime-kuma** on <http://192.168.1.51:3001> if you want alerting on
`healthcheck.sh`.
