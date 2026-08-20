# Services — what runs, where, and what depends on what

`doctor` (192.168.1.51) hosts several unrelated projects. This document covers
**this stack** and names the neighbours only so nobody trips over them.

---

## This stack

| Service | Container | Port | Started by | Purpose |
|---|---|---|---|---|
| Mosquitto | `iot-mosquitto` | 1883 (LAN only) | `docker compose` | the message bus; system of record for current state |
| Home Assistant | `iot-homeassistant` | 8123 | `docker compose` | entities, history, dashboards, automations |
| MeshCore integration | inside HA | — | HA config entry | owns the Companion over TCP → node telemetry |
| weather-engine | `iot-weather-engine` | — | `docker compose` | derives `feels_like`, frost/ice risk and `data_quality` into `weather_state/` |
| rtl_433 | `iot-rtl433` | — | profile `rtl433`, **off** | SDR bridge; no dongle attached |

Everything lives in `/home/sergey/iot-stack` and is defined by one
`docker-compose.yml`.

### Dependency direction

```
                    Companion node 192.168.1.93:5000
                              │ TCP, single client
                              ▼
  collectors ──► Mosquitto ──► Home Assistant ──► dashboards / automations
   (MQTT)         1883            8123                 history
                    ▲              │
                    └──────────────┘   HA both reads and publishes
```

What survives what:

- **Mosquitto down** — HA keeps serving its UI and history; no new sensor values
  arrive. Watches and ESP32 devices lose their data source. This is the one
  component whose failure stops ingestion.
- **Home Assistant down** — the broker keeps accepting and retaining values.
  Watches and any direct MQTT consumer keep working. Nothing is lost; HA catches
  up on restart from the retained state. MeshCore telemetry pauses, because the
  integration lives inside HA.
- **MeshCore node unreachable** — the integration reports a connection error and
  retries. Nothing else notices.
- **weather-engine down** — derived values freeze with their last measurement
  stamp and `weather_state/engine_status` flips to `offline` through the last
  will. No measurement is lost; nothing upstream of it notices. It reads
  `weather/#` and writes `weather_state/#` and touches nothing else.
- **rtl_433 down** — by design, nothing notices. It is an optional profile and
  no core path depends on it.

That ordering is deliberate: optional things may fail loudly, the data path may
not.

---

## Systemd units this stack owns

| Unit | State | What it does |
|---|---|---|
| `docker.service` | enabled | brings the containers back after a reboot |
| `iot-stack-firewall.service` | enabled | reinstates the `DOCKER-USER` rule that confines 1883 to the LAN and to any range listed in `firewall/allowed-subnets.conf` |
| `/etc/cron.d/iot-stack-backup` | active | daily `--full` archive at 04:30, keeping 14, reporting to `monitor/backup/status` |

Nothing else was added, and no existing unit was modified.

---

## Neighbours on the same host — do not touch

These belong to other projects. They were running before this stack and are
listed only so their ports are not mistaken for ours.

| Port | What | Web UI |
|---|---|---|
| 5678 | `n8n` | <http://192.168.1.51:5678> — **in a restart loop**, expired licence certificate, pre-existing |
| 3000 / 8889 / 8432 | `mem0` stack | <http://192.168.1.51:3000> |
| 51234 / 8765 | `letheclaw` + caddy | <http://192.168.1.51:51234> |
| 8080 | `tapo-api` | — |
| 443 | `intronet-demo` nginx | — |
| 9000 | Portainer | <http://192.168.1.51:9000> |
| 8888 | Dozzle (container logs) | <http://192.168.1.51:8888> |
| 3001 | Uptime Kuma | <http://192.168.1.51:3001> |
| 25565 / 24454 | allowed in ufw for a Minecraft server that is **not currently running** — nothing listens on either port, and there is no Java process. `mc-bot.service` and four `mc-*` cron jobs belong to that project and are alive | — |

Portainer, Dozzle and Uptime Kuma are other people's deployments but are
genuinely useful for operating this stack too — Dozzle in particular is the
fastest way to read container logs without SSH.

Docker subnets `172.17`–`172.23` belong to those projects. This stack uses
`172.28.0.0/24` and nothing else.

---

## What is deliberately not running

| Not installed | Why | Where the reasoning lives |
|---|---|---|
| RemoteTerm | would fight `meshcore-ha` for the radio's single client slot | `DECISIONS.md` D-001 |
| `meshcoretomqtt` | needs a USB-attached repeater on custom firmware; we have a WiFi companion | D-004 |
| MeshCore live map | waiting on a real packet feed, and one payload-key mismatch is unverified | D-007 |
| InfluxDB / Prometheus / Grafana | 774 KB of history does not need a second storage engine | D-006 |
| Any VPN software on doctor | the tunnel terminates on the router instead, which covers the whole LAN and leaves this host with no VPN daemon to maintain. Tailscale was installed, never authenticated, and fully removed | `TODO.md` X2 |
| A reverse proxy | with WireGuard on the router there is nothing to proxy; HA is reached at its LAN address over the tunnel | `TODO.md` X2 |
