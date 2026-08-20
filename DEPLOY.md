# DEPLOY.md — bringing this stack up from a fresh clone

`README.md` describes the stack as it runs on `doctor`. This file is the other
direction: an empty machine, a `git clone`, and what has to happen — in order —
before anything works.

Everything here was derived from the live deployment, but **a fresh clone is not
the same starting point as that machine**: three files the containers need are
deliberately absent from git (secrets, the broker password file, the engine's
instance config). Steps 1 and 2 exist because of that, and skipping them fails
in a way that is not obvious — see the box in step 2.

---

## 0. Prerequisites

| | |
|---|---|
| OS | Linux with Docker Engine ≥ 24 and the Compose v2 plugin. Developed on Ubuntu 24.04, x86-64 |
| Access | a normal user in the `docker` group, plus `sudo` for the firewall step |
| Disk | ~2 GB for images, plus the Home Assistant database, which grows with history |
| Network | a LAN. Nothing here should ever be exposed to the internet — see README "External access" |

`git`, `curl` and Python 3 (for the helper scripts, stock on Ubuntu) are the
only host-side dependencies. `mosquitto_pub`/`mosquitto_sub` are **not** needed
on the host: every script that speaks MQTT runs the client inside a throwaway
`eclipse-mosquitto` container.

```sh
git clone https://github.com/hleserg/iot-stack.git
cd iot-stack
```

Any directory works, but two files hardcode an absolute path — see step 5.

---

## 1. Secrets

```sh
./scripts/gen-secrets.sh
```

Creates `.env` (chmod 600) with a fresh 28-character random password per MQTT
account, and generates `mosquitto/config/passwd` by running `mosquitto_passwd`
inside the broker image. Both are gitignored and neither ever leaves the host.

Idempotent: an existing `.env` is left alone. `--force` regenerates everything,
which invalidates Home Assistant's stored broker connection and every flashed
device — rarely what you want, and never on a running system.

Adjust `TZ` and `LAN_SUBNET` in `.env` afterwards if your network is not
`192.168.1.0/24`.

---

## 2. The engine's instance config

```sh
cp weather-engine/config.example.yaml weather-engine/config.yaml
```

> **Do not skip this, and do not reorder it after step 3.**
>
> `docker-compose.yml` bind-mounts two paths that git does not carry:
> `mosquitto/config/passwd` and `weather-engine/config.yaml`. When a bind-mount
> source does not exist, Docker does not fail — it **creates a directory** at
> that path. The broker then reads a directory as its password file and rejects
> every login, and the engine reads a directory as its config and crash-loops.
> Both failures look like a bug in the stack rather than a missing file, and
> deleting the directories afterwards is the only fix. Step 1 produces the
> first file, this step the second.

`config.yaml` is gitignored on purpose: it is the instance's own wiring —
topics, thresholds, and the `expected_inputs:` list that says what *your*
station promises. `config.example.yaml` stays in git as the documented shape.

---

## 3. First start

```sh
docker compose up -d --build
docker compose ps
```

`--build` is needed the first time: `weather-engine` is built from
`./weather-engine`, not pulled. The other two images come from a registry.

Expect three containers — `iot-mosquitto`, `iot-homeassistant`,
`iot-weather-engine`. Home Assistant takes a minute or two to answer on 8123 the
first time; the broker is up in seconds.

The engine will log `no_data` until something publishes measurements. That is
correct: it derives, it does not invent.

---

## 4. Home Assistant onboarding

```sh
./scripts/bootstrap-homeassistant.sh
./scripts/normalize-entity-ids.sh
```

A fresh Home Assistant has no owner account and no MQTT connection, and the
broker connection is a *config entry* in `.storage` — Home Assistant offers no
YAML equivalent for it. `bootstrap-homeassistant.sh` creates the owner over the
API and wires up MQTT with the `homeassistant` account from `.env`, writing the
generated admin credentials back into `.env` as `HA_ADMIN_USER` /
`HA_ADMIN_PASSWORD`. Both scripts are idempotent.

`normalize-entity-ids.sh` then rewrites the entity registry so entities declared
in `homeassistant/config/packages/` get `<domain>.<unique_id>` rather than the
long auto-generated ids. Re-run it whenever you add entities to `packages/`.

Log in at `http://<host>:8123` and confirm the Weather dashboard renders.

---

## 5. Firewall — keep 1883 on the LAN

Published Docker ports bypass `ufw` entirely: Docker writes its own rules in the
`FORWARD` path. The supported hook is the `DOCKER-USER` chain, which is where
this rule lives.

```sh
sudo ./firewall/docker-user-lan-only.sh apply
sudo ./firewall/docker-user-lan-only.sh status
```

To survive reboots, install the systemd unit:

```sh
sudo cp firewall/iot-stack-firewall.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iot-stack-firewall
```

**Edit the paths first if you did not clone to `/home/sergey/iot-stack`** —
`iot-stack-firewall.service` and `cron/iot-stack-backup` both carry that
absolute path. They are the only two files in the repository that do.

Allowed source networks are read from `firewall/allowed-subnets.conf` at apply
time; add a WireGuard client range there rather than editing the script.

---

## 6. Optional pieces

Both are genuinely optional — the core stack does not depend on either.

```sh
# RTL-SDR bridge. Needs a dongle physically attached.
docker compose --profile rtl433 up -d

# MeshCore Home Assistant integration (third-party, not vendored here).
./scripts/install-meshcore-integration.sh
docker compose restart homeassistant
```

The MeshCore integration is fetched from
<https://github.com/meshcore-dev/meshcore-ha> at install time. It is somebody
else's project under its own licence, which is why this repository ships the
installer and not a copy. HACS is the alternative and adds update
notifications. `MESHCORE.md` covers the connection choice and what the
integration does once it is there.

---

## 7. Backups

```sh
./scripts/backup.sh            # config only, no credentials
./scripts/backup.sh --full     # + history + secrets — treat the archive as a secret
sudo cp cron/iot-stack-backup /etc/cron.d/iot-stack-backup   # daily 04:30
```

The default target is `/mnt/backup/iot-stack`; change it in `scripts/backup.sh`
if you have no such mount. `BACKUP_RESTORE.md` says what is in an archive, what
is not, and how to restore from one.

---

## 8. Acceptance — prove it actually works

Not "is the container up". These check the data path end to end:

```sh
./scripts/healthcheck.sh
```

Eight checks: broker alive, Home Assistant answering, disk, per-container
restart counts, an authenticated MQTT publish→subscribe round-trip, and the
engine reporting `online` on `weather_state/engine_status`.

```sh
./scripts/test-weather-publisher.sh            # one retained sample
./scripts/test-weather-publisher.sh --loop 30  # a simulated live station
./scripts/mqtt-watch.sh 'weather_state/#' 40   # watch the engine react
```

Within one compute interval the engine should publish `feels_like`,
`frost_risk`, `ice_risk` and `data_quality`. Then look at the Weather dashboard
in Home Assistant: it should show the same numbers with a freshness badge.

```sh
./scripts/test-weather-publisher.sh --offline  # station goes away
```

The dashboard must switch to a stale/offline indication rather than keeping the
last value on display. `ACCEPTANCE.md` lists every test that was run against the
real deployment, with the results.

---

## 9. Adapting it to your setup

Nothing here is auto-detected. This is the full list of what is specific to the
deployment this repository documents:

| What | Where | Note |
|---|---|---|
| `192.168.1.51` | docs, `esphome/own-sensor-reference.yaml`, `tools/*.py` | the host's LAN address |
| `LAN_SUBNET` | `.env` | used by the firewall script |
| Absolute path `/home/sergey/iot-stack` | `firewall/iot-stack-firewall.service`, `cron/iot-stack-backup` | edit both if you clone elsewhere |
| `TZ` | `.env` | defaults to `Europe/Moscow` |
| NTP servers | `esphome/own-sensor-reference.yaml` | LAN servers on purpose — sensors must keep time without internet |
| Docker subnet `172.28.0.0/24` | `docker-compose.yml` | changed only if it collides on your host |
| Backup target `/mnt/backup` | `scripts/backup.sh` | |
| `192.168.1.48`, `192.168.1.93` | docs | other nodes on that LAN; nothing in the stack depends on them |

The MQTT topic contract in `MQTT.md` and the ACL in `mosquitto/config/acl.conf`
are not site-specific and are meant to be used as-is.

---

## 10. What this repository does not contain

Three categories, all deliberate:

- **Secrets.** No `.env`, no `mosquitto/config/passwd`, no
  `homeassistant/config/secrets.yaml`. They are generated on the host by
  `gen-secrets.sh` and never committed. `.env.example` shows the shape.
- **Third-party code.** `homeassistant/config/custom_components/` is gitignored.
  The MeshCore integration is fetched by its installer script — it is not this
  project's code and not this project's licence to grant.
- **Runtime state.** The Home Assistant database, `.storage`, logs, Mosquitto's
  retained-message store. `BACKUP_RESTORE.md` is how those move between
  machines; git is not.

---

## 11. Removing it

```sh
docker compose down                  # keeps volumes
docker compose down -v               # also drops retained MQTT state
sudo systemctl disable --now iot-stack-firewall
sudo ./firewall/docker-user-lan-only.sh remove
sudo rm -f /etc/cron.d/iot-stack-backup /etc/systemd/system/iot-stack-firewall.service
```

The firewall script confines itself to a dedicated `IOT-MQTT-LAN` chain plus one
jump, so `remove` leaves the rules Docker and any other project on the host rely
on untouched. Home Assistant's config and database stay on disk under
`homeassistant/config/` until you delete the directory yourself.
