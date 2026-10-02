#!/usr/bin/python3
"""Подсветка bigpc в такт светомузыке (homeassistant/config/packages/light_music.yaml).

На каждый такт script.light_music_show шлёт в ha/light_music/pc
«hue sat bright period» (0–360, 0–100, 0–100, секунды), в конце — «off».
Интеграция OpenRGB в HA тратит 0,6 с на команду и такт не держит, поэтому
кадры идут прямо в OpenRGB SDK, 30 в секунду: вспышка на такт, затухание,
цвет перетекает в новый за полтакта. Тактов нет 3 периода — плату не трогает
(после стопа HA возвращает свою подсветку сам).

Настройки — /etc/default/pc-rgb-fader (KEY=VALUE): MQTT_PASS обязателен,
остальное по умолчанию ниже. Учётка: scripts/add-mqtt-user.sh pc_rgb.
Зависимости: paho-mqtt (1.6+), openrgb-python (0.3.6, как в HA).
Проверка без железа: pc_rgb_fader.py --selftest
"""
import colorsys, os, signal, sys, threading, time

for path in [a for a in sys.argv[1:] if a != "--selftest"]:
    for line in open(path, encoding="utf-8"):
        k, sep, v = line.strip().partition("=")
        if sep and not k.startswith("#"):
            os.environ.setdefault(k.strip(), v.strip())

MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.1.51")
MQTT_USER = os.environ.get("MQTT_USER", "pc_rgb")
TOPIC = "ha/light_music/pc"
RGB_HOST = os.environ.get("OPENRGB_HOST", "192.168.1.10")   # bigpc
DEVICE = os.environ.get("OPENRGB_DEVICE", "MSI")            # подстрока имени платы
FPS = 30
GLIDE = 0.5    # доля такта на переход цвета
FLOOR = 0.55   # к концу такта яркость падает до этой доли вспышки

beat = {"t": 0.0, "h0": 0.0, "h1": 0.0, "s": 1.0, "b": 0.0, "p": 1.0}
shown = {"h": 0.0}
lock = threading.Lock()


def frame(b, now):
    """Кадр (h, s, v) на момент now или None, если тактов давно нет."""
    if not b["t"] or now - b["t"] > max(3.0, 3 * b["p"]):
        return None
    ph = min(1.0, (now - b["t"]) / b["p"])
    dh = (b["h1"] - b["h0"] + 180) % 360 - 180   # короткой дорогой по кругу
    h = (b["h0"] + dh * min(1.0, ph / GLIDE)) % 360
    return h, b["s"], b["b"] * (FLOOR + (1 - FLOOR) * (1 - ph) ** 2)


def on_message(client, userdata, msg):
    parts = msg.payload.decode(errors="replace").split()
    with lock:
        if parts[:1] == ["off"]:
            beat["t"] = 0.0
            return
        try:
            h, s, b, p = map(float, parts)
        except ValueError:
            print(f"не разобрал {msg.payload!r}", flush=True)
            return
        beat.update(t=time.monotonic(), h0=shown["h"], h1=h % 360,
                    s=min(1.0, s / 100), b=min(1.0, b / 100), p=max(0.2, p))


def connect():
    from openrgb import OpenRGBClient
    cli = OpenRGBClient(RGB_HOST, 6742, "pc-rgb-fader")
    dev = next(d for d in cli.devices if DEVICE in d.name)
    # HA's effect "off" asks for Direct and this board falls back to Breathing;
    # Static holds a colour.
    if dev.modes[dev.active_mode].name != "Static":
        dev.set_mode("Static")
    print(f"openrgb: {dev.name}, {len(dev.leds)} LED", flush=True)
    return cli, dev


def on_connect(client, userdata, flags, rc, *props):  # rc: int (1.6) или ReasonCode (2.x)
    print(f"mqtt: подключение {rc}", flush=True)
    client.subscribe(TOPIC)


def main():
    import paho.mqtt.client as mqtt
    from openrgb.utils import RGBColor
    c = (mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="pc-rgb-fader")
         if hasattr(mqtt, "CallbackAPIVersion") else mqtt.Client(client_id="pc-rgb-fader"))
    c.username_pw_set(MQTT_USER, os.environ["MQTT_PASS"])
    c.on_connect = on_connect
    c.on_message = on_message
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    c.connect_async(MQTT_HOST, 1883, keepalive=60)
    c.loop_start()

    cli, dev, tried, active = None, None, 0.0, False
    while True:
        now = time.monotonic()
        with lock:
            f = frame(beat, now)
        if f is None:
            active, tried = False, 0.0
            time.sleep(0.1)
            continue
        if dev is None or not active:   # заново на каждую светомузыку: bigpc мог перезагрузиться
            if now - tried < 10:
                time.sleep(0.1)
                continue
            tried = now
            if cli:
                try:
                    cli.disconnect()
                except OSError:
                    pass
            try:
                cli, dev = connect()
            except (OSError, StopIteration) as e:
                print(f"openrgb: {e!r}", flush=True)
                cli = dev = None
                continue
            active = True
        h, s, v = f
        shown["h"] = h
        try:
            dev.set_color(RGBColor(*(int(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s, v))), fast=True)
        except OSError as e:
            print(f"openrgb: {e!r}", flush=True)
            active, tried = False, 0.0
        time.sleep(max(0.0, 1 / FPS - (time.monotonic() - now)))


def selftest():
    b = {"t": 10.0, "h0": 350.0, "h1": 30.0, "s": 1.0, "b": 0.8, "p": 1.0}
    h, s, v = frame(b, 10.0)
    assert (round(h), round(v, 2)) == (350, 0.8), (h, v)            # вспышка на такт
    h, s, v = frame(b, 10.25)
    assert round(h) == 10, h                                         # через 0, а не через 180
    h, s, v = frame(b, 11.5)
    assert (round(h), round(v, 2)) == (30, 0.44), (h, v)             # такт кончился — держит пол
    assert frame(b, 13.1) is None and frame(dict(b, t=0.0), 10.0) is None
    print("selftest ok")


if __name__ == "__main__":
    selftest() if "--selftest" in sys.argv else main()
