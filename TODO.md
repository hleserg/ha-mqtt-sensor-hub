# Backlog — NOW / NEXT / LATER

Ordered by dependency, not by enthusiasm. An item is only NOW if the thing it
depends on already exists.

Status: `[x]` done · `[~]` in progress · `[ ]` not started · `[!]` blocked on
hardware, secrets or a decision that is yours to make.

---

## NOW — the working foundation

| | Task | Depends on | State |
|---|---|---|---|
| N1 | Mosquitto with per-account ACL, anonymous off, LAN-only firewall | — | `[x]` |
| N2 | Home Assistant connected to the broker, entity ids normalized | N1 | `[x]` |
| N3 | End-to-end proof: simulated sensor → broker → HA entity → history | N2 | `[x]` |
| N4 | Freshness model — `fresh` / `stale` / `offline`, age always visible | N3 | `[x]` |
| N5 | Companion investigated: protocol, ownership, single-client behaviour | — | `[x]` |
| N6 | MeshCore integration live over TCP, `data_only` contact discovery | N5, N2 | `[x]` |
| N7 | Topic contract extended: ownership, identity, raw vs normalized | N1 | `[x]` |
| N8 | ACL for `radio/raw/…` and `observed/…`, verified positive and negative | N7 | `[x]` |
| N9 | Documentation set + decision log on doctor | all | `[x]` |

NOW is complete. What follows is not started, and deliberately so.

---

## NEXT — almost certainly needed

### X1 · Pin the Companion's address `[x]`
*Depends on: N6.* Done — the owner reports a DHCP reservation is already in
place on the router for `192.168.1.93`. That is a router-side fact and cannot be
confirmed from doctor; what doctor can confirm is that the node has stayed at
`.93` across every check and an HA restart. If MeshCore entities ever go
`unavailable` for no other reason, re-check the reservation first.

### X2 · Remote access to home data `[x]`
*Depends on: the router. Nothing further was needed from doctor.*

**WireGuard on the router, verified working 2026-08-20** — the owner reached
Home Assistant from a phone on mobile data. Tailscale was installed on doctor,
never authenticated, and removed at their request; the router is the better
place for this anyway, because it puts the whole LAN behind one tunnel instead
of one host, and doctor keeps zero VPN software. Removal was verified: no
binaries, no systemd units, no `tailscale0`, no repository, no leftover `ts-`
iptables rules, `/etc/resolv.conf` untouched, all 22 containers running.

The router's WireGuard server: udp/41495, tunnel subnet `172.16.6.0/24`, the
router itself `172.16.6.1`, clients from `.2` upward.

What doctor needed, and has:

- **1883** — `172.16.6.0/24` in `firewall/allowed-subnets.conf`, applied, and
  re-read at boot by `iot-stack-firewall.service`. Without it the tunnel would
  reach the host and then be dropped in `DOCKER-USER`.
- **8123** — nothing. Home Assistant runs with host networking, so ufw governs
  it, and ufw already accepts 8123 from any source that can route to the host.

The one non-obvious part was client-side, and cost a round trip: *Разрешённые
подсети* means opposite things at the two ends. On the router, a peer's entry is
the address that peer owns (`172.16.6.2/32`). In the phone's config, the peer
block's `AllowedIPs` is what gets routed **into** the tunnel, so it must list
`192.168.1.0/24` — with only `172.16.6.0/24` there, the tunnel comes up, the
handshake succeeds, and nothing in the house is reachable.

Consequence worth remembering: while the tunnel is up, `192.168.1.0/24` belongs
to home. On someone else's Wi-Fi using the same very common subnet, their local
addresses become unreachable.

### X3 · Real weather station `[~]` — self-built, incrementally
*Depends on: hardware being built, not bought.* Decided 2026-08-20: **no
commercial station.** The owner builds sensors himself, on **ESP32-C5**, adding
them one at a time. The purchase recommendation is withdrawn;
`WEATHER-STATION-CHOICE.md` was rewritten around the new plan and keeps the
part of the analysis that survives — the data-direction argument, which now
decides `mqtt:` over ESPHome's native API for the same reason it decided SDR over
a vendor gateway.

Everything downstream is unchanged and still proven against
`test-weather-publisher.sh`: topics, entities, freshness machinery, the watch
contract. `weather/outdoor/…` is now stated explicitly as a **role** — whatever
plays the outdoor-station part fills it — and every other own sensor lives in
`own/<id>/…` (`MQTT.md` §14, `SENSORS.md` case 5, D-013).

**2026-09-06: the first station exists on paper.** `esphome/weather-outdoor.yaml`
— XIAO ESP32C3, SHT30 (`0x44`, temperature + humidity) and BME280 (`0x76`,
pressure), both on 30-50 cm probe leads. It fills the `weather/outdoor/…` role
rather than living in `own/`. `esphome config` passes on 2026.8.2; it has not
been flashed, and the C5 stays the plan for whatever needs more pins later. The
BME280 has not arrived, so the first flash runs on the SHT30 alone.

Two decisions worth keeping:

- **`temperature_secondary`.** The BME280 also reports temperature, published to
  `weather/outdoor/temperature_secondary` and never used as an input. Two
  sensors disagreeing by more than their combined tolerance is the cheapest
  detector of a failing one that this station can carry. No ACL change: the
  `weather_collector` grant is already `topic write weather/#`.
- **Only the SHT30 stamps `last_update`.** That timestamp covers the whole input
  set, so letting the BME280 stamp it would let a live pressure sensor report a
  dead thermometer's retained value as fresh.

Everything downstream of the node is now in place too:

- `esphome/weather-outdoor-build.html` — the build sheet: pinout, wiring table,
  how to identify the probe's wires with a multimeter before powering it, the
  bring-up checklist, and where on the balcony it goes. Ticks persist in the
  browser, because this gets done over several evenings.
- `weather/outdoor/temperature_secondary` — entity added, marked diagnostic.
- `sensor.outdoor_thermometer_divergence` and
  `binary_sensor.outdoor_thermometers_disagree` in `data_quality.yaml`, with
  `input_number.outdoor_divergence_limit` to tune the threshold from the
  dashboard. The alarm needs the gap to hold for 30 minutes, and reports
  nothing at all while the BME280 is absent.
- `expected_inputs` in `config.example.yaml` cut to the three the station
  actually measures. **The live `weather-engine/config.yaml` on doctor is
  gitignored and still lists four, including `wind_speed`** — until it is
  trimmed by hand the engine reads `partial` forever.

Left for the owner, all in the build sheet: `esphome/secrets.yaml`, the
`weather_collector` password from `.env`, the soldering, the first flash over
USB, and `./scripts/normalize-entity-ids.sh` after Home Assistant restarts.

Ready and waiting for the first board:

- `esphome/own-sensor-reference.yaml` — copy-per-sensor config for `own/`
  sensors, **never built or flashed**, no C5 in hand. First flash is the test;
  its own checklist is at the bottom of the file.
- `own/` namespace, ACL pattern rules, four functional ACL tests passed.
- `expected_inputs:` in `weather-engine/config.yaml`, so a station that measures
  three things and not four reads `ok` instead of a permanent `partial`.

Two things to know before ordering: the C5 is **ESP-IDF only** in ESPHome and its
v1.0 silicon wants ESP-IDF ≥ 5.5.2 (pin it if the default lags); and Wi-Fi costs
100–150 mA with the radio up, so a battery outdoor node is a weeks-to-months
proposition, not years.

Snow is still not measured by anything in this plan, and that is a property of
piezo and tipping-bucket gauges rather than of the choice made here.

### X4 · Weather engine `[x]`
Done 2026-08-20. The container now derives and publishes, retained:
`weather_state/feels_like`, `frost_risk`, `ice_risk`, `data_quality`,
`computed_at`, `meta` and `engine_status`. It is part of the default stack
rather than the `engine` profile. Reasoning in `DECISIONS.md` D-012.

Everything published has a closed form or a written-out threshold rule, and
`derive.py` unit-tests all of them against published reference values:

```sh
docker compose run --rm --no-deps weather-engine python /app/main.py --selftest
```

Three of the pre-existing entities stay `unknown`, and that is the answer, not
a gap: `condition` needs precipitation type, `rain_risk` needs months of local
history to fit a probability against, `thunderstorm` needs strike rate rather
than distance-to-last-strike.

Two things changed from the plan in the process:

1. **`frost_risk` and `ice_risk` are categorical** — `none` / `watch` /
   `likely` — instead of `0–100 %`. The rules behind them are thresholds, and a
   threshold rendered as a percentage claims a probability nobody computed. Safe
   to change because no long-term statistics existed for them.
2. **Dew point and pressure tendency were not moved here.** Dew point stays a
   station measurement in `weather/outdoor/`; the engine computes it internally
   only when the station omits it. Pressure tendency stays in Home Assistant,
   which already holds the pressure history.

Verified on both freshness paths against the live broker: with the simulator's
last sample 4 h 55 m old it published `data_quality: degraded` and a
`computed_at` four hours in the past; one fresh sample later, `ok` with a 31 s
stamp. The negative path — outputs freezing and aging rather than being
overwritten with `unknown` — is the same behaviour as the MeshCore mirror.

Still true: with only the simulator publishing, these values describe simulated
weather. X3 is what makes them mean anything.

### X5 · MeshCore telemetry mirror `[x]`
Done 2026-08-20. The automation `MeshCore -> MQTT mirror` republishes an
allow-list of node metrics into `meshcore/<node_id>/<metric>`, retained, once a
minute: `battery`, `battery_voltage`, `status`, `last_seen`, `meta`. Verified
against the live node — `meshcore/044e2d/battery 73.08`,
`binary_sensor.meshcore_mirror_stale off`.

The design questions and their answers are in `DECISIONS.md` D-011: MQTT
Statestream was rejected on topic shape, the integration's own `mqtt_uploader`
turned out to be a LetsMesh packet feed rather than telemetry, and the metric
list is explicit rather than a loop because this mesh produces ~190 entities.

Two things changed from the original plan, both because the speculative version
did not survive contact with the hardware:

1. **Four of the six placeholder entities were deleted, not filled.** They
   expected `temperature`, `humidity`, `pressure` and `rssi`. This node carries
   no environmental sensor, and RSSI describes a received packet rather than a
   node. Publishing those topics would have been inventing data. They return
   under the same names when a node with a BME280 is attached.
2. **A 168-entity mess surfaced on the way** and was contained — see D-002's
   outcome note and `scripts/prune-meshcore-entities.sh`. Registry went from
   265 entities to 95.

What replaced the placeholders is the one thing the integration cannot report:
whether the MQTT copy is current. `sensor.meshcore_mirror_age` and
`binary_sensor.meshcore_mirror_stale` trip after ten minutes of silence from
either the automation or the radio.

### X6 · LPP pressure gap `[!]`
*Depends on: a mesh node that actually reports pressure.* `meshcore-ha` v2.9.0
does not map Cayenne LPP type 115 (barometer); such a reading arrives as a
generic unitless sensor. Fix locally with a template sensor, or upstream with a
mapping patch. Evidence in `MESHCORE.md`.

**Re-marked `[!]` on 2026-09-06, after checking instead of assuming.** This was
carrying `[ ]` as though it were merely unstarted work. It is not — there is no
pressure reading anywhere in this system to map. The whole retained contents of
`meshcore/#`, read off the broker:

```
meshcore/044e2d/battery          35.75
meshcore/044e2d/battery_voltage  3.429
meshcore/044e2d/status           offline
meshcore/044e2d/last_seen        2026-08-26T09:35:04+00:00
meshcore/044e2d/meta             {…}
```

Battery and nothing else, exactly as X5 records — this node has no
environmental sensor of any kind, let alone a barometer. Writing the template
sensor now would mean writing and testing a mapping against a value that has
never existed, which is the definition of speculative work. It stays written
down because the *finding* is worth keeping; it does not become a task until a
node with a BME280 exists.

**And the node behind those readings has been off the air since 2026-08-26.**
`status` reads `offline` and `last_seen` is eleven days old, which is the
freshness machinery working exactly as designed — nothing is stale-labelled as
current. Confirmed at the network layer rather than inferred: the Companion at
`192.168.1.93` answers no ICMP and gives `No route to host` on TCP/5000, so it
is not merely wedged, it is off the LAN. Nothing in the stack is broken by this
and no alert was warranted — the mirror reports it correctly — but it is worth
knowing before anyone debugs "why is there no mesh data", and it is the reason
X6, L1 and L2 cannot be moved forward from a keyboard today. Getting the node
back on is hands-on: it is a power or Wi-Fi question at the node, not a
software one here.

### X7 · Schedule backups `[x]`
Done. Daily `--full` at 04:30 via `/etc/cron.d/iot-stack-backup`, retaining 14,
into `/mnt/backup/iot-stack` (a real ext4 mount on `/dev/sda1`). The owner chose
that location knowing the archive holds every device password in plaintext; it
is written `chmod 600`.

Three defects were found by testing rather than by reading, and fixed:

1. `--full` **had never worked**. Home Assistant writes five `.storage` files as
   root with mode 600, so tar aborted with exit 2 at the last step. The script
   now re-executes itself through sudo for that mode, and hands the finished
   archive back to the invoking user.
2. Every archive was carrying `tools/meshcore-venv` — 1170 files of
   reinstallable dependencies, 4.9 MB of a 5.5 MB archive. Excluded.
3. The status publish used `mosquitto_pub -W`, which does not exist; the guard
   swallowed the error and nothing was ever published. Fixed and tested on both
   the success and the failure path.

Every run now publishes retained JSON to `monitor/backup/status`, surfaced as
`sensor.backup_last_run`, `sensor.backup_last_result`, `sensor.backup_age` and
`binary_sensor.backup_failing`. A backup that silently stops is the failure this
exists to prevent — see `DECISIONS.md` D-010.

---

### X8 · The Minecraft power plug `[ ]`
*Raised 2026-08-21 by the owner: "умная розетка на перезагрузку minecraft
сервера… надо бы её тоже подключить." Deferred to the next session by his own
call. Nothing has been changed.*

**What is already there**, read-only inspection on doctor:

| | |
|---|---|
| Plug | TP-Link Tapo at `192.168.1.134` |
| Driver | `tapo-api` container (`tapo_api.py`), `POST /on`, `POST /off`, up 3 months |
| Caller | `/home/sergey/scripts/mc-healthcheck.sh`, cron `*/2 * * * *` |
| Target | the Minecraft host `192.168.1.50` — a *different* machine |
| Role | last-resort rung of an escalation ladder: alert → RCON warn + restart → docker kill → **Tapo power reset** → WOL → dead |

**The design question, and it is the whole task.** The plug already has exactly
one owner, and that owner is a state machine that cuts mains power to a running
server. Adding Home Assistant's Tapo integration would create a second owner of
one relay — avoidance #1, in its most literal form: HA could toggle power while
`mc-healthcheck.sh` is mid-escalation. Default answer, unless the owner wants
otherwise: **Home Assistant observes and does not control.** The script stays
the actor and publishes what it did; HA renders state and history and can alert.
A manual switch in HA, if wanted at all, needs an interlock — not a second
opinion about the same relay.

**Two findings from the inspection, neither introduced by us:**

1. A power cycle failed on the record. `2026-08-12 03:38` — `off` returned 200,
   the follow-up `on` returned 500: `ConnectionRefused` to `192.168.1.134:80`.
   The plug's own HTTP endpoint refused right after the cut. So the server was
   left unpowered by a routine that intended to restart it. Whatever shape the
   integration takes, `on` needs a retry with backoff and an alert on final
   failure; the current one-shot is a coin flip at the worst moment.
2. `mc-healthcheck.sh` carries a Telegram bot token and chat id hardcoded in
   plaintext, and posts through an external bot API host. Out of scope for this
   stack, worth moving to an env file regardless.

**Live finding, 2026-08-21 01:40 — the hard reset is currently broken.** The
plug answers ICMP and sits in the ARP table (`6c:4c:bc:fe:f6:1d`, TP-Link), so
it has power and a Wi-Fi association. But **every TCP port is refused**: 80,
443, 8080, 8443, 9999, 10002, 10443 were all tried. `GET /status` on the
tapo-api container returns the same `ConnectionRefused` to `192.168.1.134:80`
as the failed `on` did on 2026-08-12. So this is not a one-off — the local API
has been unreachable for at least nine days, and the escalation ladder's last
rung would fail today if the Minecraft server hung. Likely a firmware update
that moved or closed the local API, or a device that needs re-adding in the
Tapo app; that is a hands-on check in the app, not something to guess at from
here.

Nothing was written to the plug during this inspection — only `GET /status` and
TCP connect attempts. `POST /on` / `POST /off` power-cycle a running server and
are not diagnostics.

**Re-checked 02:05 the same night, with the owner switching the plug on from
the Tapo app while the scan ran.** The app works, so the firmware is alive and
its cloud path is fine — but the local API stayed refused, and a sweep of every
live host on the LAN found **nothing listening on 80 except the router and the
RF node**. So the plug is not merely unreachable at `.134`; its local HTTP
service is not answering anywhere. The most likely cause is a firmware update
that closed local access, which fits the timeline — it worked before 2026-08-12
and has failed since — but that is inference, not a measurement.

The consequence is concrete: Home Assistant's built-in `tplink` integration is
**local-only** and will not find this plug in its current state. Options, in
order of preference and none of them started:

1. Get local access back — check the Tapo app for a third-party/local-access
   toggle, or whether the plug wants re-adding after the update. Hands-on.
2. Leave the plug where it is. `mc-healthcheck.sh` is the owner and it is the
   only thing that needs the plug; the fix belongs there either way.
3. A cloud-based Tapo component. Adds a cloud dependency to the hard reset of a
   local server, which is the wrong direction for the one rung that exists to
   work when everything else has failed.

**02:50 the same night — the owner switched the plug on from the app and added
it to Home Assistant. Both halves changed.**

*The local API is back.* `192.168.1.134` accepts TCP/80 again and
`GET /status` on tapo-api returns `ok`: `P110`, `server-socket`, `device_on:
true`, `on_time: 118`, 56 W, RSSI −12. So the hard-reset rung of
`mc-healthcheck.sh` is functional again without any change to the script. What
killed it is still not established — the plug kept its Wi-Fi association and
answered ICMP throughout the nine days, so "it was simply off" does not explain
it; toggling from the app is what brought the local service back.

*It went in through the built-in `tplink` integration* — the local one, not a
cloud component. Fifteen entities, including real metering:
`switch.server_socket`, `sensor.server_socket_voltage` (224.4 V),
`…_current` (0.34 A), `…_current_consumption` (68.2 W), today's and this
month's kWh, plus the plug's own auto-off controls.

**The two-owner problem is no longer hypothetical.** `switch.server_socket` and
`mc-healthcheck.sh` can both cut mains power to a running Minecraft server, and
neither knows about the other. Nothing is broken today because nothing in Home
Assistant automates that switch — and that is precisely the property to keep:

- **Home Assistant observes.** The metering entities are the win here: power
  draw is a genuine signal about the machine behind the plug, and reading it
  costs nothing. Do not build an automation that toggles `switch.server_socket`.
- **`mc-healthcheck.sh` stays the only actor.** It owns the escalation ladder;
  a power cut has to come from the component that knows whether RCON and a
  graceful restart were tried first.
- **Leave the plug's own auto-off alone.** `switch.server_socket_auto_off_enabled`
  is `off` and `number.server_socket_turn_off_in` reads 120. Enabling that would
  make the plug itself a third owner, on a timer, with no idea what it powers.

**Worth fixing in the script regardless of the above:** the 2026-08-12 event was
a single-shot `on` that failed and was never retried, and the ladder has no rung
after the power cut. `on` needs a retry with backoff and an alert when it
finally fails — the moment it matters is exactly the moment the server is
already unpowered.

**Not decided yet:** whether the plug's state reaches MQTT from the script, from
a poller, or from HA in read-only mode; whether it lives under `own/` or a new
namespace, since it is neither a sensor of mine nor somebody else's transmitter.
Decide that before writing anything.

**2026-09-06 — the script fix is written and tested, and not yet applied.**
Reading `mc-healthcheck.sh` turned up three defects in `tapo_power_reset()`,
not the one this entry recorded:

1. **`on` was one-shot and its result was discarded** — `curl -s … > /dev/null
   2>&1`, no status checked. This is exactly the 2026-08-12 failure: `off`
   returned 200, `on` returned 500, and the machine was left unpowered with
   nobody told.
2. **`off` was unchecked too.** A failed cut still logged "hard power reset"
   and still slept as though it had happened — so the log would have lied
   about the one action in this script that touches mains.
3. **The log says "waiting 90s for boot" and the code sleeps 20.** The next
   cron run at +2 min therefore lands on a machine that may still be booting.

Replacement written against a mock HTTP endpoint — the real plug was never
called, since `POST /on` and `/off` power-cycle a running server and are not
diagnostics. It checks both calls by HTTP status, retries `on` with 5/10/20/40 s
backoff (~2.5 min of trying), alerts loudly if power was cut and could not be
restored, and returns non-zero so the caller stops pretending the reset
happened. Four paths exercised:

| Case | Behaviour |
|---|---|
| `on` succeeds first try | exit 0, one call |
| `on` fails twice then succeeds — *the 2026-08-12 case* | recovers on attempt 3, exit 0 |
| `on` never succeeds | 🚨 alert naming the machine as unpowered, exit 1 |
| `off` itself fails | alerts, **does not cut power**, exit 1 |

Not applied: the file lives outside this repository, on doctor, and it is the
rung that cuts mains to a running server — applying it is the owner's call, not
something to do from here in passing. It is kept here in full rather than in a
scratch file, because a tested patch that only exists in a session transcript
is a patch that has to be written twice. Replace `tapo_power_reset()` in
`/home/sergey/scripts/mc-healthcheck.sh` with everything below, and check that
`TAPO_API`, `log` and `send_alert` are still named that way in the surrounding
script before doing so.

```bash
# ─── Tapo hard reset ──────────────────────────────────────────────────────────
# The last rung of the ladder: it cuts mains to a running server. Three things
# were wrong here and all three only bite at the worst possible moment.
#
#   1. `on` was one-shot and its result was thrown away. On 2026-08-12 `off`
#      returned 200 and `on` returned 500 (ConnectionRefused to the plug), so
#      the machine was left unpowered by a routine whose whole job was to
#      restart it. Nobody was told.
#   2. `off` was unchecked too, so a failed cut still logged "hard power reset"
#      and still slept as though it had happened.
#   3. The log said "waiting 90s for boot" and the code slept 20.
#
# Now: every call is checked by HTTP status, `on` is retried with backoff, and a
# machine left dark raises the loudest alert this script has. Returns non-zero
# if the power cycle did not complete, so callers can stop pretending it did.

TAPO_RETRIES="${TAPO_RETRIES:-5}"
TAPO_BOOT_WAIT="${TAPO_BOOT_WAIT:-90}"

# tapo_call <on|off> -> 0 if the plug answered 2xx
tapo_call() {
    local action="$1" code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
           -X POST "${TAPO_API}/${action}" 2>/dev/null)
    [ "${code:0:1}" = "2" ] || { log "TAPO: /${action} returned HTTP '${code:-no-response}'"; return 1; }
    return 0
}

tapo_power_reset() {
    log "ACTION: Tapo hard power reset — OFF → 5s → ON"

    if ! tapo_call off; then
        send_alert "⚠️ Tapo: не удалось выключить розетку. Жёсткий сброс НЕ выполнен, питание не трогали."
        return 1
    fi
    sleep 5

    # Restoring power is the half that must not fail silently. Backoff 5s, 10s,
    # 20s, 40s, 80s -- roughly 2.5 minutes of trying before giving up, which is
    # far better than one attempt and a dark machine.
    local delay=5 i
    for (( i = 1; i <= TAPO_RETRIES; i++ )); do
        if tapo_call on; then
            [ "$i" -gt 1 ] && log "TAPO: power restored on attempt $i"
            log "ACTION: waiting ${TAPO_BOOT_WAIT}s for the machine to boot..."
            sleep "$TAPO_BOOT_WAIT"
            return 0
        fi
        [ "$i" -lt "$TAPO_RETRIES" ] && { log "TAPO: /on failed (attempt $i/$TAPO_RETRIES), retrying in ${delay}s"; sleep "$delay"; delay=$(( delay * 2 )); }
    done

    send_alert "🚨 СЕРВЕР ОБЕСТОЧЕН. Питание снято, включить обратно не удалось (попыток: ${TAPO_RETRIES}). Нужны руки: розетка в приложении Tapo или физически."
    log "FATAL: power was cut and could not be restored after ${TAPO_RETRIES} attempts"
    return 1
}
```

**Still outstanding from the original entry, and unchanged:** the Telegram bot
token and chat id are still hardcoded in plaintext at the top of that script
(`BOT_TOKEN=`, `CHAT_ID=`, lines 9–10). The file is mode 0711 so it is not
world-readable, but a live bot token in a script under `/home/sergey/scripts`
is one `cat` away for anything running as that user. Move both to an env file
alongside the change above, and rotate the token when you do — it has been in
plaintext long enough that rotation is cheaper than auditing who has seen it.

### X9 · Alice / Yandex — half configured `[~]`
*Owner's call, 2026-08-21: "и под алису все установи, завтра настроим."
Both components are on the machine and load cleanly.*

**Status corrected 2026-09-06.** The entry below said "neither is configured",
and that stopped being true within the hour it was written. Read from
`core.config_entries` rather than from memory:

| Component | Config entry | Created |
|---|---|---|
| `yandex_station` | `tremer1988` | 2026-08-20 23:24 |
| `yandex_smart_home` | **none** | — |

So step 2 below is done — the station is authorised and the token is stored.
Step 1, the Alice → Home Assistant direction, and step 3, the exposure
allow-list, are still open and still the owner's part. Everything under
"Costs" applies as written, and now applies for real rather than
prospectively: the Yandex token is in `.storage` today and is in every
`--full` backup taken since 2026-08-20.

Two integrations, because they solve opposite problems:

| | Direction | Component | Installed |
|---|---|---|---|
| Voice control of what lives in HA | Alice → Home Assistant | `dext0r/yandex_smart_home` | v1.1.2 |
| HA speaking and playing through the speaker; Yandex smart-home devices pulled into HA | Home Assistant → station | `AlexxIT/YandexStation` | v3.21.4 |

Installed with `./scripts/install-custom-component.sh <repo>`, both pinned to
their latest release tag, both recording `.installed-from`. Neither declares any
Python `requirements`, so Home Assistant pulls nothing at startup and a boot
without internet is unaffected. Verified after a restart: HA answered in ~9 s,
both appear as loaded custom integrations, zero `ERROR`/`Traceback`, healthcheck
8/8.

**What is left, and it is all in the browser — the owner's part, not mine:**

1. `yandex_smart_home`: Settings → Devices & Services → Add Integration →
   *Умный дом Яндекса*. Choose the **cloud** connection type. Per its
   documentation the cloud type needs no public address, no port forward and no
   certificate — which is the only variant compatible with "не выставлять HA
   наружу". The direct type is explicitly not to be used here.
2. `yandex_station`: Add Integration → *Яндекс.Станция*, authorise by QR code.
   The component stores a token, not the password.
3. Then decide **which entities are exposed to Alice**. Default to a short
   allow-list rather than the whole registry: everything exposed becomes voice-
   reachable, including things that should not be.

**Costs, recorded so they are not rediscovered later:**

- Both are cloud paths by nature. Weather, MQTT and the sensors keep working
  with the internet unplugged — only voice stops. Do not put Alice on the
  required path of any automation; that would be avoidance #9 with extra steps.
- The Yandex token lands in `.storage` in plaintext, inside the HA config
  directory. That is account-level access, a wider blast radius than the
  Keenetic case that was declined on 2026-08-20.
- `--full` backups therefore now carry that token too. `BACKUP_RESTORE.md`
  already treats those archives as secret; this does not change the rule, it
  raises what the rule is protecting.

### X10 · The dog feeder (Tuya / Smart Life) `[~]`
*Owner, 2026-08-21: "еще должна быть умная кормушка для собаки в сети…
кормушка у меня в smart life." Found and identified; the component is
installed; nothing is configured.*

**Identified by a LAN sweep**, not by guessing:

| | |
|---|---|
| Address | `192.168.1.94` |
| MAC | `88:49:2d:47:64:ed` — Shenzhen Bilian Electronic, the usual OEM behind Tuya Wi-Fi modules |
| Open port | **6668/tcp** — the Tuya local protocol, and the only port it answers on |
| App | Smart Life, which is Tuya's own client |

It did not answer the UDP discovery broadcast during a 25-second listen on
6666/6667, so the model and protocol version are still unknown. That is common
on newer firmware and does not block anything: the address and the local
protocol are enough.

**The feeder has a camera, and it was re-paired at 02:00 while a scan ran.**
Results, because they change what is possible rather than merely how it is set
up:

- The feeder is still `192.168.1.94` and **still answers on 6668 and nothing
  else**. Re-pairing exposed no RTSP (554/8554), no ONVIF (2020/8000), no HTTP.
- A second listen on 6666/6667, 45 seconds, right after re-pairing: still
  silent.
- One new host appeared, `192.168.1.60`, MAC `c2:f3:59:…` — a locally
  administered address, i.e. a randomised MAC, with no open ports. That is the
  signature of a phone joining Wi-Fi, not of a feeder; the re-pairing was done
  from it. Noted so nobody chases it tomorrow.

So: **LocalTuya will give the controls — feed, portions, schedule, status — and
will not give the video.** Tuya cameras stream through Tuya's own P2P/cloud path
and expose no local stream; there is nothing on this device for `generic_camera`
or go2rtc to pull. If live video in Home Assistant matters, that is a separate
question with its own answer, and the honest short version is that this
particular device probably cannot do it locally at all.

**Installed:** `xZetsubou/hass-localtuya` 2026.7.0, no Python requirements,
loads cleanly. Chosen over the two alternatives on purpose:

- vs. Home Assistant's built-in `tuya` — that one is cloud-only. The feeder
  would stop responding when the internet does, which is the wrong property for
  something that dispenses food.
- vs. `make-all/tuya-local` — better curated device configs, but it needs the
  feeder's model to be in its library, and we do not know the model yet.
  LocalTuya can drive raw datapoints regardless, and its config flow can pull
  the device list and local keys from a Tuya account instead of hand-copying
  them. If the feeder turns out to be covered by `tuya-local`, switching is one
  script run and one config entry.

**Added by the owner, 2026-08-21 ~02:20 — through the built-in cloud `tuya`
integration, not LocalTuya.** It works, and it brought 12 entities:

`camera.smart_feeder` (streams, `supported_features=2`), `switch.*` for privacy
mode / flip / time watermark / motion alarm / motion zone, `select.*` for
anti-flicker and motion sensitivity, `number.smart_feeder_volume`,
`light.smart_feeder_indicator_light`, `event.smart_feeder_doorbell_message`,
`button.smart_feeder_restart`. Zero errors in the log.

**Two things follow, and both matter more than the setup itself:**

1. **The video works — over the cloud.** The earlier finding stands unchanged:
   the device offers no local stream, and nothing here contradicts that. But
   Tuya's own cloud path does deliver it, and that is what the owner used. The
   practical answer to "can I see the camera in Home Assistant" turned out to be
   yes, with an internet dependency attached.
2. **Not one of the 12 entities dispenses food.** Home Assistant's Tuya
   integration mapped this device as a *camera* category and exposed the camera
   half of it. Feeding, portions and schedule are not in Home Assistant at all —
   they still live only in Smart Life. That is exactly the gap LocalTuya fills,
   since it reads raw datapoints regardless of how the device is categorised,
   and it is already installed. The local key is still the only blocker.

Note the split that now exists: the camera arrives over the cloud, and the
feeding — if it is wired up at all — would arrive locally over 6668. Two
transports to one device is acceptable here because they carry different
things and neither commands the other. Feeding schedules must still live in
exactly one place.

**What is left — the owner's part, because it needs an account:** every local
Tuya integration needs the device's **local key**, which only the Tuya IoT
Platform hands out. That means registering a (free) Tuya IoT developer account
and linking the Smart Life account to it. LocalTuya's config flow does the rest
by itself. Nothing else is blocking.

**Note on ownership:** while Smart Life keeps talking to the feeder through
Tuya's cloud, local control does not conflict with it — the device accepts both
— but scheduled feeding must live in exactly one place. Two schedulers feeding
one dog is avoidance #1 with a real-world consequence.

### X11 · Zigbee2MQTT `[~]` — bridge live, network empty
*Deployed by the owner 2026-09-05 (commit `4304409`). Documented and audited
2026-09-06, which is what this entry is.*

The bridge is real and healthy. Read from the running stack, not the compose
file:

| | |
|---|---|
| Container | `iot-zigbee2mqtt`, `koenkk/zigbee2mqtt:2.14.1`, part of the default stack |
| Coordinator | ZB-GW04 stick reflashed to **EmberZNet 8.0.3 [GA]**, EZSP v16, build 581 |
| Serial | `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` → `/dev/ttyUSB0`, `adapter: ember` |
| Network | up; `pan_id` set, network key in `zigbee2mqtt/data/configuration.yaml` |
| Broker | connected as `zigbee2mqtt`; `bridge/state` = `{"state":"online"}` |
| Discovery | seven bridge entities announced into `homeassistant/…/config` |
| Web UI | <http://192.168.1.51:8099> — pairing and OTA |
| **Devices paired** | **0** |

**The commit shipped code and no documentation, and that is what was actually
unfinished.** Nine documents describe this stack and none of them knew Zigbee
existed: `SERVICES.md` listed three services, `README.md` said "all three",
`MQTT.md` had no `zigbee2mqtt/` namespace and an accounts table missing an
account, `BACKUP_RESTORE.md` did not mention that the archive now carries a
Zigbee network key, `ACCEPTANCE.md` counted eight accounts, and there was no
decision entry at all. All of that is now written, and D-014 records why the
bridge sits outside Home Assistant rather than inside it as ZHA.

**One real defect was found by testing the ACL rather than reading it.** The
`homeassistant` account had `read zigbee2mqtt/#` and no write rule, so Home
Assistant could see Zigbee devices and command none of them — and the bridge's
own permit-join, restart and log-level entities were dead too, since their
command topics are under `zigbee2mqtt/bridge/request/`. Measured under MQTT 5:
`RC:135 not authorized` on `zigbee2mqtt/probe/set`, `RC:16 accepted` on a
control topic. This is the `sensors/+/cmd/#` bug from the first deployment
repeating exactly, and the reason it hides is the same: under MQTT 3.1.1 the
broker acknowledges a denied publish and drops it silently. Fixed with three
narrow rules, plus a single-topic read grant for `monitor` so `healthcheck.sh`
can see the bridge's last will. **Applied on doctor 2026-09-06 and verified by
measurement, not by reading the file** — five publishes under MQTT 5:

| Account → topic | Result |
|---|---|
| `homeassistant` → `zigbee2mqtt/probe/set` | `RC:0` — accepted *and delivered*, so the bridge is subscribed |
| `homeassistant` → `zigbee2mqtt/bridge/request/permit_join` | `RC:0` |
| `homeassistant` → `zigbee2mqtt/probe` (device state) | `RC:135` — still refused, which is the point |
| `monitor` → `zigbee2mqtt/bridge/request/#` | `RC:135` |
| `monitor` subscribes `zigbee2mqtt/bridge/state` | `{"state":"online"}` |

The bridge logged `Entity 'probe' is unknown` and `Invalid payload` for those
two probes. Both are the expected complaint about a made-up device and an empty
payload, and both are better evidence than the reason codes alone: they prove
the publish reached the bridge rather than merely satisfying the broker.

It cost nothing in the field because the network is empty. It would have cost
an evening of "the lamp is in Home Assistant but the switch does nothing".

**What is left, in order:**

1. ~~**Apply the ACL fix on doctor**~~ — done 2026-09-06, table above.

   **The pull was not clean, and the reason is worth keeping.**
   `4304409` was committed on doctor and never pushed — `git cat-file -t
   4304409` in this repository says *Not a valid object name*, so it exists on
   exactly one machine. `origin/main` was at `a7788f8` until this branch landed
   and knew nothing about it. So doctor's history and origin's have diverged
   and `git pull` there will want to merge.

   It is safe to throw doctor's commit away, because its *content* is already
   here. Compared file by file:

   | File in `4304409` | Against this repository |
   |---|---|
   | `docker-compose.yml` | byte-identical, empty diff |
   | `.gitignore` | differs only by the added `esphome/secrets.yaml` |
   | `mosquitto/config/acl.conf` | differs only by the four rules of the ACL fix |

   Every line of it was a subset, so discarding doctor's commit lost nothing.

   **`reset --hard` would have been the wrong tool, and not for the obvious
   reason.** It compares the *index* against the target, not the working file —
   so it would have rewritten `acl.conf` through a rename, replacing the inode
   and silently detaching the container's view of a per-file bind mount. And
   the usual check does not catch it: the content is identical either way, so
   host and container `md5sum` still agree while the mount is already dead.
   **Compare the inode, not the sum.** What was actually done:

   ```sh
   cd /home/sergey/iot-stack
   git status --short        # clean but for four *.bak — nothing to lose
   git fetch origin
   # write acl.conf in place FIRST: truncating keeps the inode, and afterwards
   # the file already matches the target, so git has no reason to touch it
   git show origin/main:mosquitto/config/acl.conf \
     | sudo sh -c 'cat > mosquitto/config/acl.conf'
   git reset --mixed origin/main   # moves HEAD and index, leaves the worktree
   git checkout -- .               # writes only files that still differ
   ```

   Verified after: inode `49169836` before and after, unchanged, and equal to
   the container's; owner and mode still `1883:sergey 0640`; all three per-file
   mounts (`acl.conf`, `mosquitto.conf`, `passwd`) matching inside and out.
   Then `kill -HUP 1`, which reloads without dropping a connection.

   The four `*.bak-20260905-211643` files are untracked and survived it. Safe
   to delete: the other three are tracked, so their old content is in git, and
   `.env.bak` holds no key absent from the live `.env`.
2. **Pair the actual devices.** Owner's part, and hands-on by nature: each
   device has to be put into pairing mode physically. Permit-join is off and
   should be turned on only for the minute it takes, from the web UI.
3. **Name every device the moment it pairs.** The friendly name *is* the topic
   segment and the entity id, so renaming later moves the topic and breaks
   every automation and card that referenced it. `MQTT.md` §6b.
4. **Rehearse one restore before the network is worth anything.** `zigbee2mqtt/data/`
   is now the second irreplaceable thing in the stack after the recorder
   database — lose it and every device gets re-paired by hand. It is in the
   `--full` archive and untested as a restore. `BACKUP_RESTORE.md`.

**Not a task, but worth stating so it is not re-litigated:** the choice of
Zigbee2MQTT over ZHA is cheap to reverse *today* and expensive after the first
device is paired, because the network key and device table live in a format ZHA
does not read. D-014.

### X12 · The MeshCore Companion over USB is not visible on doctor `[ ]` — needs the owner, hands on the hardware
*Raised 2026-09-06. Next session: 2026-09-07.* The owner reports the T114 USB
companion is plugged in and the job is done. Doctor disagrees, and the
disagreement is what has to be resolved before anything downstream is believed:

| Checked on doctor | Result |
|---|---|
| `ls /dev/serial/by-id/` | only `usb-1a86_USB_Serial-if00-port0` — the ZB-GW04 Zigbee stick |
| any `/dev/ttyACM*` | none |
| MeshCore integration `devices:` | `null` |
| `meshcore/044e2d/status` | `offline`, retained, unchanged since 2026-08-26 |

Nothing here proves the board is faulty — it proves doctor cannot see it. The
owner's own two hypotheses are the first two to test, in this order, because
both are cheap and both are common:

1. **It is in a different machine.** Easiest to settle: look at which box the
   cable actually runs to.
2. **The cable is charge-only.** Kit cables with no data pair are everywhere and
   are visually identical to working ones. The board powers up, the LED lights,
   nothing enumerates — which is exactly the symptom above. Swap in a cable
   known to carry data and re-check `ls /dev/serial/by-id/`.

Only if both are excluded does it become a firmware or driver question.

**Why this waits for the owner:** every step is physical — reading a label,
following a cable, swapping one. Nothing about it can be established from a
shell, which is why guessing further from here would only produce confident
fiction.

Blocks: `MESHCORE.md` §"Planned: moving to the T114 over USB" and the
`ARCHITECTURE.md` diagram both still describe the pre-USB arrangement. Neither
gets rewritten until the true state is known.

---

## LATER / EXPERIMENTAL

### L1 · MeshCore live map `[ ]`
*Depends on: X8 (a real packet feed) and one verification.* Topics line up
(`meshcore/#` vs `meshcore/{IATA}/{PUBLIC_KEY}/packets`), the hex field name
lines up (`raw` is in its `LIKELY_PACKET_KEYS`), but the map reads `rssi`/`snr`
lowercase while `meshcore-ha` publishes `RSSI`/`SNR`, and there is no key-case
normalization in its decoder. Verify with a real feed before installing.
Read-only consumer, so it cannot damage the data path — that is the point of
having it downstream of MQTT.

### L2 · Enable the packet feed `[ ]`
*Depends on: L1 being wanted.* `meshcore-ha` can publish packets to MQTT; the
namespace is reserved (`radio/raw/meshcore/…`) and the ACL rule exists. Not
enabled, because a packet stream from a 168-node mesh with no consumer is just
disk and CPU.

### L3 · RF collector ingestion `[!]`
*Depends on: the rebuilt Tuya IR/RF device at 192.168.1.48.* The contract is
written (`SENSORS.md` §2, allow-list flow), the simulator proves the whole path,
and the ACL is in place. Passive reception of unencrypted broadcasts only.

The node's own agent has published a hardware audit and is building a sensor
collector on it. Coordination — including the answered questions (a)–(d) — is in
`../COORDINATION-rf-node.md`. What that audit settles, and what nothing in this
repo can change: **the node has no FSK**, so Fine Offset/Ecowitt, Bresser and
LaCrosse are permanently out of its reach. It hears OOK — the cheap 433 MHz
thermo-hygrometer population — on 315, 433.92 and 868 MHz, one frequency at a
time.

### L3b · rtl_433 over the node's pulse data `[ ]`
*Depends on: L3, and on nothing else — no dongle needed.* The node's
`?format=ook` output is genuine rtl_433 `pulse_data`. A small host service that
polls it, runs `rtl_433 -r` and lets rtl_433's **native** MQTT output publish the
decode gives decoded third-party sensors with hardware already on the shelf, and
costs zero broker changes — `rtl_433/#`, the `rtl433` account and the HA read
grant all exist. Agreed with the node agent as the decode path (D-005 again: the
decoder is not ours to write).

Non-negotiable condition, recorded when this was agreed: **no entity is created
from `rtl_433/#` except by explicit allow-listing.** The gate in `SENSORS.md` §2
is what stops an open 433 MHz band becoming hundreds of rows, and moving decode
to the host must not quietly bypass it.

### L4 · rtl_433 with a real SDR `[!]`
*Depends on: an SDR dongle — now optional rather than planned.* Config exists and
uses rtl_433's **native** MQTT output (`DECISIONS.md` D-005). Its remaining value
is precisely the gap L3b cannot fill: **FSK** neighbours on 868 MHz, which the
node cannot demodulate at all. Whether that is worth ~3–4k ₽ is answerable for
free first — scan with the node and see what is actually on the air.

### L5 · The normalizer `[ ]`
*Depends on: two collectors hearing the same transmitter.* Today there is one
collector, so dedup would be theatre. The identity format, the `observed/…`
namespace and the metadata contract are already fixed — that is the part that is
expensive to change later. `MQTT.md` §12–13, and the dedup-key rule is already
fixed in `DECISIONS.md` D-009 — key on transmitter bytes, never on RSSI, hops or
receive time.

### L6 · Long-term storage `[ ]`
*Depends on: having history worth exporting.* Recorder holds 774 KB. Revisit
when a year of pressure data exists, or when a question needs SQL that recorder
cannot answer. `DECISIONS.md` D-006.

### L7 · MeshCore → home control `[ ]`
*Depends on: X5, and on an identity allow-list.* Sending an alert out over the
mesh is easy and safe. Accepting a *command* from the mesh is not: it needs an
allow-list by public key, because otherwise any participant can talk to the
house. Do not build the inbound direction without it.

### L8 · RemoteTerm as radio owner `[ ]`
*Depends on: a second Companion node.* Not a competitor to `meshcore-ha` on one
radio — they cannot share it. With two nodes both can run. Note its own warning
that bots execute arbitrary Python.

### L9 · ESP32-S3 watch firmware `[ ]`
*Depends on: hardware.* The backend already does what it needs to: every value
is retained, the `watch` account is read-only, and no HTTP frontend is in the
path. Nothing here blocks it.

---

## Explicitly not planned

| | Why |
|---|---|
| A homegrown rtl_433 parser | upstream publishes MQTT itself — D-005 |
| `meshcoretomqtt` | needs USB repeater + custom firmware build — D-004 |
| Two applications on one Companion | the transport evicts the older client — D-001 |
| An entity per discovered MeshCore contact | 168 of them — D-002 |
| InfluxDB/Prometheus/Grafana now | nothing to store yet — D-006 |
| MeshCore as transport for WiFi sensors | LoRa is for where WiFi is not; avoidance #10 in the brief |
