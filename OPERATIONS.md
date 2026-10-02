# Operations — start, stop, update, back up, recover

Everything here runs from `/home/sergey/iot-stack` on doctor. Commands assume
you are in that directory.

---

## Daily

```sh
docker compose ps                       # what is up
./scripts/healthcheck.sh                # broker, HA, disk, restarts, round-trip, engine, zigbee
docker compose logs -f --tail 50        # follow every container
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
docker compose restart                  # all of them
docker compose down                     # stop and remove containers (volumes stay)

docker compose exec mosquitto kill -HUP 1   # reload ACL/passwords, no downtime
```

`docker compose up -d` brings up all four services: Mosquitto, Home Assistant,
the weather engine and the Zigbee bridge. One optional profile is off unless
asked for:

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

### Pairing a Zigbee device

Pairing happens in Zigbee2MQTT's own web UI at <http://192.168.1.51:8099>, not
in Home Assistant — the bridge owns the radio (`DECISIONS.md` D-014). Devices
arrive in Home Assistant by themselves a few seconds later, over MQTT Discovery.

1. **Permit join → on**, in the UI or via
   `switch.zigbee2mqtt_bridge_permit_join`. Turn it back off when you are done:
   an open network accepts anything in range.
2. Put the device into pairing mode — usually a long press, sometimes a power
   cycle. Physical, always.
3. **Rename it immediately.** The friendly name *is* the MQTT topic and the
   entity id, so renaming later moves the topic and breaks every automation and
   dashboard card that used it. `MQTT.md` §6b.

Unplugging the coordinator stops the container from starting at all — the
`devices:` mapping names the stick by `/dev/serial/by-id/`. That is deliberate:
a bridge with no radio should fail loudly, not run and find nothing.

---

## Changing configuration

| What changed | What to do |
|---|---|
| `mosquitto/config/acl.conf` or `passwd` | `docker compose exec mosquitto kill -HUP 1` |
| `mosquitto/config/mosquitto.conf` | `docker compose restart mosquitto` — see the note below, it bounces Zigbee2MQTT too |
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

**Restarting the broker takes the Zigbee bridge down with it.** Zigbee2MQTT
exits rather than waits when MQTT is unreachable, and `restart: unless-stopped`
then brings it back — so a broker restart costs the bridge a minute of
restart-looping, and any Zigbee command sent in that window is lost. `kill -HUP
1` does not do this, which is one more reason to prefer it: it reloads the ACL
and the passwords without dropping a single connection. This is not
hypothetical — it is where the bridge's 49 restarts came from, all of them
during the afternoon it was first deployed:

```
z2m: MQTT failed to connect, exiting... (getaddrinfo ENOTFOUND mosquitto)
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

## Frigate (runs on bigpc, not doctor)

NVR and object detection for the iFEEL camera (192.168.1.141) live on bigpc
(192.168.1.10) for its GPU, inside WSL2 Ubuntu, in `/mnt/nvme/frigate`:
`docker-compose.yml`, `.env` (camera, MQTT and Frigate `admin` passwords,
mode 600), `config/config.yml`, `media/`. UI: <https://192.168.1.10:8971>
(self-signed certificate), user `admin`.

```sh
ssh bigpc                                           # Windows cmd, then:
wsl -d Ubuntu
cd /mnt/nvme/frigate && docker compose up -d        # start / apply compose changes
docker restart frigate                              # apply config.yml changes
curl -s 127.0.0.1:5000/api/stats | jq .detectors    # unauthenticated API, WSL-local only
```

Detection runs on the RTX 5070 Ti: `onnx` detector, YOLOv9-s 320, ~5 ms per
inference. The model `config/model_cache/yolov9-s-320.onnx` is not downloaded
by Frigate; `/mnt/nvme/frigate/export-yolov9.sh` builds it (docker build, took
~2 h on 2026-09-26 because the ISP throttles PyPI). Lose it and detection stops.

It talks to this stack only over MQTT (`frigate` user, `frigate/#`); HA reads
it through the Frigate custom component, pointed at `https://192.168.1.10:8971`.

**A new WSL port is not on the LAN until the owner's portproxy task runs.** The
scheduled task `WSL LAN portproxy` republishes every `0.0.0.0` WSL port, but only
every 30 minutes, and WSL's address changes on reboot. If HA shows Frigate
`unavailable` or its config flow says `cannot_connect`, run it now on bigpc:
`schtasks /run /tn "WSL LAN portproxy"`.

## bigpc fan RGB (OpenRGB)

`light.podsvetka_kompa` is the MSI MAG Z790 TOMAHAWK WIFI Mystic Light
(USB 1462:7D91), served by OpenRGB 1.0 on bigpc and read by the core HA
`openrgb` integration («Большой комп», 192.168.1.10:6742). OpenRGB lives in
`C:\Program Files\OpenRGB` and runs as the scheduled task `OpenRGB server`
(SYSTEM, at startup +30 s): `--server --server-host 0.0.0.0 --startminimized`.
Without `--server-host` it listens on 127.0.0.1 only. Its config and logs are in
the SYSTEM profile: `C:\Windows\System32\config\systemprofile\AppData\Roaming\OpenRGB\`.

- MSI Center's lighting is off so it does not fight OpenRGB: the services
  `Mystic_Light_Service` and `LightKeeperService` are Disabled, and so is the
  task `\MSI Task Host - LEDKeeper2_Host`. The rest of MSI Center still runs.
- The ARGB headers JRAINBOW1..3 were detected with 0 LEDs and are now resized to
  6 each in OpenRGB's config (SDK `zone.resize`, then restart the task). While
  they were 0, the board ignored every mode change and stayed on `Rainbow wave`,
  until OpenRGB restarted with the sizes. The size must match the real strip:
  at 100 the hardware effects looked single-coloured and glitchy on 6-LED fans.
- What sits where (checked by lighting one header at a time): JRAINBOW1 — the
  front case fans, 6 LEDs each, wired in parallel so every fan repeats LEDs
  0..5; JRAINBOW2/3 — empty; JRGB1 (12 V, one colour) — the CPU cooler,
  Cooler Master ML240L V2 RGB (MLW-D24M-A18PC-R2: pump and both radiator fans
  are 12 V RGB, so no per-LED effects on it from any header).
- JRGB1 does not light in Direct mode, only in the board's hardware modes. HA
  uses Direct for «no effect», so a plain colour from HA leaves the cooler
  dark; any HA effect lights it. HA does not offer `Static` as an effect.
- The case fans used to run from the case's own hub (C039 V3.1, no sync
  input, own button). Their cable «MAIN» now goes straight to JRAINBOW1
  (board label JARGB_V2_1, bottom edge by JPWRLED1, `+5V · D · _ · G`).
  MAIN has three contacts in a row and does not fit the gapped header as is:
  G has to reach the fourth pin separately (contact out of the housing, or
  Dupont wires). Swapping 5V and G burns the LEDs.
- Direct mode is volatile, so after a reboot the board shows its own saved
  effect until HA sets a color.
- The GPU (`light.gigabyte_…`) is detected too; its entity is disabled.
- The firewall rule `OpenRGB SDK from doctor` (TCP 6742 from 192.168.1.51)
  exists, but bigpc's Private/Public firewall is off, so the unauthenticated
  SDK port is reachable from the whole LAN.

## bigpc commands (HASS.Agent)

`button.bigpc_agent_*` in HA (monitor off/on, play/pause, next, previous,
volume up/down, mute, YouTube) come from HASS.Agent 2.2.1 on bigpc, broker
user `bigpc_agent` (ACL: `homeassistant/+/bigpc_agent/#` only). Telemetry stays
with `host_mqtt.py`; HASS.Agent publishes no sensors.

- Installed per user in `%LOCALAPPDATA%\HASS.Agent\Client` (user Serg),
  started at logon by the HKCU Run value `HASS.Agent`. It must run in the
  desktop session — from ssh (session 0) the media keys and monitor do nothing.
- It does not stop a second copy from starting. Two copies share the client id
  and knock each other off the broker every second (mosquitto: `bigpc_agent
  already connected, closing old connection`): kill one.
- Config is `...\Client\config\{appsettings,commands,sensors}.json`; the MQTT
  password sits there in plain text. Log: `...\Client\logs\`.
- `LaunchUrlCommand`'s `Command` is JSON, not a bare URL:
  `{"Url":"https://www.youtube.com/","Incognito":false}`. A bare URL kills
  loading all commands (`[FTL] [SETTINGS_COMMANDS]`) and the agent never
  connects to MQTT.
- The status window shows Local API, HA API and Quick Actions stopped and the
  Satellite Service failed: none of them are used. MQTT and Commands must be
  `running`.
- The agent logs nothing when a command runs; check from HA that the button's
  state (last press time) moved and that mosquitto logged no `denied`.

### YouTube limit

`packages/youtube_limit.yaml` holds the rules. The on/off switch is
`input_boolean.youtube_limit`, also on the «Пульт» dashboard in the «YouTube на компе» view.
It needs two HASS.Agent entities, added in the agent's GUI:
- sensor ActiveWindow, entity name `activewindow`, interval 5 s
  (→ `sensor.bigpc_agent_activewindow`);
- command PowerShell, type button, entity name `yt_close`, command
  `C:\Users\Serg\AppData\Local\HASS.Agent\yt-close.ps1` (copy of
  `tools/yt-close.ps1`). HA passes the message as the command's action
  (`homeassistant/button/bigpc_agent/yt_close/action`).

Adding a `counter` or `history_stats` entity needs an HA restart; reload does not pick them up.

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
| Frigate entities `unavailable` in HA | on bigpc: `schtasks /run /tn "WSL LAN portproxy"` (see Frigate above) |

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
