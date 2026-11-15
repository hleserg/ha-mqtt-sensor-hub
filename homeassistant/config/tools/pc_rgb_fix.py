#!/usr/bin/env python3
"""Подсветка bigpc: доделывает то, чего интеграция OpenRGB в HA сама не может.

Сервер OpenRGB 1.0 молча отвергает режим, если поля в описании от клиента не
совпали с его собственными, и заново применяет старый. На плате MSI Z790 из-за
этого не проходят: Direct от openrgb-python (так HA выключает и включает «без
эффекта») и Static, пока сервер считает его цвет случайным, — а таким он его
читает с платы после каждого своего старта. Проходит Static, которому цвет по
светодиодам назначен явно. Подробности — OPERATIONS.md, «bigpc fan RGB».

  pc_rgb_fix.py norm   после старта сервера: Static со «случайным» цветом →
                       Static с цветом по светодиодам. Печатает fixed, ok
                       (чинить нечего) или skip (идёт эффект, его не трогаем).
  pc_rgb_fix.py off    погасить из любого режима. Печатает off.

Вызывает packages/pc_rgb.yaml. Проверка — вживую: OPERATIONS.md, там же.
"""
import sys

from openrgb import OpenRGBClient
from openrgb.utils import ModeColors, RGBColor

HOST, DEVICE = "192.168.1.10", "MSI"   # bigpc, подстрока имени платы


def static(dev, brightness):
    m = next(m for m in dev.modes if m.name == "Static")
    m.color_mode = ModeColors.PER_LED
    m.brightness = brightness
    dev.set_mode(m)   # сам перечитывает устройство с сервера
    m = dev.modes[dev.active_mode]
    if m.name != "Static" or m.color_mode != ModeColors.PER_LED:
        sys.exit(f"сервер не принял Static, остался {m.name!r}")


def main(action):
    cli = OpenRGBClient(HOST, 6742, "ha-pc-rgb-fix")
    try:
        dev = next(d for d in cli.devices if DEVICE in d.name)
        full = next(x for x in dev.modes if x.name == "Static").brightness_max
        if action == "off":
            static(dev, 0)   # сначала яркость режима в ноль: без вспышки старым цветом
            dev.set_color(RGBColor(0, 0, 0))
            static(dev, full)
            return "off"
        m = dev.modes[dev.active_mode]
        if m.color_mode == ModeColors.PER_LED:
            return "ok"
        if m.name != "Static":
            return "skip"
        static(dev, full)
        return "fixed"
    finally:
        cli.disconnect()


if __name__ == "__main__":
    if sys.argv[1:] not in (["norm"], ["off"]):
        sys.exit(__doc__)
    print(main(sys.argv[1]))
