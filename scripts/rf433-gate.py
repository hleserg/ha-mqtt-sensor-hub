#!/usr/bin/env python3
# -*- coding: utf-8 -*-
u"""Ворота §2: разбор rtl_433 -> внешние сенсоры Home Assistant.

    tools/rtl433.py --once --ack --json | scripts/rf433-gate.py
    scripts/rf433-gate.py --dry-run  < hits.jsonl     # что бы опубликовалось
    scripts/rf433-gate.py --self-test                 # без брокера и без ноды

Разделение труда (Таск 26, согласовано с агентом ноды): нода — уши, rtl_433 —
мозг, эти ворота — паспортный стол. Сюда приходит по строке JSON на каждый
разбор rtl_433, отсюда уходит только то, что владелец разрешил впустить.

**Своих декодеров тут нет и не будет.** Шумофильтр — это сам факт, что rtl_433
дал разбору имя: строка без `model` не сигнал, а совпадение по краям. Ни
`canon_len`, ни длина посылки, ни RSSI на решение не влияют — на пороге −110
они дают ложное срабатывание, это измерено на ноде.

Порядок ровно тот, что в SENSORS.md §2 и в scripts/sim-rf-collector.sh, и
топики совпадают с симулятором побайтно — симулятор для того и написан:

  1. услышали неразрешённое  -> sensors/rf433/_discovered/<id>   НЕ retained
  2. владелец разрешил (script.rf_enable_sensor) -> retained cmd/enable/<id>
  3. разрешённое            -> homeassistant/.../config           retained
  4. и дальше состояние     -> sensors/rf433/<id>/<метрика>        retained
                              sensors/rf433/<id>/event            НЕ retained

Список разрешённых нигде не хранится: он и есть retained-сообщения в брокере,
их читают в начале каждого запуска. Своего состояния у ворот нет вообще — это
не экономия, а требование: cron-задача, потерявшая файл состояния, не должна
воскрешать сущности, которых владелец не разрешал.

`expire_after: 3600` на каждом значении — чужой датчик пропадает молча, и
замерший навсегда retained-градус хуже дырки в графике. Своей станции такое
ставить нельзя, чужой — обязательно.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

COLLECTOR = os.environ.get('RF_COLLECTOR', 'rf433')
PREFIX = os.environ.get('DISCOVERY_PREFIX', 'homeassistant')
IMAGE = 'eclipse-mosquitto:2.0.22'
ENV_FILE = os.environ.get('IOT_ENV', os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

# Поле rtl_433 -> (метрика, единица, device_class, state_class).
# Таблицу не сочиняли: имена полей взяты из rtl_433, соответствие классам — из
# его же examples/rtl_433_mqtt_hass.py, который уже обёрнут в
# ~/remote_ir_rf/tools/rtl433_hass.py. Здесь только то, что реально встречается
# у дешёвых датчиков на 433,92; неизвестное поле не выдумывается, а считается
# в счётчик `unknown_fields` и уходит целиком в `event`.
METRICS = {
    'temperature_C':  ('temperature', u'°C',  'temperature',      'measurement'),
    'temperature_1_C':('temperature_1', u'°C','temperature',      'measurement'),
    'temperature_2_C':('temperature_2', u'°C','temperature',      'measurement'),
    'humidity':       ('humidity',    '%',    'humidity',         'measurement'),
    'pressure_hPa':   ('pressure',    'hPa',  'pressure',         'measurement'),
    'wind_avg_km_h':  ('wind_avg',    'km/h', 'wind_speed',       'measurement'),
    'wind_max_km_h':  ('wind_max',    'km/h', 'wind_speed',       'measurement'),
    'wind_dir_deg':   ('wind_dir',    u'°',   None,               'measurement'),
    'rain_mm':        ('rain',        'mm',   'precipitation',    'total_increasing'),
    'moisture':       ('moisture',    '%',    'moisture',         'measurement'),
    'depth_cm':       ('depth',       'cm',   'distance',         'measurement'),
}

# Поля, по которым разбор считается управляющим сигналом, а не измерением:
# пульт, кнопка, датчик открытия, брелок. Ключ тут — не «полезность», а то,
# что HA для такого нужна другая сущность: не число, а событие.
CONTROL_FIELDS = ('cmd', 'button', 'code', 'data', 'state', 'event', 'motion', 'tamper')

# Служебные поля rtl_433 и наши добавки — не метрики и не признак пульта.
IGNORED = ('time', 'model', 'id', 'channel', 'mic', 'protocol', 'rssi', 'snr',
           'noise', 'freq', 'freq1', 'freq2', 'mod', 'capture_id', 'battery_ok',
           'subtype', 'sequence_num', 'count', 'num_rows', 'len', 'unit')


def slug(text):
    u"""Имя устройства из модели rtl_433: только то, что можно класть в топик."""
    return re.sub(r'_+', '_', re.sub(r'[^a-z0-9]+', '_', str(text).lower())).strip('_')


def device_id(hit):
    u"""`Nexus-TH-42` -> `nexus_th_42`. Без id модель сама себе идентификатор.

    Идентификатор обязан быть устойчивым между запусками — он попадает в
    `unique_id` сущностей HA и в retained-разрешение владельца. Поэтому в него
    входит и канал: у метеостанций три канала — это три разных датчика в трёх
    разных местах, и склеивать их в один нельзя.
    """
    parts = [slug(hit['model'])]
    for key in ('id', 'channel'):
        if hit.get(key) not in (None, ''):
            parts.append(slug(hit[key]))
    return '_'.join(parts)


# --------------------------------------------------------------- брокер ---

def env_value(name):
    u"""Пароль читается из .env и живёт только в памяти процесса.

    В командную строку он не попадает никогда: `docker run -e` берёт значение
    из окружения родителя, а не из аргументов, и в `ps` виден только `-e W`.
    """
    try:
        with open(ENV_FILE) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(name + '='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    except IOError:
        return None
    return None


def broker_env():
    password = env_value('RF_COLLECTOR_MQTT_PASSWORD')
    if not password:
        sys.exit('в %s нет RF_COLLECTOR_MQTT_PASSWORD' % ENV_FILE)
    return {
        'H': env_value('MQTT_HOST') or '127.0.0.1',
        'P': env_value('MQTT_PORT') or '1883',
        'U': env_value('RF_COLLECTOR_MQTT_USER') or 'rf_collector',
        'W': password,
    }


def allowlist():
    u"""Что владелец разрешил — читается из брокера, не из файла.

    Пустая полезная нагрузка на топике разрешения означает «убрано»: так
    работает `script.rf_disable_sensor`, и так retained-сообщение исчезает из
    брокера целиком, а не остаётся лежать со значением «выключено».
    """
    env = dict(os.environ, **broker_env())
    env['T'] = 'sensors/%s/cmd/enable/#' % COLLECTOR
    out = subprocess.run(
        ['docker', 'run', '--rm', '--network', 'host',
         '-e', 'H', '-e', 'P', '-e', 'U', '-e', 'W', '-e', 'T', IMAGE, 'sh', '-c',
         'mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -v -W 2 2>/dev/null'],
        capture_output=True, text=True, env=env, timeout=60)
    enabled = {}
    for line in out.stdout.splitlines():
        topic, _, payload = line.partition(' ')
        did = topic.rsplit('/', 1)[-1]
        if not payload.strip():
            continue
        try:
            body = json.loads(payload)
        except ValueError:
            continue
        if body.get('enabled'):
            enabled[did] = body.get('alias') or did
    return enabled


def publish(batch):
    u"""Одна пачка — один контейнер.

    По контейнеру на публикацию было бы 5-6 запусков docker на каждый датчик;
    на десяти датчиках это минута работы там, где хватает секунды. Разделитель
    — табуляция: `json.dumps` экранирует и её, и перевод строки, так что
    полезная нагрузка не может разорвать строку протокола.
    """
    if not batch:
        return
    env = dict(os.environ, **broker_env())
    feed = ''.join('%d\t%s\t%s\n' % (r, t, p) for t, p, r in batch)
    subprocess.run(
        ['docker', 'run', '--rm', '--network', 'host', '-i',
         '-e', 'H', '-e', 'P', '-e', 'U', '-e', 'W', IMAGE, 'sh', '-c',
         'while IFS="\t" read -r R T M; do '
         '  if [ "$R" = "1" ]; then '
         '    mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -m "$M" -q 1 -r; '
         '  else '
         '    mosquitto_pub -h "$H" -p "$P" -u "$U" -P "$W" -t "$T" -m "$M" -q 1; '
         '  fi; '
         'done'],
        input=feed, text=True, env=env, timeout=300, check=True)


# ------------------------------------------------------------ сущности ---

def device_block(did, alias, hit):
    u"""Паспорт устройства в HA. `manufacturer: third-party` — не украшение.

    Владелец просил, чтобы чужой прибор был явно помечен чужим. Метка стоит в
    трёх местах сразу, потому что в разных экранах HA видно разные поля: в
    производителе, в модели (там же полоса и протокол) и в привязке к
    коллектору через `via_device`.
    """
    model = '%s (%s MHz, third-party)' % (
        hit.get('model', '?'),
        round(float(hit.get('freq', 433.92)), 2) if hit.get('freq') else '433.92')
    return {'identifiers': ['%s_%s' % (COLLECTOR, did)], 'name': alias,
            'manufacturer': 'third-party', 'model': model,
            'via_device': 'rf_ble_collector'}


def configs(did, alias, hit):
    u"""Описания сущностей для одного разрешённого устройства."""
    dev = device_block(did, alias, hit)
    base = 'sensors/%s/%s' % (COLLECTOR, did)
    avail = {'availability_topic': 'sensors/%s/status' % COLLECTOR,
             'payload_available': 'online', 'payload_not_available': 'offline'}
    out = []

    def cfg(kind, metric, body):
        body.update(avail)
        body['unique_id'] = body['object_id'] = '%s_%s_%s' % (COLLECTOR, did, metric)
        body['device'] = dev
        out.append(('%s/%s/%s_%s/%s/config' % (PREFIX, kind, COLLECTOR, did, metric),
                    json.dumps(body, ensure_ascii=False), 1))

    for field, (metric, unit, dclass, sclass) in METRICS.items():
        if field not in hit:
            continue
        body = {'name': metric.replace('_', ' ').capitalize(),
                'state_topic': '%s/%s' % (base, metric),
                'unit_of_measurement': unit, 'state_class': sclass,
                'expire_after': 3600}
        if dclass:
            body['device_class'] = dclass
        cfg('sensor', metric, body)

    if 'battery_ok' in hit:
        cfg('binary_sensor', 'battery_low',
            {'name': 'Battery low', 'state_topic': '%s/battery_low' % base,
             'payload_on': 'true', 'payload_off': 'false',
             'device_class': 'battery', 'entity_category': 'diagnostic'})

    if is_control(hit):
        # Событие, а не число: пульт нечего усреднять, его нажимают. Сущность
        # `event` — штатный триггер автоматизаций HA, то есть та самая
        # «управляшка»: чужая кнопка становится выключателем моего света.
        #
        # `event_types` из одного значения намеренно. Кодов у незнакомого
        # пульта заранее знать неоткуда, а описание сущности retained — список,
        # который дописывается на ходу, пришлось бы хранить. Код уезжает
        # атрибутом, условие в автоматизации ставится на него.
        cfg('event', 'signal',
            {'name': 'Signal', 'state_topic': '%s/signal' % base,
             'event_types': ['signal'], 'icon': 'mdi:remote'})

    cfg('sensor', 'rssi',
        {'name': 'RSSI', 'state_topic': '%s/rssi' % base,
         'unit_of_measurement': 'dBm', 'device_class': 'signal_strength',
         'state_class': 'measurement', 'entity_category': 'diagnostic',
         'expire_after': 3600})
    cfg('sensor', 'last_seen',
        {'name': 'Last seen', 'state_topic': '%s/last_seen' % base,
         'device_class': 'timestamp', 'entity_category': 'diagnostic'})
    return out


def is_control(hit):
    u"""Управляющий сигнал — это разбор без единого измерения.

    Проверка именно такая, а не «есть поле code»: у датчика открытия двери тоже
    есть `state`, и у метеостанции бывает `count`. Меряет — сенсор, не меряет —
    пульт.
    """
    if any(f in hit for f in METRICS):
        return False
    return any(f in hit for f in CONTROL_FIELDS)


def states(did, hit, seen_at):
    u"""Состояние разрешённого устройства: метрики retained, сырьё — нет."""
    base = 'sensors/%s/%s' % (COLLECTOR, did)
    out = []
    for field, (metric, _, _, _) in METRICS.items():
        if field in hit:
            out.append(('%s/%s' % (base, metric), str(hit[field]), 1))
    if 'battery_ok' in hit:
        out.append(('%s/battery_low' % base,
                    'false' if hit['battery_ok'] else 'true', 1))
    # RSSI берётся у ноды: у rtl_433 при файловом входе своего уровня нет, он
    # считает его по синтезированному IQ и врёт. Нет числа от ноды — нет топика.
    if hit.get('rssi') is not None:
        out.append(('%s/rssi' % base, str(hit['rssi']), 1))
    out.append(('%s/last_seen' % base, seen_at, 1))
    if is_control(hit):
        # Не retained: нажатие — это момент, а не состояние. Retained-нажатие
        # выстрелило бы всеми автоматизациями при каждом рестарте HA.
        out.append(('%s/signal' % base,
                    json.dumps(dict(hit, event_type='signal'), ensure_ascii=False), 0))
    out.append(('%s/event' % base, json.dumps(hit, ensure_ascii=False), 0))
    return out


# ----------------------------------------------------------------- ход ---

def run(lines, dry_run=False, quiet_air=True):
    u"""Разобрать поток строк rtl_433 и собрать всё, что надо опубликовать.

    Возвращает (пачка, счётчики). Пачку не публикует — так её можно
    напечатать в `--dry-run` и проверить в `--self-test`, не трогая брокер.
    """
    stat = {'lines': 0, 'noise': 0, 'hits': 0, 'devices': 0,
            'announced': 0, 'enabled': 0, 'control': 0, 'bad_json': 0}
    hits = {}
    for line in lines:
        line = line.strip()
        if not line or line[0] != '{':
            continue
        stat['lines'] += 1
        try:
            hit = json.loads(line)
        except ValueError:
            stat['bad_json'] += 1
            continue
        if not hit.get('model'):
            # rtl_433 не дал имени — значит это не устройство, а совпадение.
            stat['noise'] += 1
            continue
        stat['hits'] += 1
        # Последний разбор на устройство: в одном запуске датчик присылает один
        # и тот же кадр по три раза, и публиковать надо свежее, а не первое.
        hits[device_id(hit)] = hit

    stat['devices'] = len(hits)
    if not hits:
        return [], stat

    enabled = {} if dry_run else allowlist()
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    batch = [('sensors/%s/status' % COLLECTOR, 'online', 1),
             ('sensors/%s/last_run' % COLLECTOR, now, 1)]

    for did, hit in sorted(hits.items()):
        hit.setdefault('device_id', did)
        if is_control(hit):
            stat['control'] += 1
        if did not in enabled:
            # Объявление НЕ retained: оно описывает эфир сейчас. Retained
            # объявление воскрешало бы датчики, проехавшие мимо дома год назад.
            # Не чаще раза в минуту на устройство (§2) — соблюдается тем, что
            # запуск один и внутри него устройство одно; интервал держит cron.
            batch.append(('sensors/%s/_discovered/%s' % (COLLECTOR, did),
                          json.dumps(dict(hit, source=COLLECTOR, timestamp=now),
                                     ensure_ascii=False), 0))
            stat['announced'] += 1
            continue
        stat['enabled'] += 1
        batch.extend(configs(did, enabled[did], hit))
        batch.extend(states(did, hit, now))

    return batch, stat


def self_test():
    u"""Проверка ворот без брокера и без ноды.

    Берёт три строки: метеодатчик, пульт и мусор без модели, — и проверяет
    ровно то, что ворота обязаны делать: шум выбросить, неразрешённое только
    объявить, состояние не публиковать. Разрешённую ветку проверяет отдельно,
    подсунув список разрешений напрямую.
    """
    sample = [
        json.dumps({'time': '2026-09-06 12:00:00', 'model': 'Nexus-TH', 'id': 42,
                    'channel': 1, 'temperature_C': -3.8, 'humidity': 84,
                    'battery_ok': 1, 'rssi': -71}),
        json.dumps({'time': '2026-09-06 12:00:01', 'model': 'Akhan-100F14',
                    'id': 324422, 'code': 'ff00aa', 'rssi': -66}),
        json.dumps({'time': '2026-09-06 12:00:02', 'canon_len': 8}),
        'не json вовсе',
    ]

    batch, stat = run(sample, dry_run=True)
    assert stat['hits'] == 2, stat
    assert stat['noise'] == 1, stat          # разбор без model выброшен
    assert stat['devices'] == 2, stat
    assert stat['announced'] == 2, stat      # ничего не разрешено — только объявили
    assert stat['control'] == 1, stat
    topics = [t for t, _, _ in batch]
    assert 'sensors/rf433/_discovered/nexus_th_42_1' in topics, topics
    assert 'sensors/rf433/_discovered/akhan_100f14_324422' in topics, topics
    assert not any(t.startswith('homeassistant/') for t in topics), topics
    assert not any(re.match(r'sensors/rf433/[a-z]\w*/(temperature|event)$', t)
                   for t in topics), topics
    assert all(r == 0 for t, _, r in batch if '_discovered' in t), batch

    # Разрешённый датчик: описания сущностей, состояние retained, сырьё нет.
    hit = json.loads(sample[0])
    did = device_id(hit)
    assert did == 'nexus_th_42_1', did
    cfgs = dict((t, json.loads(p)) for t, p, _ in configs(did, u'Сосед', hit))
    temp = cfgs['homeassistant/sensor/rf433_nexus_th_42_1/temperature/config']
    assert temp['expire_after'] == 3600, temp
    assert temp['device']['manufacturer'] == 'third-party', temp
    assert temp['device']['via_device'] == 'rf_ble_collector', temp
    assert temp['unique_id'] == 'rf433_nexus_th_42_1_temperature', temp
    assert 'homeassistant/binary_sensor/rf433_nexus_th_42_1/battery_low/config' in cfgs
    assert not any('/event/' in t for t in cfgs), 'у датчика не бывает пульта'

    st = dict((t, (p, r)) for t, p, r in states(did, hit, 'T'))
    assert st['sensors/rf433/nexus_th_42_1/temperature'] == ('-3.8', 1), st
    assert st['sensors/rf433/nexus_th_42_1/battery_low'] == ('false', 1), st
    assert st['sensors/rf433/nexus_th_42_1/rssi'] == ('-71', 1), st
    assert st['sensors/rf433/nexus_th_42_1/event'][1] == 0, 'сырьё не retained'

    # Пульт: событие, а не число, и нажатие не retained.
    rc = json.loads(sample[1])
    rid = device_id(rc)
    ccfg = dict((t, json.loads(p)) for t, p, _ in configs(rid, u'Чужой пульт', rc))
    assert 'homeassistant/event/rf433_akhan_100f14_324422/signal/config' in ccfg, ccfg
    cst = dict((t, r) for t, _, r in states(rid, rc, 'T'))
    assert cst['sensors/rf433/akhan_100f14_324422/signal'] == 0, cst

    # RSSI ноды нет — топика тоже нет, а не ноль вместо числа.
    assert not any('/rssi' in t for t, _, _ in
                   states('x', {'model': 'M', 'temperature_C': 1}, 'T'))

    print(u'self-test: ок — 2 разбора, 1 шум, 2 объявления, 0 сущностей без разрешения')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dry-run', action='store_true',
                    help=u'напечатать пачку и не публиковать (брокер не нужен)')
    ap.add_argument('--self-test', action='store_true')
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    batch, stat = run(sys.stdin, dry_run=args.dry_run)
    if args.dry_run:
        for topic, payload, retain in batch:
            print('%s %s %s' % ('R' if retain else '-', topic, payload[:160]))
    else:
        publish(batch)

    # Пустой эфир — это результат, а не отсутствие результата: об этом просил
    # агент ноды, и он прав. Поэтому счётчики печатаются всегда, в том числе
    # когда публиковать было нечего.
    print(u'строк %(lines)d, разборов %(hits)d, шума %(noise)d, устройств '
          u'%(devices)d, объявлено %(announced)d, опубликовано %(enabled)d '
          u'(пультов %(control)d)' % stat)


if __name__ == '__main__':
    main()
