#!/usr/bin/python3
"""Состояние компьютера -> MQTT (own/<id>/<metric>, MQTT.md §14).

Один файл на все машины стека: Linux (systemd), macOS (LaunchAgent), Windows
(Планировщик). Чего на машине нет (vcgencmd, UPS BetaPi, датчики температуры
на mac/Windows) — просто не публикуется.

Настройки — переменные окружения или файл KEY=VALUE первым аргументом
(пример: tools/host-mqtt.env.example). Зависимости: paho-mqtt (1.6+), psutil.
"""
import json, os, platform, signal, subprocess, sys, time
from datetime import datetime, timezone

for path in [a for a in sys.argv[1:] if a != "--selftest"]:
    for line in open(path, encoding="utf-8"):
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())

import psutil

HOST = os.environ.get("MQTT_HOST", "192.168.1.51")
PORT = int(os.environ.get("MQTT_PORT", "1883"))
USER = os.environ.get("MQTT_USER", platform.node().split(".")[0].lower())
PASS = os.environ.get("MQTT_PASS", "")
BASE = "own/" + os.environ.get("MQTT_ID", USER)
INTERVAL = int(os.environ.get("INTERVAL_S", "30"))
BOARD = os.environ.get("BOARD", platform.system().lower())
DISK = os.environ.get("DISK_PATH", "C:\\" if os.name == "nt" else "/")
# имена чипов hwmon: первый найденный — температура CPU
CPU_SENSORS = os.environ.get("CPU_SENSORS", "cpu_thermal,coretemp,k10temp").split(",")
UPS_STATE = os.environ.get("X1202_STATE", "/run/x1202-guard/state.json")
UPS_MAX_AGE = int(os.environ.get("UPS_MAX_AGE_S", "60"))


def temps():
    """cpu_temperature, nvme_temperature (самый горячий из NVMe); только Linux."""
    t = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    cpu = next((t[n][0].current for n in CPU_SENSORS if t.get(n)), None)
    nvme = max((s.current for s in t.get("nvme", []) if s.label in ("", "Composite")), default=None)
    return {"cpu_temperature": cpu and round(cpu, 1), "nvme_temperature": nvme and round(nvme, 1)}


def vcgencmd(*args):
    try:
        return subprocess.run(["vcgencmd", *args], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def parse_ext5v(out):
    # "     EXT5V_V volt(24)=5.16570000V"
    return round(float(out.split("=")[1].rstrip("V\n")), 2) if "=" in out else None


def parse_throttled(out):
    # "throttled=0x50005"
    return int(out.split("=")[1], 16) if "=" in out else None


def ups(now):
    """ac_ok/battery_* из файла x1202-guard (BetaPi); устаревший файл = данных нет."""
    try:
        s = json.load(open(UPS_STATE))
    except (OSError, ValueError):
        return {}
    if now - s.get("ts", 0) > UPS_MAX_AGE:
        return {}
    return {"ac_ok": int(s["ac_ok"]), "battery_voltage": s["volt"], "battery_pct": s["pct"]}


def collect():
    du = psutil.disk_usage(DISK)
    m = {
        "cpu_used_pct": psutil.cpu_percent(),  # среднее с прошлого вызова
        "disk_used_pct": du.percent,  # как df: без резерва root
        "disk_free_gb": round(du.free / 1e9, 1),
        "ram_used_pct": psutil.virtual_memory().percent,
        "load_1m": round(psutil.getloadavg()[0], 2),
        "ext5v_voltage": parse_ext5v(vcgencmd("pmic_read_adc", "EXT5V_V")),
        "throttled": parse_throttled(vcgencmd("get_throttled")),
    }
    m.update(temps())
    m.update(ups(time.time()))
    return {k: v for k, v in m.items() if v is not None}


def selftest():
    assert parse_ext5v("     EXT5V_V volt(24)=5.16570000V\n") == 5.17
    assert parse_ext5v("") is None
    assert parse_throttled("throttled=0x50005\n") == 0x50005
    assert parse_throttled("") is None
    global UPS_STATE
    UPS_STATE = "/nonexistent"
    assert ups(time.time()) == {}
    m = collect()
    assert {"cpu_used_pct", "disk_used_pct", "ram_used_pct"} <= m.keys(), m
    print(BASE, json.dumps(m))
    print("selftest ok")


def main():
    import paho.mqtt.client as mqtt
    cid = BASE.replace("/", "-")
    c = (mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
         if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client(client_id=cid))  # paho 1.6 в Ubuntu 24.04
    c.username_pw_set(USER, PASS)
    c.will_set(BASE + "/status", "offline", qos=1, retain=True)

    def on_connect(client, userdata, flags, rc, *props):  # rc: int (1.6) или ReasonCode (2.x)
        print(f"mqtt: подключение {rc}", flush=True)
        if rc == 0:
            client.publish(BASE + "/status", "online", qos=1, retain=True)
            client.publish(BASE + "/meta", json.dumps({
                "sensor_id": BASE.split("/")[1], "board": BOARD,
                "boot_time": datetime.fromtimestamp(psutil.boot_time(), timezone.utc).isoformat(timespec="seconds"),
            }), qos=1, retain=True)

    c.on_connect = on_connect
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    c.connect_async(HOST, PORT, keepalive=60)
    c.loop_start()
    try:
        while True:
            if not c.is_connected():  # ждём связи, а не целый интервал
                time.sleep(1)
                continue
            for k, v in collect().items():
                c.publish(f"{BASE}/{k}", str(v), retain=True)
            c.publish(BASE + "/last_update",
                      datetime.now(timezone.utc).isoformat(timespec="seconds"), retain=True)
            time.sleep(INTERVAL)
    finally:  # штатная остановка: LWT не сработает, offline пишем сами
        c.publish(BASE + "/status", "offline", qos=1, retain=True).wait_for_publish(5)
        c.disconnect()


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
