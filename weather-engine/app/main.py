"""weather-engine — derived weather state.

Reads the normalized measurement namespace, computes the quantities that follow
from physics rather than from a model, and publishes them retained under
`weather_state/`.

Three rules shape everything below.

*Nothing is invented.* Only closed-form, documented formulas and stated rules
appear here (`derive.py`). Outputs that would need history the stack does not
have — rain probability, thunderstorm approach, a condition label — are not
published at all. They keep reading `unknown` in Home Assistant, which is the
honest answer.

*Nothing is presented as current without its age.* Every publish is stamped with
`weather_state/computed_at`, and that stamp is the *measurement* time of the
inputs, never the wall clock. A derivation from a four-hour-old reading
therefore looks four hours old, exactly like the reading it came from.

*Words are never payloads.* If an input is missing or unparseable the affected
output is left alone — its previous retained value stays and ages. `unknown`
and `unavailable` are Home Assistant states, not measurements, and they never
travel over MQTT from here.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml

import derive

VERSION = "1.0"

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("weather-engine")

CONFIG_PATH = Path(os.environ.get("CONFIG_PATH", "/app/config.yaml"))

# Latest value per topic, with the time we received it. Reception time is kept
# separately from measurement time on purpose: a buffered node can deliver an
# hour-old reading, and by reception time that looks perfectly fresh.
_latest: dict[str, dict] = {}
_lock = threading.Lock()
_stop = threading.Event()
_published: dict[str, str] = {}

# What the derivation can read. Everything else in `inputs:` is subscribed for
# the log summary and for whatever comes later.
USED = (
    "temperature",
    "humidity",
    "pressure",
    "wind_speed",
    "dew_point",
    "rain_rate",
    "surface_temperature",
)

# Without a temperature there is nothing downstream to compute, so its absence
# is `no_data` rather than a grade. This one is not configurable: it is a
# property of the derivations, not of the station.
REQUIRED = ("temperature",)

# What `data_quality` is graded against comes from the config key
# `expected_inputs:` — the set the *owner declares this station is supposed to
# provide*. It is not the set of everything the engine could use, and that
# distinction is the whole point.
#
# Grading against "everything conceivable" pins the quality at `partial`
# forever on any station that lacks a rain gauge or a surface probe, and a
# signal that is permanently yellow is wallpaper. Grading against a declared
# set makes the question answerable: *did I get what this station promises?*
# The owner is building sensors one at a time — a node that measures only
# temperature, humidity and pressure is complete for months, and it should read
# `ok`. The day the anemometer is added, `wind_speed` joins `expected_inputs:`
# and a dead anemometer correctly drops the grade.
EXPECTED_DEFAULT = ("temperature", "humidity", "pressure", "wind_speed")


def load_config() -> dict:
    if not CONFIG_PATH.is_file():
        log.error("config not found at %s", CONFIG_PATH)
        sys.exit(1)
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}

    mqtt_cfg = cfg.setdefault("mqtt", {})
    # Environment wins over the file, so credentials can come from .env and
    # never have to be written into a mounted config.
    mqtt_cfg["host"] = os.environ.get("MQTT_HOST", mqtt_cfg.get("host", "mosquitto"))
    mqtt_cfg["port"] = int(os.environ.get("MQTT_PORT", mqtt_cfg.get("port", 1883)))
    mqtt_cfg["username"] = os.environ.get(
        "WEATHER_ENGINE_MQTT_USER", mqtt_cfg.get("username", "weather_engine")
    )
    mqtt_cfg["password"] = os.environ.get(
        "WEATHER_ENGINE_MQTT_PASSWORD", mqtt_cfg.get("password", "")
    )

    if not mqtt_cfg["password"]:
        log.error(
            "no MQTT password: set WEATHER_ENGINE_MQTT_PASSWORD in .env "
            "(the broker does not accept anonymous clients)"
        )
        sys.exit(1)

    if not cfg.get("inputs"):
        log.error("config has no `inputs:` section, nothing to subscribe to")
        sys.exit(1)

    cfg.setdefault("outputs", {}).setdefault("prefix", "weather_state")
    cfg["outputs"].setdefault("retain", True)
    cfg.setdefault("stale_after_seconds", 900)
    cfg.setdefault("offline_after_seconds", 3600)
    cfg.setdefault("compute_interval_seconds", 30)

    expected = cfg.get("expected_inputs") or list(EXPECTED_DEFAULT)
    if not isinstance(expected, list):
        log.error("`expected_inputs:` must be a list, got %r", type(expected).__name__)
        sys.exit(1)
    unknown = [n for n in expected if n not in USED]
    if unknown:
        # Refuse rather than ignore. A typo here would silently grade against a
        # name that can never be present, pinning the quality at `partial`
        # forever — the precise failure this key exists to prevent.
        log.error(
            "`expected_inputs:` names nothing the derivation reads: %s (known: %s)",
            ", ".join(unknown), ", ".join(USED),
        )
        sys.exit(1)
    for name in REQUIRED:
        if name not in expected:
            log.error("`expected_inputs:` must contain %r — nothing derives without it", name)
            sys.exit(1)
    no_topic = [n for n in expected if not cfg["inputs"].get(n)]
    if no_topic:
        # Not fatal: a topic may be added the same day the sensor is. But it is
        # a permanent `partial` until then, so it must be said out loud once at
        # startup rather than discovered from a yellow badge weeks later.
        log.warning(
            "expected inputs with no topic under `inputs:`: %s "
            "— data_quality will stay `partial` until they are configured",
            ", ".join(no_topic),
        )
    cfg["expected_inputs"] = expected
    return cfg


# ------------------------------------------------------------------ helpers --


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(cfg: dict, name: str) -> float | None:
    """Latest value of a named input as a float, or None if absent/garbage."""
    topic = cfg["inputs"].get(name)
    if not topic:
        return None
    with _lock:
        entry = _latest.get(topic)
    if not entry:
        return None
    try:
        return float(entry["value"])
    except (TypeError, ValueError):
        log.warning("input %s is not a number: %r", name, entry["value"][:32])
        return None


def _measured_at(cfg: dict):
    """The measurement timestamp the derivation should be stamped with.

    The station's own `last_update` when it publishes one — that is the whole
    point of the identity contract in `MQTT.md`. Reception time is the fallback,
    and the fallback is reported, because the two are not interchangeable.
    """
    topic = cfg["inputs"].get("last_update")
    if topic:
        with _lock:
            entry = _latest.get(topic)
        if entry:
            try:
                ts = datetime.fromisoformat(entry["value"].replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                return ts, "station_last_update"
            except ValueError:
                log.warning("last_update is not a timestamp: %r", entry["value"][:32])

    newest = None
    for name in USED:
        t = cfg["inputs"].get(name)
        with _lock:
            entry = _latest.get(t) if t else None
        if entry:
            got = datetime.fromisoformat(entry["received_at"])
            newest = got if newest is None or got > newest else newest
    return newest, "reception_time"


def _quality(cfg: dict, age):
    """How much of the *declared* input set is there, and how old it is.

    This is the one output that keeps updating while the station is silent: it
    describes the inputs rather than being derived from them, so freezing it
    would hide exactly the situation it exists to report.
    """
    expected = cfg["expected_inputs"]
    present = [n for n in USED if _num(cfg, n) is not None]
    missing = [n for n in USED if n not in present]
    expected_missing = [n for n in expected if n not in present]
    # The age deliberately does NOT go into the metadata. It ticks every cycle,
    # `meta` is retained and mirrored into the attributes of
    # sensor.weather_state_data_quality, and a value that changes every 30 s
    # would write a recorder row every 30 s to say nothing new. The age is
    # already available two better ways: `computed_at` on this same device, and
    # sensor.outdoor_data_age, which exists precisely for this.
    detail = {
        "inputs_present": present,
        "inputs_missing": missing,
        "expected_inputs": list(expected),
        "expected_missing": expected_missing,
    }
    if not all(_num(cfg, n) is not None for n in REQUIRED):
        return "no_data", detail
    if age is None or age > cfg["offline_after_seconds"]:
        return "degraded", detail
    if age > cfg["stale_after_seconds"] or expected_missing:
        return "partial", detail
    return "ok", detail


# --------------------------------------------------------------- derivation --


def compute(cfg: dict):
    """Return (topic suffix -> payload, metadata, input age). Absent outputs are
    omitted entirely rather than published as a placeholder."""
    measured_at, stamp_source = _measured_at(cfg)
    age = None if measured_at is None else (_now() - measured_at).total_seconds()

    quality, detail = _quality(cfg, age)
    out = {"data_quality": quality}
    meta = {
        "engine_version": VERSION,
        "quality": quality,
        "timestamp_source": stamp_source,
    }
    meta.update(detail)

    temp = _num(cfg, "temperature")
    if temp is None:
        # Nothing downstream of temperature can be computed. Previously
        # published values keep their retained state and keep aging.
        meta["computed"] = []
        return out, meta, age

    humidity = _num(cfg, "humidity")
    wind = _num(cfg, "wind_speed")
    rain_rate = _num(cfg, "rain_rate")
    surface = _num(cfg, "surface_temperature")

    dew = _num(cfg, "dew_point")
    dew_source = "station"
    if dew is None and humidity is not None:
        dew = derive.dew_point(temp, humidity)
        dew_source = "magnus_fallback"
    if dew is None:
        dew_source = "unavailable"

    value, method = derive.feels_like(temp, humidity, wind)
    out["feels_like"] = str(value)
    meta["feels_like_method"] = method

    frost, basis = derive.frost_risk(temp, dew, surface)
    out["frost_risk"] = frost
    meta["frost_basis"] = basis

    out["ice_risk"] = derive.ice_risk(temp, humidity, rain_rate, surface)

    meta["dew_point_source"] = dew_source
    meta["dew_point"] = dew
    meta["computed"] = ["feels_like", "frost_risk", "ice_risk"]

    if measured_at is not None:
        out["computed_at"] = measured_at.isoformat()

    return out, meta, age


def publish_cycle(client, cfg: dict) -> None:
    prefix = cfg["outputs"]["prefix"]
    retain = bool(cfg["outputs"]["retain"])
    out, meta, age = compute(cfg)
    out["meta"] = json.dumps(meta, sort_keys=True)

    changed = []
    for suffix, payload in out.items():
        if _published.get(suffix) == payload:
            continue
        client.publish(prefix + "/" + suffix, payload, qos=1, retain=retain)
        _published[suffix] = payload
        changed.append(suffix)

    if changed:
        log.info(
            "published %s  (quality=%s, input age=%ss)",
            ", ".join(sorted(changed)),
            meta["quality"],
            "?" if age is None else round(age),
        )


def worker(client, cfg: dict) -> None:
    interval = int(cfg["compute_interval_seconds"])
    summary_every = max(1, int(cfg.get("summary_interval_seconds", 300)) // interval)
    tick = 0
    while not _stop.wait(interval):
        tick += 1
        try:
            publish_cycle(client, cfg)
        except Exception:  # a bad input must not kill the loop
            log.exception("compute cycle failed")
        if tick % summary_every == 0:
            with _lock:
                snapshot = dict(_latest)
            log.info("holding %d input topics", len(snapshot))
            for topic in sorted(snapshot):
                log.info("  %-42s %s", topic, snapshot[topic]["value"][:64])


# ---------------------------------------------------------------- mqtt glue --


def on_connect(client, userdata, flags, reason_code, properties=None):
    if reason_code != 0:
        log.error("connect failed: %s", reason_code)
        return
    cfg = userdata["config"]
    for name, topic in cfg["inputs"].items():
        client.subscribe(topic, qos=1)
        log.info("subscribed  %-22s %s", name, topic)
    client.publish(
        cfg["outputs"]["prefix"] + "/engine_status", "online", qos=1, retain=True
    )
    log.info(
        "connected to %s:%s as %s — engine v%s",
        cfg["mqtt"]["host"],
        cfg["mqtt"]["port"],
        cfg["mqtt"]["username"],
        VERSION,
    )


def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8", errors="replace")
    with _lock:
        _latest[msg.topic] = {
            "value": payload,
            "received_at": _now().isoformat(),
            "retained": bool(msg.retain),
        }


def _quality_selftest() -> None:
    """Grade against a declared set, with no broker and no config file.

    `_quality` reads inputs through `_num`, which reads `_latest`, so the whole
    grader is testable by writing straight into that dict. What is being
    checked is the property the `expected_inputs:` key exists for: a station
    that provides exactly what it declares reads `ok`, and adding a sensor to
    the declaration before the sensor exists is what makes it `partial`.
    """
    now = _now()

    def station(*names):
        _latest.clear()
        for n in names:
            _latest["t/" + n] = {"value": "1.0", "received": now}
        return {
            "inputs": {n: "t/" + n for n in USED},
            "stale_after_seconds": 900,
            "offline_after_seconds": 3600,
        }

    def grade(expected, *present, age=10.0):
        cfg = station(*present)
        cfg["expected_inputs"] = list(expected)
        return _quality(cfg, age)

    tri = ("temperature", "humidity", "pressure")

    q, d = grade(tri, *tri)
    assert q == "ok", q
    assert d["expected_missing"] == [], d
    # The three-sensor node is missing four things the engine could use, and is
    # still complete. That gap is the entire point of the key.
    assert set(d["inputs_missing"]) == set(USED) - set(tri), d

    q, _ = grade(tri + ("wind_speed",), *tri)
    assert q == "partial", q

    q, _ = grade(tri, *tri, age=1200.0)
    assert q == "partial", q
    q, _ = grade(tri, *tri, age=7200.0)
    assert q == "degraded", q
    q, _ = grade(tri, *tri, age=None)
    assert q == "degraded", q

    # No temperature outranks everything, including a declaration that does not
    # ask for one anywhere else.
    q, _ = grade(tri, "humidity", "pressure")
    assert q == "no_data", q

    # A station may declare less than it sends; the extra is not a fault and
    # does not lift or lower the grade.
    q, d = grade(("temperature",), *tri)
    assert q == "ok", q
    assert d["expected_inputs"] == ["temperature"], d

    _latest.clear()
    print("quality selftest: OK")


def main() -> None:
    if "--selftest" in sys.argv:
        derive._selftest()
        _quality_selftest()
        return

    cfg = load_config()
    mqtt_cfg = cfg["mqtt"]

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=cfg.get("client_id", "weather-engine"),
        userdata={"config": cfg},
    )
    client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
    client.on_connect = on_connect
    client.on_message = on_message
    client.will_set(
        cfg["outputs"]["prefix"] + "/engine_status", "offline", qos=1, retain=True
    )

    def shutdown(signum, frame):
        log.info("signal %s, shutting down", signum)
        _stop.set()
        client.publish(
            cfg["outputs"]["prefix"] + "/engine_status", "offline", qos=1, retain=True
        )
        client.disconnect()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    threading.Thread(target=worker, args=(client, cfg), daemon=True).start()

    log.info("weather-engine v%s starting (config: %s)", VERSION, CONFIG_PATH)
    client.connect(mqtt_cfg["host"], mqtt_cfg["port"], keepalive=60)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
