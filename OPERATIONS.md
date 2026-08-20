# Operations — start, stop, update, back up, recover

Everything here runs from `/home/sergey/iot-stack` on doctor. Commands assume
you are in that directory.

---

## Daily

```sh
docker compose ps                       # what is up
./scripts/healthcheck.sh                # broker, HA, disk, restarts, round-trip
docker compose logs -f --tail 50        # follow both containers
```

`healthcheck.sh` is the one to trust: it does a real publish → broker →
subscribe round-trip rather than just asking Docker whether a process exists.

For a browser instead of a shell, Dozzle at <http://192.168.1.51:8888> shows the
same logs live.

---

## Start and stop

```sh
docker compose up -d                    # bring the stack up
docker compose restart homeassistant    # one service
docker compose restart                  # both
docker compose down                     # stop and remove containers (volumes stay)

docker compose exec mosquitto kill -HUP 1   # reload ACL/passwords, no downtime
```

`docker compose up -d` brings up all three services: Mosquitto, Home Assistant
and the weather engine. One optional profile is off unless asked for:

```sh
docker compose --profile rtl433 up -d      # needs an SDR dongle
```

After a reboot the stack comes back on its own. A container you stopped by hand
stays stopped — that is what `restart: unless-stopped` means.

### Handing the MeshCore radio back to your phone

The Companion serves one client at a time. While HA is connected, the phone app
cannot use it over WiFi:

```sh
docker compose stop homeassistant       # frees the radio immediately
docker compose start homeassistant      # takes it back
```

Or disable just the integration: **Settings → Devices & Services → MeshCore →
Disable**. Details in `DECISIONS.md` D-001.

---

## Changing configuration

| What changed | What to do |
|---|---|
| `mosquitto/config/acl.conf` or `passwd` | `docker compose exec mosquitto kill -HUP 1` |
| `mosquitto/config/mosquitto.conf` | `docker compose restart mosquitto` |
| `homeassistant/config/**` YAML | `docker compose restart homeassistant` |
| A new YAML entity in `packages/` | restart HA, then `./scripts/normalize-entity-ids.sh` |
| `weather-engine/config.yaml` | `docker compose restart weather-engine` |
| `weather-engine/app/*.py` | `docker compose build weather-engine && docker compose up -d weather-engine` |
| `esphome/*.yaml` | nothing here — it is flashed to a sensor, not run on doctor |
| `docker-compose.yml` | `docker compose up -d` (recreates only what changed) |
| `firewall/allowed-subnets.conf` | `sudo ./firewall/docker-user-lan-only.sh apply` |

The ACL file is owned by the broker's uid. Editing it from your workstation
means copying through a temp path — and it must be written **in place**:

```sh
scp acl.conf sergey@192.168.1.51:/tmp/acl.conf.new
ssh sergey@192.168.1.51 'cd /home/sergey/iot-stack &&
  sudo sh -c "cat /tmp/acl.conf.new > mosquitto/config/acl.conf" &&
  sudo chown 1883:1000 mosquitto/config/acl.conf &&
  sudo chmod 0640 mosquitto/config/acl.conf &&
  docker compose exec -T mosquitto kill -HUP 1'
```

**`cat > file`, not `cp`.** `acl.conf`, `mosquitto.conf` and `passwd` are
*file*-level bind mounts, and Docker binds them by inode when the container
starts. A write that replaces the inode leaves the container reading the old
file forever: the host shows your edit, `kill -HUP 1` cheerfully reloads, and
nothing changes. Shell redirection truncates in place and keeps the inode.

This is not theoretical — it is how the `meshcore/#` write grant appeared to be
ignored until the two inodes were compared. Check the container's own view
before believing any config change:

```sh
sudo md5sum mosquitto/config/acl.conf
docker compose exec -T mosquitto md5sum /mosquitto/config/acl.conf
```

Different sums mean the mount is detached. Re-attach with
`docker compose up -d --force-recreate mosquitto`; retained messages survive it,
because they live in the `mosquitto_data` volume rather than in the container.

**Always verify an ACL change functionally.** A denied publish is silent under
MQTT 3.1.1, so ask MQTT 5:

```sh
docker run --rm --network host eclipse-mosquitto:2.0.22 \
  mosquitto_pub -V 5 -d -h 127.0.0.1 -u USER -P 'PASS' -t 'some/topic' -m x -q 1
# RC:16  accepted   RC:135  not authorized   RC:0  accepted and delivered
```

That check is not ceremony — it is how the `sensors/observed` design flaw was
caught: the rule looked right and the broker accepted a publish it should have
refused.

---

## Updating

```sh
docker compose pull                     # newer HA / Mosquitto images
docker compose up -d
./scripts/healthcheck.sh                # confirm before walking away
```

Home Assistant tracks `:stable`, so `pull` moves it to the current release. Take
a backup first — HA migrates its `.storage` schema on upgrade and does not
migrate back.

The MeshCore integration is a custom component, updated separately:

```sh
./scripts/install-meshcore-integration.sh   # re-run to fetch the latest release
docker compose restart homeassistant
```

---

## Backups

A `--full` archive runs **daily at 04:30** from `/etc/cron.d/iot-stack-backup`,
keeping the last 14 in `/mnt/backup/iot-stack` (an ext4 mount on `/dev/sda1`).
By hand:

```sh
./scripts/backup.sh                     # config only — safe to copy anywhere
./scripts/backup.sh --with-db           # + the recorder database
./scripts/backup.sh --full              # + .env, passwd, .storage — a secrets file
```

`--full` **re-executes itself through sudo** and will say so. It has to: Home
Assistant writes five `.storage` files as root with mode 600, and an
unprivileged tar aborts on the first of them. The finished archive is
`chmod 600` and owned by whoever invoked it. That is why the cron entry runs as
`sergey` rather than as root — a root-owned archive would need sudo just to be
copied off the machine.

### Checking that backups are actually happening

Every run publishes retained JSON to `monitor/backup/status`, so this is
visible in Home Assistant rather than only in a log:

| Entity | Meaning |
|---|---|
| `sensor.backup_last_run` | timestamp of the last completed run |
| `sensor.backup_last_result` | `ok` or `failed` |
| `sensor.backup_age` | seconds since that run |
| `binary_sensor.backup_failing` | `on` if the last run failed, or nothing has run for two days |

From a shell instead:

```sh
./scripts/mqtt-watch.sh 'monitor/backup/#' 5
tail -20 ~/iot-stack-backup.log
```

`binary_sensor.backup_failing` is `device_class: problem`, so `on` means
trouble. It is also `on` before the first backup has ever been seen — never
having a backup is not a healthy state.

### What is in each archive

Config-only leaves out `.env`, `mosquitto/config/passwd`,
`homeassistant/config/.storage` and the recorder database. `--full` contains all
of them, which is exactly why it is treated as a credential file: Home Assistant
stores the broker password in `.storage/core.config_entries` as plaintext JSON.

`tools/meshcore-venv` is excluded from every mode — it is 1170 files of
reinstallable dependencies and was once 90% of the archive.

**The recorder database is the irreplaceable part.** Configs are in git; months
of measurements are not. It is in `--with-db` and `--full`, not in the default.

Full restore procedures, including what to re-run afterwards, are in
`BACKUP_RESTORE.md`.

---

## Recovering

| Symptom | First move |
|---|---|
| Nothing works after a reboot | `docker compose ps`; if empty, `docker compose up -d` |
| HA is up, no sensor values | `./scripts/mqtt-watch.sh 'weather/#' 10` — is anything publishing at all? |
| A publisher "succeeds" but nothing arrives | ACL. Re-run the publish with `-V 5` and read the reason code |
| Devices on the LAN cannot reach 1883 | `sudo iptables -L IOT-MQTT-LAN -n`; the unit is `iot-stack-firewall.service` |
| MeshCore entities went `unavailable` | something else grabbed the radio's single slot, or the node changed IP |
| Entities named `sensor.outdoor_weather_station_*` | `./scripts/normalize-entity-ids.sh` |

Symptom → cause in detail: `TROUBLESHOOTING.md`.

---

## Scripts

| Script | What it does |
|---|---|
| `healthcheck.sh` | broker, HA, disk, restart counts, MQTT round-trip |
| `mqtt-watch.sh` | subscribe to a pattern for N seconds |
| `test-weather-publisher.sh` | publish a full retained weather sample, loop, or mark offline |
| `sim-rf-collector.sh` | simulate an RF collector: announce, enable, publish, check allow-list |
| `gen-secrets.sh` | generate `.env` and the broker password file |
| `add-mqtt-user.sh` | add one MQTT account |
| `bootstrap-homeassistant.sh` | first run: owner account + MQTT config entry |
| `normalize-entity-ids.sh` | rewrite entity ids to `<domain>.<unique_id>` |
| `install-meshcore-integration.sh` | install/update the MeshCore custom component |
| `install-custom-component.sh` | install/update any custom component from a GitHub repo, pinned to its latest release |
| `backup.sh` | config-only or full archive; `--full` re-execs via sudo and reports to MQTT |
| `prune-meshcore-entities.sh` | disable the per-contact sensors, delete registry orphans; dry run unless `--apply` |

The weather engine's formulas have their own test, which needs no broker and
exits non-zero on failure — run it after touching `derive.py`:

```sh
docker compose run --rm --no-deps weather-engine python /app/main.py --selftest
```

`tools/` holds one-off diagnostics rather than operational scripts:
`mc_probe.py` (talk to the Companion directly), `two_clients.py` (demonstrate
the single-client eviction), `mc_check.py` (list MeshCore entities and states),
and `meshcore-venv/` for their dependencies.
