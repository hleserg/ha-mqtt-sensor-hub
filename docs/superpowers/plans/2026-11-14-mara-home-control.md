# Мара управляет домом — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Мара включает и выключает свет и телевизор по просьбе в Telegram или голосом, отвечает тишиной или «Ок» и сама дописывает словарь, когда её поправили.

**Architecture:** Нового кода в Hermes нет. Рычаги `ha_*` уже встроены и включаются токеном: заводим в Home Assistant отдельного не-админа «Мара», кладём его токен в `.env` Hermes, пишем один навык `mara/home-control` (правила + словарь + журнал уроков), добавляем одну фразу-исключение в `SOUL.md` и одну строку crontab для копий навыка.

**Tech Stack:** Home Assistant 2026.9.4 (REST и WebSocket API, контейнер `iot-homeassistant` на doctor), Hermes Agent на mac-mini (CLI `hermes chat`, инструменты `ha_call_service` и `skill_manage`), bash + ssh с Pi, python3 (`requests` на Pi, `aiohttp` внутри контейнера HA), launchd, crontab.

**Spec:** `docs/superpowers/specs/2026-11-14-mara-home-control-design.md` — исполнитель читает оба файла.

## Global Constraints

- Файлы на маке правятся только точечно, с копией `.bak-20261114` и md5 до и после; одна правка за раз. Если md5 «до» не совпал с записанным — стоп: файл меняет другая сессия (`~/mara`).
- Шлюз `ai.hermes.gateway` рестартуется только по отдельному «да» владельца и не во время хода Мары.
- `hermes plugins enable` не запускать (переписывает `config.yaml`). Модель Мары не менять.
- Всё, что меняет состояние устройств или запускает Мару, — днём и после предупреждения владельца в чате. После проверки устройство возвращается в исходное состояние.
- Значения токенов и пароля не печатаются в терминал и не попадают в план, `HANDOFF.md` и git. Файлы с токеном — права 600.
- `SOUL.md` целиком не читать. Содержимое `~/mask/heard` не читать и не цитировать — только имена файлов.
- Навык в git не кладётся. После начала живых проверок (Task 6) его правит только Мара; с моей стороны — чтение и откат.
- Алису не трогаем: `yandex_smart_home` и его фильтр не меняются.
- Рабочие файлы на Pi — в `~/.cache/mara-ha/` (не `/tmp`: его стирает перезагрузка).
- Вне задачи: быстрый путь без модели, снятие устройств с Алисы, объявления голосом Мары, музыка и таймеры, починка слуха.

## Review Focus

Пять случаев, о которых спек молчит, а человек наткнётся первым. У каждого — проверка в задаче-владельце.

1. **Двусмысленное имя** («включи люстру» — пиратская или кухонная): один короткий вопрос, ни одна лампа не меняется. → Task 3, шаг 9.
2. **Две просьбы в одной фразе** («выключи ночник и подсветку компа»): выполнены обе, ответ один. → Task 3, шаг 8.
3. **Лампа не умеет то, что просят** («приглуши свет в прихожей», а она только вкл/выкл): одна короткая фраза, состояние не меняется. → Task 3, шаг 10.
4. **Обычный разговор с меткой маски** («Мара, как настроение?»): исключение в `SOUL.md` не расползлось, рычаги не тронуты, ответ обычный. → Task 4, шаг 8.
5. **Дозапись урока портит словарь** (пропала строка или якорь): после поправки в файле только добавления, якорь ровно один. → Task 6, шаг 6.

## Отступления от спека и находки

- Спек запрещает Маре действие `edit`. У движка такого нет; реальные действия `skill_manage` — `create`, `patch`, `delete`, `write_file`, `remove_file`. В навыке разрешён только `patch`, остальные четыре запрещены поимённо.
- Строка-якорь в тексте правил не цитируется: `patch` требует уникального совпадения, а якорь, повторённый в инструкции, сделал бы его неуникальным.
- Якорь — HTML-комментарий (в таблице он не виден как лишняя строка). Сканер навыков Hermes (`tools/skills_guard.py:319`) считает подозрительным комментарий со словами `ignore`, `override`, `system`, `secret`, `hidden` — в якорях их нет. Сам сканер для правок Мары выключен (`skills.guard_agent_created` в `config.yaml` не задан).
- Словарь только растёт, поэтому правило конфликта — «нижняя строка главнее».
- «Маленькая лампочка» и «Светомузыка» без зоны в HA и без подсказки в названии: комната `не задана`, ответ «Ок».
- «Телевизор» в словаре — это скрипты `script.tv_on` / `tv_off` / `tv_volume` (их же дёргает Алиса); `media_player.televizor` оставлен запасной строкой.
- Проверка 6 спека (гость просит «хозяйскую» строку) делается тихо из консоли с имитацией метки маски — владельцу не нужно изображать гостя.
- Проверка 7 спека: команда копии проверяется сразу ручным запуском; что cron сам сработал в 04:45 — пункт в `todo.vsiakii_umnyi_dom` на 15.11.
- `NO_REPLY` в журналах Hermes не виден (0 вхождений в `agent.log`, `gateway.log`, `errors.log`). Тишину подтверждает владелец: маска молчит, в Telegram пусто.
- Шлюз перечитывает `.env` на каждом ходу (`gateway/run.py:2083`), но держит кэш агента и системного промпта на сессию, а `session_reset.mode: none`. Новый навык и фраза в `SOUL.md` до живой сессии Мары, скорее всего, без рестарта не дойдут. Спрашиваю «да» отдельно, как велит спек (Task 6, шаг 3).

## Файлы

| Где | Файл | Что это |
|---|---|---|
| Pi | `~/.cache/mara-ha/ha.py` | три админских запроса к HA для проверок: состояние, вернуть как было, пункт в todo |
| Pi | `~/.cache/mara-ha/ask.sh` | один тихий ход Мары из консоли мака |
| Pi | `~/.cache/mara-ha/mara_user.py`, `run_in_ha.sh` | создание (и откат) пользователя «Мара» внутри контейнера HA |
| Pi | `~/.cache/mara-ha/home-control/` | исходник навыка — эталон для сверки |
| HA | пользователь «Мара» (логин `mara`), токен `hermes` | учётка рычагов |
| mac | `~/.hermes/skills/mara/home-control/SKILL.md`, `references/lessons.md` | навык |
| mac | `~/.hermes/.env` | +2 строки: `HASS_URL`, `HASS_TOKEN` |
| mac | `~/.hermes/SOUL.md` | +2 строки после строки 46 |
| mac | crontab | +1 строка, 04:45 |
| repo | `HANDOFF.md` | состояние после каждой задачи |

---

### Task 1: Учётка «Мара» и токен в Home Assistant

**Files:**
- Create (Pi): `~/.cache/mara-ha/ha.py`, `~/.cache/mara-ha/mara_user.py`, `~/.cache/mara-ha/run_in_ha.sh`
- Create (Pi, права 600): `~/.cache/mara-ha/hass_token`, `~/.cache/mara-ha/hdr`
- Create (HA): пользователь «Мара», долгоживущий токен `hermes`

**Interfaces:**
- Consumes: `~/.config/ha.env` (`HA_URL`, `HA_TOKEN` — админ), `ssh doctor`.
- Produces: `~/.cache/mara-ha/hass_token` — одна строка, токен Мары; `~/.cache/mara-ha/ha.py st <entity_id>…` → строки `entity_id состояние last_changed`; `ha.py set <entity_id> on|off`; `ha.py todo "текст"`.

- [ ] **Step 1: Помощник `ha.py`**

```bash
mkdir -p ~/.cache/mara-ha && chmod 700 ~/.cache/mara-ha
cat > ~/.cache/mara-ha/ha.py <<'EOF'
#!/usr/bin/env python3
"""ha.py — три админских запроса к HA для проверок плана. Токен из ~/.config/ha.env, не печатается.
  ha.py st <entity_id>…          состояние и last_changed
  ha.py set <entity_id> on|off   вернуть устройство в исходное состояние
  ha.py todo "текст"             пункт в todo.vsiakii_umnyi_dom
"""
import os, sys, requests

env = {}
for line in open(os.path.expanduser("~/.config/ha.env")):
    k, sep, v = line.strip().partition("=")
    if sep and not k.startswith("#"):
        env[k] = v.strip("\"'")
URL = env["HA_URL"].rstrip("/")
H = {"Authorization": "Bearer " + env["HA_TOKEN"]}


def post(service, **data):
    r = requests.post("%s/api/services/%s" % (URL, service), headers=H, json=data, timeout=10)
    r.raise_for_status()


cmd, args = (sys.argv[1:2] or [""])[0], sys.argv[2:]
if cmd == "st":
    for e in args:
        r = requests.get("%s/api/states/%s" % (URL, e), headers=H, timeout=10)
        r.raise_for_status()
        j = r.json()
        print(e, j["state"], j["last_changed"])
elif cmd == "set":
    assert args[1] in ("on", "off"), "on|off"
    post("homeassistant/turn_" + args[1], entity_id=args[0])
elif cmd == "todo":
    post("todo/add_item", entity_id="todo.vsiakii_umnyi_dom", item=args[0])
else:
    sys.exit(__doc__)
EOF
chmod +x ~/.cache/mara-ha/ha.py
```

- [ ] **Step 2: Проверить помощник**

Run: `~/.cache/mara-ha/ha.py st light.torsher light.net_takoi`
Expected: первая строка `light.torsher on 2026-…+00:00` (или `off`), затем падение `requests.exceptions.HTTPError: 404` на несуществующей сущности. Так и должно быть: опечатка в `entity_id` не проходит молча.

- [ ] **Step 3: Скрипт создания пользователя**

Выполняется внутри контейнера HA (там есть `aiohttp`, а запрос с `localhost` считается локальным — это нужно для флага «только локальная сеть»). Переменные `ADMIN_TOKEN` и `MODE` дописывает сверху `run_in_ha.sh`.

```bash
cat > ~/.cache/mara-ha/mara_user.py <<'EOF'
# Гонится ВНУТРИ контейнера iot-homeassistant: python3 - < этот файл.
# ADMIN_TOKEN и MODE ("create" | "delete") приходят двумя строками сверху от run_in_ha.sh.
import asyncio, secrets, sys
import aiohttp

BASE = "http://localhost:8123"
CLIENT = BASE + "/"


async def ws_open(session, token):
    ws = await session.ws_connect(BASE + "/api/websocket")
    assert (await ws.receive_json())["type"] == "auth_required"
    await ws.send_json({"type": "auth", "access_token": token})
    assert (await ws.receive_json())["type"] == "auth_ok", "HA не принял токен"
    return ws


async def call(ws, mid, **msg):
    await ws.send_json({"id": mid, **msg})
    while True:
        r = await ws.receive_json()
        if r.get("id") == mid and r.get("type") == "result":
            if not r["success"]:
                sys.exit("HA отказал на %s: %s" % (msg["type"], r["error"]))
            return r["result"]


async def main():
    async with aiohttp.ClientSession() as s:
        ws = await ws_open(s, ADMIN_TOKEN)
        users = await call(ws, 1, type="config/auth/list")
        mine = [u for u in users if u["name"] == "Мара" and not u["is_owner"] and not u["system_generated"]]
        if MODE == "delete":
            for u in mine:
                await call(ws, 9, type="config/auth/delete", user_id=u["id"])
                print("удалён:", u["id"][:8], file=sys.stderr)
            return
        if mine or any(u.get("username") == "mara" for u in users):
            sys.exit("пользователь «Мара» уже есть — стоп")
        user = (await call(ws, 2, type="config/auth/create", name="Мара",
                           group_ids=["system-users"], local_only=True))["user"]
        print("создан:", user["id"][:8], user["group_ids"], user["local_only"], file=sys.stderr)
        password = secrets.token_urlsafe(32)   # нигде не сохраняется: админ сбросит в интерфейсе
        await call(ws, 3, type="config/auth_provider/homeassistant/create",
                   user_id=user["id"], username="mara", password=password)
        await ws.close()

        r = await s.post(BASE + "/auth/login_flow", json={
            "client_id": CLIENT, "handler": ["homeassistant", None], "redirect_uri": CLIENT})
        flow = await r.json()
        r = await s.post(BASE + "/auth/login_flow/" + flow["flow_id"], json={
            "username": "mara", "password": password, "client_id": CLIENT})
        step = await r.json()
        assert step.get("type") == "create_entry", "вход под mara не удался: %s" % step.get("errors")
        r = await s.post(BASE + "/auth/token", data={
            "grant_type": "authorization_code", "code": step["result"], "client_id": CLIENT})
        access = (await r.json())["access_token"]

        ws = await ws_open(s, access)
        token = await call(ws, 1, type="auth/long_lived_access_token", client_name="hermes", lifespan=3650)
        await ws.close()
        print(token)


asyncio.run(main())
EOF
cat > ~/.cache/mara-ha/run_in_ha.sh <<'EOF'
#!/bin/bash
# run_in_ha.sh create|delete — гонит mara_user.py внутри контейнера HA.
# Токен админа уходит по stdin, в аргументах команд его нет.
set -euo pipefail
{ python3 - "$1" <<'PY'
import os, sys
for line in open(os.path.expanduser("~/.config/ha.env")):
    k, _, v = line.strip().partition("=")
    if k == "HA_TOKEN":
        print("ADMIN_TOKEN = %r" % v.strip("\"'"))
print("MODE = %r" % sys.argv[1])
PY
  cat ~/.cache/mara-ha/mara_user.py
} | ssh doctor 'docker exec -i iot-homeassistant python3 -'
EOF
chmod +x ~/.cache/mara-ha/run_in_ha.sh
```

- [ ] **Step 4: Создать пользователя и выпустить токен**

Run:
```bash
( umask 077; ~/.cache/mara-ha/run_in_ha.sh create > ~/.cache/mara-ha/hass_token )
wc -c < ~/.cache/mara-ha/hass_token; cut -c1-3 ~/.cache/mara-ha/hass_token; stat -c %a ~/.cache/mara-ha/hass_token
```
Expected: в stderr `создан: <8 символов> ['system-users'] True`; затем число больше 150, `eyJ`, `600`.

Если упало после строки «создан:» — пользователь есть, токена нет. Откат и повтор: `~/.cache/mara-ha/run_in_ha.sh delete` (ждём `удалён: …`), затем этот шаг заново. Второй отказ — стоп, владельцу четыре шага в интерфейсе HA: «Настройки → Люди → Пользователи → Добавить» (имя «Мара», логин `mara`, не администратор, «только локальная сеть») → войти под ней → «Профиль → Безопасность → Долгосрочные токены доступа» → создать `hermes` → положить токен одной строкой в `~/.cache/mara-ha/hass_token` на Pi.

- [ ] **Step 5: Проверка 1 спека — токен рабочий и не админский**

Run:
```bash
( umask 077; printf 'Authorization: Bearer %s\n' "$(cat ~/.cache/mara-ha/hass_token)" > ~/.cache/mara-ha/hdr )
for p in /api/ /api/states/light.torsher /api/error_log; do
  printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' -H @"$HOME/.cache/mara-ha/hdr" "http://192.168.1.51:8123$p"
done
```
Expected: `/api/ 200`, `/api/states/light.torsher 200`, `/api/error_log 401` (403 тоже годится). Если `/api/error_log` дал 200 — пользователь вышел админом: `run_in_ha.sh delete` и стоп.

- [ ] **Step 6: HANDOFF и коммит**

В `HANDOFF.md`, в раздел задачи: «Task 1 готов: пользователь „Мара“ (не админ, только локальная сеть), токен `hermes` лежит в `~/.cache/mara-ha/hass_token` на Pi до Task 3; откат — `run_in_ha.sh delete`».

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара — учётка HA и токен готовы, проверка 1 пройдена" && git push
```

---

### Task 2: Навык `home-control` на маке

**Files:**
- Create (Pi): `~/.cache/mara-ha/home-control/SKILL.md`, `~/.cache/mara-ha/home-control/references/lessons.md`
- Create (mac): `~/.hermes/skills/mara/home-control/SKILL.md`, `~/.hermes/skills/mara/home-control/references/lessons.md`

**Interfaces:**
- Consumes: `ha.py st` из Task 1.
- Produces: навык с именем `home-control`; якорь словаря — строка, начинающаяся с `<!-- якорь-словаря`; якорь журнала — строка, начинающаяся с `<!-- якорь-журнала`. Копия на Pi остаётся эталоном для сверки в Task 6.

- [ ] **Step 1: Написать `SKILL.md`**

```bash
mkdir -p ~/.cache/mara-ha/home-control/references
```

Файл `~/.cache/mara-ha/home-control/SKILL.md`, целиком:

````markdown
---
name: home-control
description: Управление домом через Home Assistant — когда просят включить, выключить, приглушить или перекрасить свет, включить или выключить телевизор, сделать громче или тише, нажать кнопку устройства. В Telegram или голосом через «[маска…]». Словарь «как называют → рычаг» и правило ответа — молча или «Ок».
version: 1.0.0
author: Сергей (hleserg)
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [home, homeassistant, light, tv]
    related_skills: [pronunciation]
---
# Дом: рычаги и словарь

Дом управляется через Home Assistant, рычаг один — `ha_call_service`. Понять просьбу, дёрнуть рычаг один раз, почти ничего не сказать.

## Цикл

1. Кто просит — раздел «Кто просит».
2. Найди просьбу в словаре — по смыслу, не по букве. Одинаковое название в двух строках — верь нижней: это поздняя поправка.
3. Нашлось одно устройство — один вызов `ha_call_service`. Несколько просьб в одной фразе — по одному вызову на каждую, ответ один на всё.
4. Ответь по таблице «Что ответить» и остановись. Сделанное не пересказывай.

Не нашлось ничего или подходят разные устройства («люстра» — пиратская или кухонная) — один короткий вопрос, рычаг не трогай. Не угадывай.

Состояние до и после вызова не проверяй: каждый лишний ход — секунды тишины.

## Кто просит

| Откуда пришло | Кто это | Что можно |
|---|---|---|
| Telegram или консоль, метки «[маска…]» нет | хозяин | весь словарь; чего в словаре нет — раздел «Устройства нет в словаре» |
| «[маска, вижу boss…]» | хозяин | то же |
| «[маска, вижу guest-N…]» или «[маска, никого не вижу…]» | гость | только строки, где «кто» — `все` |

Гость просит строку «кто — `хозяин`» или устройство вне словаря — одна короткая фраза отказа («Это только хозяину»). Рычаг не трогай, словарь не меняй.

## Что ответить

| Ситуация | Ответ |
|---|---|
| Голосом («[маска…]»), комната устройства — «Комната» | ровно `NO_REPLY`: результат и так виден |
| Голосом, комната другая, «весь дом» или «не задана» | `Ок` |
| Telegram | `Ок` |
| Рычаг вернул ошибку или Home Assistant молчит | повтори вызов один раз; снова нет — одна короткая фраза («Торшер не отвечает») |
| Устройство так не умеет (яркость у «только вкл/выкл») | одна короткая фраза («Эта только включается и выключается»), рычаг не трогай |
| Нет в словаре или подходят два | один короткий вопрос |

Несколько просьб в одной фразе голосом: все устройства в «Комнате» — `NO_REPLY`, иначе `Ок`.

## Как дёргать

`data` — строка JSON.

| Что просят | Вызов |
|---|---|
| включить, выключить свет | `ha_call_service(domain="light", service="turn_on")` или `"turn_off"`, `entity_id` из словаря |
| яркость | `light.turn_on`, `data='{"brightness_pct": 30}'`: «приглуши» — 30, «наполовину» — 50, «поярче», «на полную» — 100 |
| цвет | `light.turn_on`, `data='{"rgb_color": [255, 0, 0]}'` |
| тёплый, холодный белый | `light.turn_on`, `data='{"color_temp_kelvin": 2700}'` или `6500` |
| выключатель | `domain="switch"`, `turn_on` или `turn_off` |
| кнопка | `domain="button"`, `service="press"` |
| переключатель | `domain="input_boolean"`, `turn_on` или `turn_off` |
| скрипт | `domain="script"`, `service` — имя скрипта (`tv_on`), без `entity_id` |
| громкость телевизора | `domain="script"`, `service="tv_volume"`, `data='{"steps": 1}'`: 1 — громче, -2 — на два тише |

## Словарь

| как называют | entity_id | действие | комната | кто |
|---|---|---|---|---|
| пиратская люстра (вся) | `light.piratskaya_lyustra` | вкл/выкл, яркость, цвет | Комната | все |
| пиратская люстра 1 | `light.piratskaya_lyustra_1` | вкл/выкл, яркость, цвет | Комната | все |
| пиратская люстра 2 | `light.piratskaya_lyustra_2` | вкл/выкл, яркость, цвет | Комната | все |
| пиратская люстра 3 | `light.piratskaya_lyustra_3` | вкл/выкл, яркость, цвет | Комната | все |
| пиратская люстра 4 | `light.piratskaya_lyustra_4` | вкл/выкл, яркость, цвет | Комната | все |
| половина пиратской люстры | `switch.polovina_piratskoi_liustry` | вкл/выкл (выключатель) | Комната | все |
| одна лампочка пиратской люстры | `switch.odna_lampochka_piratskoi_liustry` | вкл/выкл (выключатель) | Комната | все |
| торшер в комнате | `light.torsher` | вкл/выкл, яркость, цвет | Комната | все |
| подсветка компа | `light.podsvetka_kompa` | только вкл/выкл | Комната | все |
| лисёнок ночник | `light.lisenok_nochnik` | вкл/выкл, яркость | Комната | все |
| маленькая лампочка | `light.malenkaia_lampochka` | вкл/выкл, яркость, цвет | не задана | все |
| кухня люстра 1 | `light.kuhnya_lyustra_1` | вкл/выкл, яркость, цвет | Кухня | все |
| кухня лампочки над стеной | `light.kukhnia_lampochki_nad_stenoi` | только вкл/выкл | Кухня | все |
| прихожая | `light.prihozhaya` | только вкл/выкл | Прихожая | все |
| свет в коридоре | `light.svet_v_koridore` | только вкл/выкл | Коридор | все |
| свет в ванной | `light.svet_v_vannoi` | только вкл/выкл | Ванная | все |
| весь свет выкл | `script.ves_svet_vykl` | скрипт `ves_svet_vykl` | весь дом | все |
| светомузыка | `input_boolean.svetomuzyka` | вкл/выкл (переключатель) | не задана | все |
| телевизор: включить | `script.tv_on` | скрипт `tv_on` | Комната | все |
| телевизор: выключить | `script.tv_off` | скрипт `tv_off` | Комната | все |
| телевизор: громкость | `script.tv_volume` | скрипт `tv_volume`, `steps` | Комната | все |
| телевизор (запасной путь, если скрипты молчат) | `media_player.televizor` | `media_player.turn_on` / `turn_off` | Комната | все |
| ТВ-приставка Mi TV Stick | `remote.tv_pristavka_mi_tv_stick` | `remote.turn_on` / `turn_off` | Комната | все |
| пульт: питание телевизора | `button.living_room_ir_blaster_tv_power` | кнопка | Комната | все |
| пульт: питание домашнего кинотеатра | `button.living_room_ir_blaster_ht_power` | кнопка | Комната | все |
| пульт: открыть шкаф | `button.living_room_ir_blaster_cupboard_open` | кнопка | Комната | хозяин |
| пульт: Vanya Box | `button.living_room_ir_blaster_vanya_box` | кнопка | Комната | хозяин |
| лисёнок: хватит радоваться | `button.komnata_lisenok_khvatit_radovatsia` | кнопка | Комната | хозяин |
<!-- якорь-словаря: новые строки — над этой строкой, сама строка остаётся последней -->

## Устройства нет в словаре

Только для хозяина. Найди через `ha_list_entities` (по `domain` и `area`), выполни просьбу, ответь как обычно и одной фразой спроси, запомнить ли название. Сказал «да» — допиши строку по разделу «Когда поправили».

## Когда поправили

Повод: хозяин поправил («не тот», «я про другой», «торшер — это вон тот»); ты спросила и получила ответ; хозяин попросил новое устройство и подтвердил, что запомнить.

Сделай два патча. В каждом `old_string` — строка-якорь целиком (последняя строка под таблицей, HTML-комментарий), `new_string` — новая строка таблицы, перевод строки и та же строка-якорь.

1. Словарь: `skill_manage(action="patch", name="home-control", old_string=…, new_string=…)`. Новая строка — `| как назвал хозяин | entity_id | действие | комната | кто |`.
2. Журнал: то же с `file_path="references/lessons.md"` и якорем журнала. Новая строка — `| дата | что сказали | что сделала | что имели в виду | что дописала в словарь |`.

Правила:

- Строки только добавляются. Старую строку не правь и не удаляй, даже ошибочную: новая ниже главнее.
- «кто» у новой строки — `хозяин`, пока хозяин не сказал, что можно всем.
- Комната — зона из Home Assistant; не знаешь — `не задана`.
- Только `patch`. `create`, `write_file`, `delete`, `remove_file` для этого навыка не вызывай никогда: файл целиком не переписывается.
- По словам гостя словарь и журнал не меняй.
- Токены, пароли и адреса в навык не пиши.

## Чего не трогать

Никому и никогда:

- камеры и приватность;
- автоматизации (`automation.*`);
- `input_boolean.mara_track`;
- служебные переключатели Frigate;
- блокировки от детей.
````

- [ ] **Step 2: Написать `references/lessons.md`**

Файл `~/.cache/mara-ha/home-control/references/lessons.md`, целиком:

```markdown
# Журнал уроков по дому

Записывать только случившееся: что сказали, что сделала, что имели в виду, что дописала в словарь. Одна поправка — одна строка. Строки только добавляются.

| дата | что сказали | что сделала | что имели в виду | что дописала в словарь |
|---|---|---|---|---|
<!-- якорь-журнала: новые строки — над этой строкой, сама строка остаётся последней -->
```

- [ ] **Step 3: Самопроверка засева**

Run:
```bash
cd ~/.cache/mara-ha
awk -F'|' '/^## Словарь/{f=1} /^<!-- якорь-словаря/{f=0} f && $3 ~ /\./ {gsub(/[` ]/,"",$3); print $3}' home-control/SKILL.md > ids.txt
wc -l < ids.txt; sort ids.txt | uniq -d
xargs ./ha.py st < ids.txt | awk '{print $2}' | sort | uniq -c
grep -c '^<!-- якорь-словаря' home-control/SKILL.md; grep -c '^<!-- якорь-журнала' home-control/references/lessons.md
```
Expected: `28`; пустая строка дублей; сводка состояний без `unavailable` и без падения 404 (значит, все 28 `entity_id` существуют); `1` и `1`.

- [ ] **Step 4: Положить на мак**

Run:
```bash
ssh mac-mini 'test ! -e ~/.hermes/skills/mara/home-control && echo free'
scp -rp ~/.cache/mara-ha/home-control mac-mini:.hermes/skills/mara/
( cd ~/.cache/mara-ha/home-control && md5sum SKILL.md references/lessons.md | awk '{print $1}' )
ssh mac-mini 'cd ~/.hermes/skills/mara/home-control && md5 -q SKILL.md references/lessons.md'
```
Expected: `free`; две пары md5 совпадают построчно. Нет `free` — стоп: каталог уже существует, смотреть, чей он.

- [ ] **Step 5: Hermes видит навык**

Run: `ssh mac-mini '~/.hermes/hermes-agent/venv/bin/hermes skills list' | grep home-control`
Expected: `│ home-control │ mara │ local │ local │ enabled │` (с выравниванием пробелами).

Откат задачи: `ssh mac-mini 'mv ~/.hermes/skills/mara/home-control ~/.hermes/backups/home-control-removed-20261114'`.

- [ ] **Step 6: HANDOFF и коммит**

В `HANDOFF.md`: «Task 2 готов: навык на маке, 28 строк словаря, md5 SKILL.md = …, lessons.md = …; эталон — `~/.cache/mara-ha/home-control/` на Pi».

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара — навык home-control лежит на маке, словарь сверен с HA" && git push
```

---

### Task 3: Токен в `.env` Hermes и тихая проверка рычагов

**Files:**
- Modify (mac): `~/.hermes/.env` — две строки в конец
- Create (mac): `~/.hermes/.env.bak-20261114`
- Create (Pi): `~/.cache/mara-ha/ask.sh`
- Delete (Pi): `~/.cache/mara-ha/hass_token`, `~/.cache/mara-ha/hdr`

**Interfaces:**
- Consumes: `~/.cache/mara-ha/hass_token`, `ha.py st|set`, навык из Task 2.
- Produces: `HASS_URL` и `HASS_TOKEN` в `.env`; `~/.cache/mara-ha/ask.sh "фраза"` → печатает итоговый ответ Мары (свежий процесс CLI, шлюз не трогает).

- [ ] **Step 1: Копия и md5 «до»**

Run:
```bash
ssh mac-mini 'cd ~/.hermes && test ! -e .env.bak-20261114 && cp -p .env .env.bak-20261114 && md5 -q .env .env.bak-20261114 && grep -c "^HASS_" .env; tail -c1 .env | xxd -p; stat -f %Sp .env'
```
Expected: два одинаковых md5 (записать как `ENV_MD5_BEFORE`), `0`, `0a`, `-rw-------`.

- [ ] **Step 2: Дописать две строки**

Run:
```bash
{ printf 'HASS_URL=http://192.168.1.51:8123\n'; printf 'HASS_TOKEN=%s\n' "$(cat ~/.cache/mara-ha/hass_token)"; } | ssh mac-mini 'cat >> ~/.hermes/.env'
```

- [ ] **Step 3: Проверить правку и токен с мака**

Run:
```bash
ssh mac-mini 'cd ~/.hermes && grep -c "^HASS_" .env && stat -f %Sp .env && md5 -q .env && diff .env.bak-20261114 .env | sed "s/HASS_TOKEN=.*/HASS_TOKEN=***/"; T=$(grep "^HASS_TOKEN=" .env | cut -d= -f2-); printf "Authorization: Bearer %s\n" "$T" | curl -s -o /dev/null -w "%{http_code}\n" -H @- http://192.168.1.51:8123/api/'
```
Expected: `2`; `-rw-------`; md5, отличный от `ENV_MD5_BEFORE`; diff ровно из трёх строк — `NNNa…`, `> HASS_URL=http://192.168.1.51:8123`, `> HASS_TOKEN=***`; затем `200`.

Откат правки: `ssh mac-mini "sed -i '' '/^HASS_URL=/d;/^HASS_TOKEN=/d' ~/.hermes/.env"` (не копированием `.bak`: файл могла тем временем изменить другая сессия).

- [ ] **Step 4: Убрать копию токена с Pi**

Run: `rm -f ~/.cache/mara-ha/hass_token ~/.cache/mara-ha/hdr && ls ~/.cache/mara-ha/`
Expected: в списке нет `hass_token` и `hdr`.

- [ ] **Step 5: Помощник `ask.sh`**

```bash
cat > ~/.cache/mara-ha/ask.sh <<'EOF'
#!/bin/bash
# ask.sh "фраза" — один тихий ход Мары из консоли мака: свежий процесс, шлюз не трогает.
printf '%s' "$1" | ssh mac-mini '~/.hermes/hermes-agent/venv/bin/hermes chat -Q --source tool --max-turns 8 --query-file -'
EOF
chmod +x ~/.cache/mara-ha/ask.sh
```

- [ ] **Step 6: Предупредить владельца и снять исходное состояние**

Написать владельцу в чат: «Сейчас минут пять будут мигать ночник-лисёнок и подсветка компа: проверяю рычаги Мары из консоли, вслух она говорить не будет».

Run: `~/.cache/mara-ha/ha.py st light.lisenok_nochnik light.podsvetka_kompa light.piratskaya_lyustra light.kuhnya_lyustra_1 light.prihozhaya | tee ~/.cache/mara-ha/before.txt`
Expected: пять строк. Если ночник `on` — `~/.cache/mara-ha/ha.py set light.lisenok_nochnik off`. Если подсветка компа `off` — `~/.cache/mara-ha/ha.py set light.podsvetka_kompa on`.

- [ ] **Step 7: Одна просьба — рычаг и «Ок»**

Run:
```bash
~/.cache/mara-ha/ask.sh 'Проверка рычагов из консоли. Вслух ничего не говори, say.py не зови. Включи ночник лисёнка.'
~/.cache/mara-ha/ha.py st light.lisenok_nochnik
```
Expected: ответ `Ок` (без пересказа) и строка сведений о сессии; `light.lisenok_nochnik on` с `last_changed` в пределах последней минуты.

- [ ] **Step 8: Две просьбы в одной фразе (Review Focus 2)**

Run:
```bash
~/.cache/mara-ha/ask.sh 'Проверка рычагов из консоли. Вслух ничего не говори, say.py не зови. Выключи ночник лисёнка и подсветку компа.'
~/.cache/mara-ha/ha.py st light.lisenok_nochnik light.podsvetka_kompa
```
Expected: один ответ `Ок`; обе сущности `off`.

- [ ] **Step 9: Двусмысленное имя (Review Focus 1)**

Run:
```bash
~/.cache/mara-ha/ask.sh 'Проверка рычагов из консоли. Вслух ничего не говори, say.py не зови. Включи люстру.'
~/.cache/mara-ha/ha.py st light.piratskaya_lyustra light.kuhnya_lyustra_1; grep -E 'piratskaya_lyustra |kuhnya_lyustra_1 ' ~/.cache/mara-ha/before.txt
```
Expected: один короткий вопрос, в котором названы обе люстры; `last_changed` обеих совпадает с `before.txt`.

- [ ] **Step 10: Лампа так не умеет (Review Focus 3)**

Run:
```bash
~/.cache/mara-ha/ask.sh 'Проверка рычагов из консоли. Вслух ничего не говори, say.py не зови. Приглуши свет в прихожей.'
~/.cache/mara-ha/ha.py st light.prihozhaya; grep 'prihozhaya ' ~/.cache/mara-ha/before.txt
```
Expected: одна короткая фраза о том, что эта лампа только включается и выключается; `last_changed` совпадает с `before.txt`.

- [ ] **Step 11: Вернуть как было**

Run: по `before.txt` — `~/.cache/mara-ha/ha.py set light.podsvetka_kompa on` (или `off`), `~/.cache/mara-ha/ha.py set light.lisenok_nochnik off` (или `on`); затем `~/.cache/mara-ha/ha.py st light.lisenok_nochnik light.podsvetka_kompa`.
Expected: состояния те же, что в `before.txt`.

Если шаг 7–10 не прошёл из-за формулировки навыка: поправить эталон на Pi, повторить Task 2 шаги 3–4 с `scp` поверх (до Task 6 файл ещё мой), перезапустить упавший шаг. Если Мара не видит рычагов `ha_*` — проверить шаг 3 (две строки и `200`).

- [ ] **Step 12: HANDOFF и коммит**

В `HANDOFF.md`: «Task 3 готов: `.env` +2 строки, md5 до/после = …/…, копия `.env.bak-20261114`; консольные проверки 7–10 пройдены; копия токена с Pi удалена».

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара — токен HA в .env Hermes, рычаги из консоли работают" && git push
```

---

### Task 4: Исключение в `SOUL.md`

**Files:**
- Modify (mac): `~/.hermes/SOUL.md` — две строки после строки 46
- Create (mac): `~/.hermes/SOUL.md.bak-20261114`

**Interfaces:**
- Consumes: `ask.sh`, `ha.py st|set`, навык и токен из Task 2–3.
- Produces: правило «просьба к дому с меткой маски → навык home-control» в душе Мары.

- [ ] **Step 1: Копия и md5 «до»**

Run:
```bash
ssh mac-mini 'cd ~/.hermes && test ! -e SOUL.md.bak-20261114 && cp -p SOUL.md SOUL.md.bak-20261114 && md5 -q SOUL.md SOUL.md.bak-20261114 && wc -c < SOUL.md && grep -c "home-control" SOUL.md'
```
Expected: два одинаковых md5 (записать как `SOUL_MD5_BEFORE`), размер в байтах (сейчас 47464), `0`.

- [ ] **Step 2: Вписать фразу**

Run:
```bash
ssh mac-mini '~/.hermes/hermes-agent/venv/bin/python -' <<'PY'
import pathlib
p = pathlib.Path.home() / ".hermes/SOUL.md"
s = p.read_bytes()
old = "  **остаётся**. Оба сразу, всегда.\n- Текст тут не вежливость".encode()
new = ("  **остаётся**. Оба сразу, всегда.\n"
       "  Исключение — просьба к дому (свет, телевизор, устройство): беру скил\n"
       "  home-control, дёргаю рычаг и отвечаю так, как велит скил.\n"
       "- Текст тут не вежливость").encode()
assert s.count(old) == 1, "якорь найден %d раз — стоп" % s.count(old)
assert b"home-control" not in s, "исключение уже вписано — стоп"
p.write_bytes(s.replace(old, new))
print("ok, +%d байт" % (len(new) - len(old)))
PY
```
Expected: `ok, +220 байт`. Любой `AssertionError` — стоп, файл не тронут.

- [ ] **Step 3: Проверить правку**

Run: `ssh mac-mini 'cd ~/.hermes && diff SOUL.md.bak-20261114 SOUL.md; md5 -q SOUL.md; wc -c < SOUL.md'`
Expected:
```
46a47,48
>   Исключение — просьба к дому (свет, телевизор, устройство): беру скил
>   home-control, дёргаю рычаг и отвечаю так, как велит скил.
```
md5 отличается от `SOUL_MD5_BEFORE` (записать как `SOUL_MD5_AFTER`); размер вырос на 220 байт.

Откат: если текущий md5 равен `SOUL_MD5_AFTER` — `ssh mac-mini 'cd ~/.hermes && cp -p SOUL.md.bak-20261114 SOUL.md'`; если нет (файл успел изменить кто-то ещё) — тот же скрипт шага 2 с переставленными `old` и `new`.

- [ ] **Step 4: Предупредить владельца и снять исходное состояние**

Написать владельцу: «Ещё три минуты: ночник и свет в коридоре, проверяю правило для голоса из консоли, вслух Мара не говорит».

Run: `~/.cache/mara-ha/ha.py st light.lisenok_nochnik light.svet_v_koridore button.living_room_ir_blaster_cupboard_open light.torsher light.piratskaya_lyustra | tee ~/.cache/mara-ha/before4.txt`
Expected: пять строк. Если ночник `on` — `ha.py set light.lisenok_nochnik off`; если коридор `on` — `ha.py set light.svet_v_koridore off`.

- [ ] **Step 5: Хозяин голосом, устройство в комнате маски → тишина**

Run:
```bash
~/.cache/mara-ha/ask.sh '[маска, вижу boss; сейчас 15:00 по Москве, date не нужен; ответь текстом — озвучу сама] Мара, включи ночник'
~/.cache/mara-ha/ha.py st light.lisenok_nochnik
```
Expected: ответ — ровно `NO_REPLY` (в консоли маркер виден как есть; в шлюзе он проглатывается); ночник `on`.

- [ ] **Step 6: Гость просит «хозяйскую» строку → отказ (проверка 6 спека)**

Run:
```bash
~/.cache/mara-ha/ask.sh '[маска, никого не вижу; сейчас 15:01 по Москве, date не нужен; ответь текстом — озвучу сама] Мара, открой шкаф'
~/.cache/mara-ha/ha.py st button.living_room_ir_blaster_cupboard_open; grep cupboard ~/.cache/mara-ha/before4.txt
```
Expected: одна короткая фраза отказа; состояние кнопки (время последнего нажатия) то же, что в `before4.txt`.

- [ ] **Step 7: Гость просит строку «все» в другой комнате → «Ок»**

Run:
```bash
~/.cache/mara-ha/ask.sh '[маска, вижу guest-1; сейчас 15:02 по Москве, date не нужен; ответь текстом — озвучу сама] Мара, включи свет в коридоре'
~/.cache/mara-ha/ha.py st light.svet_v_koridore
```
Expected: ответ `Ок`; `light.svet_v_koridore on`.

- [ ] **Step 8: Обычный разговор с меткой маски (Review Focus 4)**

Run:
```bash
~/.cache/mara-ha/ask.sh '[маска, вижу boss; сейчас 15:03 по Москве, date не нужен; ответь текстом — озвучу сама] Мара, как настроение?'
~/.cache/mara-ha/ha.py st light.torsher light.piratskaya_lyustra; grep -E 'torsher |piratskaya_lyustra ' ~/.cache/mara-ha/before4.txt
```
Expected: обычная реплика Мары — не `Ок` и не `NO_REPLY`; `last_changed` торшера и люстры совпадает с `before4.txt`.

- [ ] **Step 9: Вернуть как было**

Run: `~/.cache/mara-ha/ha.py set light.lisenok_nochnik off; ~/.cache/mara-ha/ha.py set light.svet_v_koridore off` (или `on` — по `before4.txt`); `~/.cache/mara-ha/ha.py st light.lisenok_nochnik light.svet_v_koridore`.
Expected: состояния те же, что в `before4.txt`.

- [ ] **Step 10: HANDOFF и коммит**

В `HANDOFF.md`: «Task 4 готов: `SOUL.md` +2 строки после 46-й, md5 до/после = …/…, копия `SOUL.md.bak-20261114`; консольные проверки 5–8 пройдены, проверка 6 спека закрыта».

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара — исключение для дома в SOUL.md, гость и хозяин различаются" && git push
```

---

### Task 5: Ежедневная копия навыка

**Files:**
- Modify (mac): crontab пользователя `serg` — вторая строка
- Create (mac): `~/.hermes/backups/crontab-20261114.bak`, `~/.hermes/backups/home-control-N/` (N — день недели, 1–7)

**Interfaces:**
- Consumes: каталог навыка из Task 2.
- Produces: семь скользящих копий `~/.hermes/backups/home-control-1…7`.

- [ ] **Step 1: Копия crontab**

Run: `ssh mac-mini 'crontab -l > ~/.hermes/backups/crontab-20261114.bak && wc -l < ~/.hermes/backups/crontab-20261114.bak && grep -c home-control ~/.hermes/backups/crontab-20261114.bak'`
Expected: `1` и `0`.

- [ ] **Step 2: Добавить строку**

Run:
```bash
ssh mac-mini 'bash -s' <<'SH'
set -e
line='45 4 * * * mkdir -p ~/.hermes/backups && rsync -a --delete ~/.hermes/skills/mara/home-control/ ~/.hermes/backups/home-control-$(date +\%u)/'
(crontab -l; printf '%s\n' "$line") | crontab -
crontab -l | wc -l
crontab -l | tail -1
SH
```
Expected: `2`; последняя строка равна `line` буква в букву (с `\%u`).

- [ ] **Step 3: Прогнать команду руками и сверить**

Run:
```bash
ssh mac-mini 'mkdir -p ~/.hermes/backups && rsync -a --delete ~/.hermes/skills/mara/home-control/ ~/.hermes/backups/home-control-$(date +%u)/ && diff -r ~/.hermes/skills/mara/home-control ~/.hermes/backups/home-control-$(date +%u) && echo same'
```
Expected: `same`. Сам запуск cron в 04:45 проверяется на следующий день (пункт в todo, Task 7).

Откат: `ssh mac-mini 'crontab ~/.hermes/backups/crontab-20261114.bak && crontab -l | wc -l'` → `1`.

- [ ] **Step 4: HANDOFF и коммит**

В `HANDOFF.md`: «Task 5 готов: crontab мака — 2 строки, копия `crontab-20261114.bak`, ручной прогон дал `same`».

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара — ежедневная копия навыка в crontab мака" && git push
```

---

### Task 6: Живые проверки с владельцем

Участие владельца — около 15 минут: одно сообщение в Telegram, три фразы вслух, одна поправка.

**Files:**
- Modify (mac, делает сама Мара): `~/.hermes/skills/mara/home-control/SKILL.md`, `references/lessons.md` — по одной новой строке
- Modify (repo): `HANDOFF.md`

**Interfaces:**
- Consumes: всё из Task 1–5; эталон навыка `~/.cache/mara-ha/home-control/` на Pi.
- Produces: результаты проверок 2–5 спека и число задержки в `HANDOFF.md`.

- [ ] **Step 1: Позвать владельца**

`AskUserQuestion`: «Рычаги готовы и проверены из консоли. Нужны 15 минут у маски и Telegram под рукой: торшер будет включаться и выключаться, телевизор лучше выключить на время замера. Начинаем?» Варианты: «Да, сейчас» / «Позже». «Позже» — записать в `HANDOFF.md`, остановиться.

- [ ] **Step 2: Проверка 2 — Telegram**

Run до: `~/.cache/mara-ha/ha.py st light.torsher`
Попросить владельца написать Маре в Telegram «включи торшер» (если он уже `on` — «выключи торшер») и переслать её ответ.
Run после: `~/.cache/mara-ha/ha.py st light.torsher`
Expected: состояние сменилось, `last_changed` свежий; ответ Мары — `Ок`.

- [ ] **Step 3: Только если шаг 2 не сработал — рестарт шлюза по отдельному «да»**

Признак: состояние не сменилось, Мара ответила, что не умеет, или ответила не по навыку. Причина — живая сессия держит старый промпт.

`AskUserQuestion`: «Мара в живой сессии не видит новый навык. Нужен рестарт шлюза Hermes: около минуты она не отвечает ни в Telegram, ни через маску. Рестартовать?» Варианты: «Да» / «Нет». «Нет» — записать в `HANDOFF.md` и остановиться.

После «Да»:
```bash
ssh mac-mini 'echo $(( $(date +%s) - $(stat -f %m ~/.hermes/logs/agent.log) )); launchctl list | grep ai.hermes.gateway'
```
Expected: первое число больше 120 (Мара не в середине хода; меньше — подождать и повторить); строка с PID шлюза.

```bash
ssh mac-mini 'launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway && sleep 20 && launchctl list | grep ai.hermes.gateway && tail -3 ~/.hermes/logs/gateway.error.log | cut -c1-160'
```
Expected: PID другой, чем до рестарта; в хвосте журнала нет `Traceback`. Затем повторить шаг 2.

- [ ] **Step 4: Проверка 3 — голосом, тишина в ответ**

Run до: `~/.cache/mara-ha/ha.py st light.torsher`
Попросить владельца сказать у маски: «Мара, выключи торшер» (или «включи» — обратное текущему) и сообщить, сказала ли маска что-нибудь и пришло ли что-то в Telegram.
Run после: `~/.cache/mara-ha/ha.py st light.torsher`
Expected: состояние сменилось; маска молчит; в Telegram пусто. Если маска не расслышала имя (известный риск слуха, вне задачи) — до трёх попыток; не вышло — записать в `HANDOFF.md` «голос упёрся в слух», проверки 3–4 отложить, идти к шагу 6 через Telegram.

- [ ] **Step 5: Проверка 4 — замер задержки**

Телевизор выключен, в комнате тихо. Сразу после шага 4:
```bash
ssh mac-mini 'ls ~/mask/heard | grep "\.wav$" | sort | tail -6'
~/.cache/mara-ha/ha.py st light.torsher
```
Имя файла — время конца фразы по Москве (`ГГГГММДД_ЧЧММСС_мсек.wav`). Взять последнее имя раньше `last_changed` торшера и посчитать:
```bash
python3 - "20261114_151203_417" "2026-11-14T12:12:14.250000+00:00" <<'PY'
import sys
from datetime import datetime, timedelta, timezone
t0 = datetime.strptime(sys.argv[1], "%Y%m%d_%H%M%S_%f").replace(tzinfo=timezone(timedelta(hours=3)))
print("задержка, с:", round((datetime.fromisoformat(sys.argv[2]) - t0).total_seconds(), 1))
PY
```
(два аргумента в примере — образец формата; подставить имя файла без `.wav` и `last_changed` из вывода выше). Повторить шаги 4–5 три раза. В `HANDOFF.md` — три числа. Expected: каждое в пределах 6–25 с; больше 25 — записать и отметить для разговора о быстром пути.

- [ ] **Step 6: Проверка 5 — поправка остаётся в словаре (Review Focus 5)**

Попросить владельца написать Маре (или сказать): «включи пиратов». Ожидаемо она спросит, что это; владелец отвечает: «пиратскую люстру, запомни».
Run:
```bash
~/.cache/mara-ha/ha.py st light.piratskaya_lyustra
ssh mac-mini 'cd ~/.hermes/skills/mara/home-control && grep -c "^<!-- якорь-словаря" SKILL.md && grep -c "^<!-- якорь-журнала" references/lessons.md && tail -1 SKILL.md | cut -c1-20'
diff <(ssh mac-mini 'cat ~/.hermes/skills/mara/home-control/SKILL.md') ~/.cache/mara-ha/home-control/SKILL.md
diff <(ssh mac-mini 'cat ~/.hermes/skills/mara/home-control/references/lessons.md') ~/.cache/mara-ha/home-control/references/lessons.md
```
Expected: люстра сменила состояние; `1`, `1`; оба `diff` показывают только строки с `<` (добавленные на маке), каждая начинается с `< | `, строк с `>` нет — ничего не потеряно; новая строка словаря содержит «пират» и `light.piratskaya_lyustra`.

Затем владелец повторяет «выключи пиратов».
Expected: люстра сменила состояние без встречного вопроса, ответ `Ок` (в Telegram) или тишина (голосом).

Если Мара переписала файл целиком или потеряла строки: восстановить из эталона (`scp -p ~/.cache/mara-ha/home-control/SKILL.md mac-mini:.hermes/skills/mara/home-control/SKILL.md`), записать случай в `HANDOFF.md` и ужесточить раздел «Когда поправили» одной фразой по факту сбоя.

- [ ] **Step 7: Алиса работает как раньше**

Run до: `~/.cache/mara-ha/ha.py st light.torsher`
Попросить владельца сказать станции: «Алиса, выключи торшер» (или «включи» — обратное текущему).
Run после: `~/.cache/mara-ha/ha.py st light.torsher`
Expected: состояние сменилось, Алиса ответила как обычно.

- [ ] **Step 8: Спросить про быстрый путь**

Показать владельцу три числа задержки (подставить в вопрос наименьшее и наибольшее). `AskUserQuestion`: «Задержка голосом от … до … с. Быстрый путь без модели (словарь прямо в ухе маски) делать отдельной задачей?» Варианты: «Нет, хватает» / «Да, отдельной задачей». Ответ — в `HANDOFF.md`.

- [ ] **Step 9: HANDOFF и коммит**

В `HANDOFF.md`: итоги проверок 2–5, Алиса, был ли рестарт, три числа задержки, решение о быстром пути.

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара управляет домом — живые проверки пройдены, задержка замерена" && git push
```

---

### Task 7: Закрытие

**Files:**
- Modify (repo): `HANDOFF.md`
- Modify (память): `~/.claude/projects/-home-hleserg-ha-mqtt-sensor-hub/memory/light-control-via-mara.md`, строка в `MEMORY.md`

**Interfaces:**
- Consumes: `ha.py todo`, итоги Task 6.
- Produces: закрытый бюджет `bet-6af7d04b-4452`.

- [ ] **Step 1: Недоделки в список дома**

Run:
```bash
~/.cache/mara-ha/ha.py todo 'Мара/дом: 15.11 проверить, что cron мака в 04:45 сделал ~/.hermes/backups/home-control-N (diff -r с навыком пуст)'
```
Плюс по одному вызову на каждое, что осталось из Task 6: «голос упёрся в слух — повторить проверки 3–4», «быстрый путь без модели — отдельной задачей». Expected: команды завершились без ошибки; пункты видны в `todo.vsiakii_umnyi_dom`.

- [ ] **Step 2: Обновить память**

Прочитать `light-control-via-mara.md` и заменить устаревшее содержание («ждёт, пока ассистент научится говорить и слышать») фактом: с 14.11.2026 Мара управляет светом и ТВ через навык `mara/home-control` на маке; словарь растёт её же патчами; учётка HA «Мара» не админ; Алиса работает параллельно. Строку в `MEMORY.md` — под новое содержание.

- [ ] **Step 3: HANDOFF, коммит, бюджет**

В `HANDOFF.md`: заголовок задачи — «сделано», что работает, как откатить каждую из пяти правок (команды из Task 1–5), что ушло в todo.

```bash
git add HANDOFF.md && git commit -m "HANDOFF: Мара управляет домом — сделано, откаты и недоделки записаны" && git push
python3 ~/.claude/hooks/task-budget.py end
```
Expected: итог бюджета; показать владельцу. Вышли за диапазон — `python3 ~/.claude/hooks/task-budget.py why bet-6af7d04b-4452 --codes …`.
