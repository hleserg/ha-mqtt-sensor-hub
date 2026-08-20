# Backup and restore

## What matters

| | Where | Recoverable without a backup? |
|---|---|---|
| Compose, configs, packages, dashboards, scripts, docs | `/home/sergey/iot-stack/` | yes — also in git |
| MQTT passwords | `.env` (chmod 600) | no — regenerating means reflashing every device |
| Mosquitto password hashes | `mosquitto/config/passwd` | rebuildable from `.env` |
| HA config entries, users, entity registry | `homeassistant/config/.storage/` | rebuildable by script — `bootstrap-homeassistant.sh` + `normalize-entity-ids.sh` |
| **Weather history** | `homeassistant/config/home-assistant_v2.db` | **no** — this is the training data |
| Broker retained state | volume `iot-stack_mosquitto_data` | no, but it refills on the next transmission |
| MeshCore integration | `homeassistant/config/custom_components/` | yes — reinstall script |

The one genuinely irreplaceable thing is the recorder database. Everything else
is either in git or costs ten minutes.

## Taking a backup

```sh
cd /home/sergey/iot-stack

./scripts/backup.sh                # config only, no credentials   (default)
./scripts/backup.sh --with-db      # + weather history
./scripts/backup.sh --full         # + .env, passwd, .storage
```

Archives land in `/mnt/backup/iot-stack/` — doctor's separate backup disk —
as `iot-stack-YYYYMMDD-HHMMSS-<mode>.tar.gz`. Retention is per mode
(14 by default), so a daily config backup can never push out the rarer full
ones.

### The three modes

**Default** leaves out `.env`, `mosquitto/config/passwd` and the whole
`.storage/` directory. That is not caution for its own sake: Home Assistant
stores the MQTT broker password in `.storage/core.config_entries` as plaintext
JSON, so an archive containing `.storage` is a credential file. Mode `0640`.

**`--with-db`** adds the recorder database, after asking Home Assistant to
checkpoint its SQLite WAL — otherwise you capture a torn snapshot of an open
database.

**`--full`** adds everything, is written `chmod 600`, and should be treated as
a secret. Use it before a risky change, not as a daily job.

### Suggested schedule

Nothing is scheduled automatically — cron on doctor is yours to decide. A
reasonable setup:

```cron
# daily config backup at 03:30
30 3 * * *   cd /home/sergey/iot-stack && ./scripts/backup.sh --quiet >> /var/log/iot-backup.log 2>&1
# weekly, with the weather history, Sunday 03:45
45 3 * * 0   cd /home/sergey/iot-stack && ./scripts/backup.sh --with-db >> /var/log/iot-backup.log 2>&1
```

`/mnt/backup` is a local disk. For anything you would mind losing in a fire,
copy the weekly archive onward — `/mnt/win-backups` is already mounted from
your Windows machine.

---

## Restoring

### From a default (config-only) archive

```sh
sudo systemctl stop iot-stack-firewall            # optional, keeps rules tidy
cd /home/sergey
docker compose -f iot-stack/docker-compose.yml down     # stops only this stack
mv iot-stack iot-stack.broken
mkdir iot-stack && tar xzf /mnt/backup/iot-stack/iot-stack-YYYYMMDD-HHMMSS-config.tar.gz -C iot-stack
cd iot-stack
```

Then rebuild what was deliberately excluded:

```sh
./scripts/gen-secrets.sh                          # new .env + passwd
sudo chown -R 1883:1883 mosquitto/log mosquitto/data
sudo chown 1883:1000 mosquitto/config/*.conf mosquitto/config/passwd
sudo chmod 0640      mosquitto/config/*.conf mosquitto/config/passwd
docker compose up -d
```

Home Assistant will start with no users and no integrations, because that all
lived in the `.storage/` directory the config-only archive leaves out. Rebuild
it — the same two scripts that set the stack up in the first place:

```sh
./scripts/bootstrap-homeassistant.sh              # owner account + MQTT entry
./scripts/normalize-entity-ids.sh                 # short entity ids
./scripts/install-meshcore-integration.sh         # only if MeshCore was in use
```

`normalize-entity-ids.sh` is not cosmetic here: the restored dashboards and
automations reference the short entity ids, so skipping it leaves a dashboard
full of missing entities. By hand the equivalent is the onboarding wizard,
**Settings → Devices & Services → + Add Integration → MQTT** (broker
`127.0.0.1`, port `1883`, user `homeassistant`, password from the new `.env`),
and renaming every entity id in the UI.

**All device passwords have changed.** Every ESP32, watch and collector needs
the new credentials from `.env`. This is the cost of the safe default, and the
reason to also keep one `--full` archive somewhere safe.

### From a `--full` archive

Same extraction, then just:

```sh
sudo chown -R 1883:1883 mosquitto/log mosquitto/data
sudo chown 1883:1000 mosquitto/config/*.conf mosquitto/config/passwd
sudo chmod 0640      mosquitto/config/*.conf mosquitto/config/passwd
docker compose up -d
```

Nothing to reconfigure — users, MQTT connection and device passwords all still
work.

### Restoring only the weather history

The usual case: the config is fine, you just want the data back.

```sh
cd /home/sergey/iot-stack
docker compose stop homeassistant
tar xzf /mnt/backup/iot-stack/iot-stack-...-config-db.tar.gz \
    -C /tmp/ha-restore --strip-components=0 ./homeassistant/config/home-assistant_v2.db
cp /tmp/ha-restore/homeassistant/config/home-assistant_v2.db homeassistant/config/
docker compose start homeassistant
```

Stop Home Assistant first. Swapping the database under a running instance
corrupts it.

### Reinstating the firewall rule

```sh
sudo cp firewall/iot-stack-firewall.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now iot-stack-firewall
sudo ./firewall/docker-user-lan-only.sh status
```

---

## Verifying a restore

```sh
./scripts/healthcheck.sh
./scripts/test-weather-publisher.sh
USER=homeassistant PASS="$HA_MQTT_PASSWORD" ./scripts/mqtt-watch.sh 'weather/#' 5
```

Then open `http://192.168.1.51:8123`, check the **Weather** dashboard shows
values and that **Outdoor Data State** reads `fresh`.

An untested backup is a rumour. Restore one into a scratch directory at least
once, before you need it.
