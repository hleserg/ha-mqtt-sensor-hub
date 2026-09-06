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
import calendar
import json
import os
import re
import subprocess
import sys
import time

COLLECTOR = os.environ.get('RF_COLLECTOR', 'rf433')
# Сколько раз повторить ПОСЫЛКУ ЦЕЛИКОМ. Единица, и это важно: в `raw` уже
# лежит вся пачка повторов так, как её услышала нода, — пульт свои три-четыре
# повтора шлёт внутри одного кадра. `repeat: 3` отправит три пачки, а не три
# повтора, то есть девять-двенадцать нажатий подряд. Поправка агента ноды к
# моему первому предположению; ручка оставлена наружу на случай приёмника,
# которому одной пачки мало, но умолчание должно быть 1.
TX_REPEAT = int(os.environ.get('RF_TX_REPEAT', '1'))
# Площадка HA, в которую складываются все чужие приборы. Владелец просил
# отделить своё от чужого — это и есть отделение, причём такое, которое
# работает и в списке устройств, и в поиске, и в голосовом помощнике.
FOREIGN_AREA = os.environ.get('RF_FOREIGN_AREA', u'Чужие приборы')
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


def broker_state():
    u"""Всё, что ворота помнят, лежит в брокере — читается одним заходом.

    Три вещи: что владелец разрешил, накопленная статистика и библиотека кодов для
    отправки. Раньше запусков контейнера было бы три; подписка на три фильтра
    сразу стоит столько же, сколько на один, и все три среза гарантированно
    сняты в один момент времени, а не в три разных.
    """
    env = dict(os.environ, **broker_env())
    env['T1'] = 'sensors/%s/cmd/enable/#' % COLLECTOR
    env['T2'] = 'sensors/%s/+/usage' % COLLECTOR
    env['T3'] = 'sensors/%s/+/tx/+' % COLLECTOR
    out = subprocess.run(
        ['docker', 'run', '--rm', '--network', 'host',
         '-e', 'H', '-e', 'P', '-e', 'U', '-e', 'W',
         '-e', 'T1', '-e', 'T2', '-e', 'T3', IMAGE, 'sh', '-c',
         'mosquitto_sub -h "$H" -p "$P" -u "$U" -P "$W" '
         '  -t "$T1" -t "$T2" -t "$T3" -v -W 2 2>/dev/null'],
        capture_output=True, text=True, env=env, timeout=60)

    enabled, usage, txlib = {}, {}, {}
    for line in out.stdout.splitlines():
        topic, _, payload = line.partition(' ')
        parts = topic.split('/')
        if not payload.strip():
            # Пустая нагрузка — «убрано». Так работает script.rf_disable_sensor,
            # и так retained-сообщение исчезает из брокера целиком, а не лежит
            # со значением «выключено».
            continue
        try:
            body = json.loads(payload)
        except ValueError:
            continue
        if '/cmd/enable/' in topic:
            if body.get('enabled'):
                enabled[parts[-1]] = body.get('alias') or parts[-1]
        elif topic.endswith('/usage') and len(parts) == 4:
            usage[parts[2]] = body
        elif len(parts) == 5 and parts[3] == 'tx':
            txlib.setdefault(parts[2], {})[parts[4]] = body
    return enabled, usage, txlib


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


# ------------------------------------------------------- память о приборе ---
#
# Ворота по-прежнему не держат СВОЕГО состояния — ни файла, ни базы. Но
# владелец попросил показывать, как часто и когда чужой прибор работает, а это
# по одному кадру не посчитать. Память нужна, и она кладётся туда же, где уже
# лежит список разрешённых, — в retained-сообщения брокера:
#
#   sensors/rf433/<did>/usage      статистика, она же источник для сущностей HA
#   sensors/rf433/<did>/tx/<код>   что отправить, чтобы повторить это нажатие
#
# Это не отступление от прежнего правила, а его же формулировка точнее: у ворот
# нет состояния, СПОСОБНОГО СОЗДАТЬ СУЩНОСТЬ. Разрешение по-прежнему одно —
# retained cmd/enable, и потеря всей статистики не воскресит ни одного прибора,
# которого владелец не разрешал. Статистика копится только у разрешённых: иначе
# в брокере оседал бы мусор по каждому датчику, once проехавшему мимо дома.

USAGE_WINDOW = 7 * 24 * 3600


def usage_update(prev, hit, now, tx, presses=1, codes_seen=None):
    u"""Досчитать статистику прибора после очередного кадра.

    Всё, что HA потом показывает, считается здесь, а не шаблоном в HA. Причина
    прозаическая: шаблон в HA считается на КАЖДОМ обновлении любой сущности, а
    здесь — раз в десять минут, и «за сутки» посчитанное на часах брокера не
    разъедется с «за неделю», посчитанным на часах HA.
    """
    u = dict(prev or {})
    hours = list(u.get('hours') or [0] * 24)
    if len(hours) != 24:
        hours = [0] * 24
    seen = [t for t in (u.get('seen') or []) if isinstance(t, str)]
    seen.extend([now] * max(1, int(presses)))

    # Окно недели режется по времени, а не по количеству: сто нажатий за час и
    # сто за неделю — это разные приборы, и обрезание по длине их сравняло бы.
    edge = calendar.timegm(time.gmtime()) - USAGE_WINDOW
    seen = [t for t in seen if _ts(t) >= edge][-5000:]
    day = calendar.timegm(time.gmtime()) - 24 * 3600

    hours[local_hour(now)] += max(1, int(presses))
    codes = dict(u.get('codes') or {})
    for cs in (codes_seen or ([code_slug(hit)] if is_control(hit) else [])):
        codes[cs] = int(codes.get(cs, 0)) + 1

    rssi = hit.get('rssi')
    n = int(u.get('rssi_n') or 0)
    rsum = float(u.get('rssi_sum') or 0.0)
    if rssi is not None:
        n += 1
        rsum += float(rssi)
    rmax = u.get('rssi_max')
    if rssi is not None and (rmax is None or float(rssi) > float(rmax)):
        rmax = rssi

    total = int(u.get('total') or 0) + max(1, int(presses))
    busiest = max(range(24), key=lambda h: hours[h]) if any(hours) else None
    out = {
        'total': total,
        'n7': len(seen),
        'n24': len([t for t in seen if _ts(t) >= day]),
        'typical': ('%02d:00' % busiest) if busiest is not None else None,
        'first_seen': u.get('first_seen') or now,
        'last_seen': now,
        'hours': hours,
        'seen': seen,
        'codes': codes,
        'rssi_max': rmax,
        'rssi_avg': round(rsum / n, 1) if n else None,
        'rssi_n': n,
        'rssi_sum': round(rsum, 1),
        'distance': distance_label(rssi if rssi is not None else rmax),
        'rolling': is_rolling(hit),
        'sendable': bool(tx) and not is_rolling(hit),
    }
    return out


def _ts(iso):
    try:
        return calendar.timegm(time.strptime(iso, '%Y-%m-%dT%H:%M:%SZ'))
    except (ValueError, TypeError):
        return 0


def tx_body(hit):
    u"""Что послать ноде, чтобы повторить это нажатие. Ничего не пересобираем.

    `POST /rf` принимает готовые тайминги: у ноды нет кодировщика протоколов, и
    это правильно — восстанавливать посылку из разбора значит угадывать то, что
    и так было в эфире. Сырьё кладёт этап A полем `tx`; нет его — нет и кнопки.
    Частота обязательна у ноды намеренно: молча стрельнуть на 433 вместо 315 —
    это `ok: true` при закрытой двери, худший вид отказа.
    """
    tx = hit.get('tx')
    if not isinstance(tx, dict):
        return None
    raw = tx.get('raw')
    freq = tx.get('frequency')
    if not raw or not freq:
        return None
    body = {'frequency': int(freq), 'raw': list(raw)}
    for opt in ('repeat', 'wait_ms', 'alternating'):
        if tx.get(opt) is not None:
            body[opt] = tx[opt]
    body.setdefault('repeat', TX_REPEAT)
    return body


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
    # `suggested_area` — то самое «сгруппировать, чтобы отделить своё от
    # чужого». Площадка в HA делает это сама и сразу везде: на странице
    # устройств, в поиске, в голосовых помощниках, — и не требует ни одной
    # строки в дашборде на каждый новый прибор.
    return {'identifiers': ['%s_%s' % (COLLECTOR, did)], 'name': alias,
            'manufacturer': 'third-party', 'model': model,
            'via_device': 'rf_ble_collector',
            'suggested_area': FOREIGN_AREA}


def configs(did, alias, hit, codes=None):
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

    usage_configs(cfg, did)
    if codes:
        tx_configs(cfg, did, codes)
    return out


# Модели, которые повторить нельзя в принципе. Не «плохо работает», а
# невозможно: в брелке счётчик, каждое нажатие даёт новый код, и вчерашняя
# запись сегодня отвергается приёмником. Документы ноды говорят то же самое —
# «математика в брелке, не в эфире» (docs/api-rf.md). Кнопку для такого не
# заводим вовсе: кнопка, которая молча не работает, хуже её отсутствия.
ROLLING = ('keeloq', 'somfy', 'rts', 'rolling', 'hcs2', 'hcs3', 'microchip',
           'chamberlain_sec', 'nice_flor', 'came_atomo')


def is_rolling(hit):
    return any(r in slug(hit.get('model', '')) for r in ROLLING)


def local_hour(iso_utc, tz=None):
    u"""Час суток по местному времени. UTC везде в этом файле, кроме здесь.

    «Обычно приходит в 18 часов» — это про быт человека, а не про Гринвич, и в
    Москве расхождение три часа: вечерний гость лёг бы на день. Пояс берётся
    из .env (TZ=Europe/Moscow), а не из настроек машины, чтобы число не
    зависело от того, где запустили.
    """
    tz = tz or env_value('TZ') or 'UTC'
    old = os.environ.get('TZ')
    os.environ['TZ'] = tz
    time.tzset()
    try:
        utc = calendar.timegm(time.strptime(iso_utc, '%Y-%m-%dT%H:%M:%SZ'))
        return time.localtime(utc).tm_hour
    finally:
        if old is None:
            os.environ.pop('TZ', None)
        else:
            os.environ['TZ'] = old
        time.tzset()


def distance_label(rssi):
    u"""«Как далеко» честно посчитать нельзя, и метры тут были бы враньём.

    Уровень зависит от мощности передатчика, антенны, стен и погоды; один и тот
    же датчик за стеной и в прямой видимости даёт разброс в двадцать децибел.
    Поэтому отдаём уровень как есть и грубую словесную отметку, а не цифру в
    метрах, которой нельзя пользоваться.
    """
    if rssi is None:
        return None
    r = float(rssi)
    if r > -70:
        return u'рядом'
    if r > -85:
        return u'недалеко'
    if r > -100:
        return u'далеко'
    return u'на пределе слышимости'


def code_slug(hit):
    u"""Чем одно нажатие пульта отличается от другого.

    У брелка на четыре кнопки один `id` и четыре разных кода — это четыре
    разные кнопки в HA, а не одна. Ключ берём из первого поля, которое пульты
    реально заполняют.
    """
    for field in ('button', 'cmd', 'code', 'data', 'event'):
        if hit.get(field) not in (None, ''):
            return slug(hit[field])[:32] or 'signal'
    return 'signal'


def usage_configs(cfg, did):
    u"""Сущности «что мы про этот прибор знаем». Все — из одного топика.

    Один retained-топик `usage` служит и памятью ворот, и источником для семи
    сущностей: HA умеет брать состояние по `value_template` и атрибуты по
    `json_attributes_topic` с одного и того же топика. Семи отдельных топиков
    заводить не надо, а память и показ гарантированно не разъедутся — они
    физически одно сообщение.
    """
    base = 'sensors/%s/%s' % (COLLECTOR, did)
    u = {'state_topic': '%s/usage' % base}

    cfg('sensor', 'signals_total', dict(
        u, name=u'Сигналов всего', value_template='{{ value_json.total }}',
        state_class='total_increasing', icon='mdi:counter'))
    cfg('sensor', 'signals_7d', dict(
        u, name=u'Сигналов за неделю', value_template='{{ value_json.n7 }}',
        state_class='measurement', icon='mdi:calendar-week'))
    cfg('sensor', 'signals_24h', dict(
        u, name=u'Сигналов за сутки', value_template='{{ value_json.n24 }}',
        state_class='measurement', icon='mdi:calendar-today'))

    # Состояние — час, в который прибор работает чаще всего; атрибуты — вся
    # раскладка по 24 часам, чтобы владелец видел не только пик, но и форму:
    # «каждое утро в семь» и «когда придётся» дают один и тот же пик.
    # Атрибуты — белым списком, а не всем сообщением. В `usage` лежит `seen`:
    # до 5000 отметок времени, и без шаблона HA писал бы весь этот массив в
    # таблицу атрибутов базы истории при КАЖДОМ проходе, то есть раз в десять
    # минут. На разговорчивом приборе это десятки мегабайт за окно хранения
    # ради данных, которые нужны только воротам и только на шине.
    cfg('sensor', 'usage_profile', dict(
        u, name=u'Обычное время', value_template='{{ value_json.typical }}',
        json_attributes_topic='%s/usage' % base,
        json_attributes_template=(
            "{{ {'hours': value_json.hours, 'codes': value_json.codes,"
            " 'rssi_avg': value_json.rssi_avg, 'rolling': value_json.rolling,"
            " 'sendable': value_json.sendable,"
            " 'first_seen': value_json.first_seen} | tojson }}"),
        icon='mdi:clock-outline'))

    cfg('sensor', 'distance', dict(
        u, name=u'Насколько близко', value_template='{{ value_json.distance }}',
        icon='mdi:map-marker-distance', entity_category='diagnostic'))
    cfg('sensor', 'rssi_max', dict(
        u, name=u'Лучший уровень', value_template='{{ value_json.rssi_max }}',
        unit_of_measurement='dBm', device_class='signal_strength',
        state_class='measurement', entity_category='diagnostic'))
    cfg('sensor', 'first_seen', dict(
        u, name=u'Впервые услышан',
        value_template='{{ value_json.first_seen }}',
        device_class='timestamp', entity_category='diagnostic'))


def tx_configs(cfg, did, codes):
    u"""Кнопка «отправить» — по одной на каждое различимое нажатие.

    У брелка на четыре кнопки один идентификатор и четыре кода: это четыре
    кнопки в HA, а не одна. Тело запроса к ноде едет прямо в `payload_press`,
    поэтому отдельного демона не нужно — HA публикует его в `cmd/send/...`, а
    одна автоматизация перекладывает в `POST /rf`.

    `retain: false` обязателен. Retained-нажатие HA перечитывает при каждом
    запуске, то есть каждый рестарт стрелял бы в эфир всеми кнопками сразу.
    """
    for cs, body in sorted(codes.items()):
        cfg('button', 'send_%s' % cs, {
            # Код в имени всегда, даже когда он пока один. Иначе первая
            # кнопка регистрируется как «Отправить», а при появлении второй
            # переименовывается в «Отправить aa» — и id, сделанный HA из
            # первого имени, остаётся навсегда расходиться с именем. Замечено
            # на живом брокере: button.…_otpravit рядом с …_otpravit_bb.
            'name': u'Отправить %s' % cs,
            'command_topic': 'sensors/%s/cmd/send/%s/%s' % (COLLECTOR, did, cs),
            'payload_press': json.dumps(body, ensure_ascii=False),
            'retain': False, 'icon': 'mdi:remote',
        })


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

def run(lines, dry_run=False, state=None):
    u"""Разобрать поток строк rtl_433 и собрать всё, что надо опубликовать.

    Возвращает (пачка, счётчики). Пачку не публикует — так её можно
    напечатать в `--dry-run` и проверить в `--self-test`, не трогая брокер.
    """
    stat = {'lines': 0, 'noise': 0, 'hits': 0, 'devices': 0,
            'announced': 0, 'enabled': 0, 'control': 0, 'bad_json': 0,
            'rolling': 0, 'sendable': 0}
    hits, codes, caps = {}, {}, {}
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
        did = device_id(hit)
        # Последний разбор на устройство: в одном запуске датчик присылает один
        # и тот же кадр по три раза, и публиковать надо свежее, а не первое.
        hits[did] = hit
        # Но схлопывать по устройству ЦЕЛИКОМ нельзя. У брелка на четыре кнопки
        # один идентификатор и четыре кода: оставь мы только последний разбор,
        # три нажатия из четырёх исчезли бы вместе со своими кнопками. Поэтому
        # коды копятся отдельно от «последнего состояния».
        tx = tx_body(hit)
        if is_control(hit) and tx and not is_rolling(hit):
            codes.setdefault(did, {})[code_slug(hit)] = tx
        # Нажатие считается по capture_id, а не по числу разборов: пачка
        # повторов лежит у ноды в ОДНОМ слоте, то есть один захват — одно
        # нажатие, сколько бы раз rtl_433 ни узнал его внутри пачки. Нет
        # capture_id (сухой прогон, чужой источник) — считаем разбор за захват.
        caps.setdefault(did, set()).add(hit.get('capture_id', object()))

    stat['devices'] = len(hits)

    # Отметка о проходе ставится ДО проверки на пустоту, и это не мелочь.
    # После выброса 390 МГц пустой эфир — нормальный случай, единицы кадров в
    # сутки; выйди мы отсюда молча, `last_run` на исправной трубе показывал бы
    # «девять часов назад», то есть ровно то, ради обнаружения чего заведён.
    # Пустой эфир — результат, и на шине это должно быть видно так же, как в
    # счётчиках.
    #
    # `sensors/rf433/status` тут намеренно НЕ публикуется, хотя раньше
    # публиковался. Этот топик принадлежит прошивке ноды: ESPHome ставит на
    # него birth/will, то есть `offline` туда пишет САМ БРОКЕР, когда нода
    # перестала отвечать. Ворота, ставящие retained `online` раз в 10 минут,
    # затирали бы эту посмертную отметку — сдохшая нода выглядела бы живой.
    # Ровно та же ошибка, что и с отметкой о проходе, только опаснее: там
    # живое выглядело мёртвым, здесь мёртвое выглядит живым.
    #
    # `availability_topic` в конфигурациях по-прежнему указывает на него, и это
    # правильно: нет ноды — нет приёма, и чужим датчикам нечего показывать.
    # Смерть самих ворот при живой ноде ловится другим — `expire_after` на
    # значениях и вот этой отметкой.
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    batch = [('sensors/%s/last_run' % COLLECTOR, now, 1)]
    if not hits:
        return batch, stat

    # `state` подставляется только проверками: без него сухой прогон считает,
    # что не разрешено ничего, и показывает лишь объявления. Ветку разрешённого
    # прибора иначе не проверить без брокера, а именно в ней живут кнопки.
    if state is not None:
        enabled, usage_prev, txlib = state
    elif dry_run:
        enabled, usage_prev, txlib = {}, {}, {}
    else:
        enabled, usage_prev, txlib = broker_state()

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

        # Код для повтора складывается в библиотеку только у прибора со статическим
        # кодом и только из целого кадра. У обрезанного (нода упёрлась в потолок
        # слота в 256 таймингов) поля `tx` не будет вовсе, и это правильно:
        # обрубок пачки повторов в эфире — это кнопка, которая молча не
        # работает, а такая хуже отсутствующей.
        # Библиотека кодов прибора = что уже лежало в брокере + что услышали
        # в этом проходе. Плавающий код сюда не попадает вовсе: повторить его
        # нельзя в принципе, счётчик живёт в брелке, а не в эфире.
        fresh = codes.get(did) or {}
        if is_rolling(hit):
            stat['rolling'] += 1
            fresh = {}
        library = dict(txlib.get(did) or {})
        for cs, body in sorted(fresh.items()):
            if library.get(cs) != body:
                batch.append(('sensors/%s/%s/tx/%s' % (COLLECTOR, did, cs),
                              json.dumps(body, ensure_ascii=False), 1))
            library[cs] = body
        stat['sendable'] += len(fresh)

        usage = usage_update(usage_prev.get(did), hit, now,
                             bool(library), presses=len(caps.get(did) or [1]),
                             codes_seen=sorted(fresh) or None)
        batch.append(('sensors/%s/%s/usage' % (COLLECTOR, did),
                      json.dumps(usage, ensure_ascii=False), 1))
        batch.extend(configs(did, enabled[did], hit, library))
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

    # Пустой вход — не «ничего не делать», а «отчитаться, что проход был».
    empty, estat = run([], dry_run=True)
    assert [t for t, _, _ in empty] == ['sensors/rf433/last_run'], empty
    assert estat['hits'] == 0 and estat['devices'] == 0, estat

    # Сторож против возврата уже сделанной ошибки: `sensors/rf433/status` —
    # birth/will прошивки ноды, `offline` туда пишет брокер. Любая наша запись
    # в него затирает посмертную отметку и показывает сдохшую ноду живой.
    full, _ = run(sample, dry_run=True)
    for t, _, _ in list(full) + list(empty):
        assert not t.endswith('/status'), u'ворота пишут в чужой status: ' + t

    # ---------------------------------------------- передача и статистика ---

    # Час считается по местному времени, иначе «обычно в 18» съезжает на день.
    assert local_hour('2026-09-07T09:06:05Z', 'Europe/Moscow') == 12
    assert local_hour('2026-09-07T22:30:00Z', 'Europe/Moscow') == 1, u'через полночь'
    assert local_hour('2026-09-07T09:06:05Z', 'UTC') == 9

    # Метров не обещаем, отдаём словесную грубую отметку.
    assert distance_label(-60) == u'рядом'
    assert distance_label(-95) == u'далеко'
    assert distance_label(None) is None

    # Сырьё для отправки протаскивается насквозь и не пересобирается.
    whole = {'model': 'Akhan-100F14', 'id': '324422', 'code': 'a1',
             'tx': {'frequency': 433920000, 'raw': [341, -1023, 341]}}
    body = tx_body(whole)
    assert body == {'frequency': 433920000, 'raw': [341, -1023, 341],
                    'repeat': 1}, body
    # Обрезанный кадр приезжает БЕЗ `tx` — кнопки у него не будет.
    assert tx_body({'model': 'Akhan-100F14', 'code': 'a1'}) is None
    assert tx_body({'model': 'X', 'tx': {'raw': [1]}}) is None, u'без частоты не шлём'

    # Плавающий код не повторить в принципе — счётчик в брелке, не в эфире.
    assert is_rolling({'model': 'KeeLoq-Simple'})
    assert is_rolling({'model': 'Somfy-RTS'})
    assert not is_rolling({'model': 'Akhan-100F14'})

    # Кнопка: одна на код, не retained, тело запроса едет в payload_press.
    cfgs = dict((t, json.loads(pl)) for t, pl, _ in
                configs('akhan_324422', u'Чужой пульт', whole, {'a1': body}))
    btn = cfgs['homeassistant/button/rf433_akhan_324422/send_a1/config']
    assert btn['retain'] is False, u'retained нажатие выстрелит при рестарте HA'
    assert json.loads(btn['payload_press']) == body, btn
    assert btn['command_topic'] == 'sensors/rf433/cmd/send/akhan_324422/a1'
    assert btn['device']['suggested_area'] == FOREIGN_AREA
    # Атрибуты — только белым списком. `seen` — массив до 5000 отметок, и без
    # шаблона HA писал бы его в базу истории на каждом проходе, раз в 10 минут.
    prof = cfgs['homeassistant/sensor/rf433_akhan_324422/usage_profile/config']
    assert 'value_json.seen' not in prof['json_attributes_template'], prof
    assert 'value_json.hours' in prof['json_attributes_template'], prof
    # Без библиотеки кодов кнопок нет вовсе.
    plain = dict((t, 1) for t, _, _ in configs('x', 'X', whole))
    assert not any('/button/' in t for t in plain), plain

    # Статистика: считается здесь, а не шаблоном в HA.
    u1 = usage_update(None, whole, '2026-09-07T15:00:00Z', body)
    assert u1['total'] == 1 and u1['n24'] == 1 and u1['n7'] == 1
    assert u1['first_seen'] == '2026-09-07T15:00:00Z'
    assert u1['codes'] == {'a1': 1}, u1
    assert u1['sendable'] is True
    u2 = usage_update(u1, whole, '2026-09-07T15:10:00Z', body)
    assert u2['total'] == 2 and u2['codes'] == {'a1': 2}, u2
    assert u2['first_seen'] == u1['first_seen'], u'первая встреча не переписывается'
    assert sum(u2['hours']) == 2, u2['hours']
    assert u2['typical'] == '%02d:00' % local_hour('2026-09-07T15:00:00Z')
    # Старое выпадает из недельного окна по ВРЕМЕНИ, а не по количеству.
    old_u = usage_update(None, whole, '2020-01-01T00:00:00Z', body)
    fresh = usage_update(old_u, whole, time.strftime('%Y-%m-%dT%H:%M:%SZ',
                                                     time.gmtime()), body)
    assert fresh['total'] == 2, u'всего — за всё время'
    assert fresh['n7'] == 1, u'за неделю — только свежее'
    # Плавающий код: сырьё есть, но отправлять нечего.
    roll = usage_update(None, {'model': 'KeeLoq-X', 'code': 'z'},
                        '2026-09-07T15:00:00Z', None)
    assert roll['rolling'] is True and roll['sendable'] is False, roll

    # Брелок на две кнопки в одном проходе: обе должны выжить. Схлопывание по
    # устройству целиком теряло все нажатия кроме последнего вместе с их
    # кнопками — это была настоящая ошибка, найденная на живом брокере.
    fob = [json.dumps({'model': 'GateTxTest', 'id': 777, 'code': c,
                       'capture_id': n, 'rssi': -63,
                       'tx': {'frequency': 433920000, 'raw': [300, -300, 300]}})
           for n, c in enumerate(['aa', 'bb', 'aa'])]
    allow = ({'gatetxtest_777': u'Чужой брелок'}, {}, {})
    fb, fstat = run(fob, dry_run=True, state=allow)
    assert fstat['sendable'] == 2, u'две разные кнопки, а не одна: %r' % fstat
    txt = sorted(t for t, _, _ in fb if '/tx/' in t)
    assert txt == ['sensors/rf433/gatetxtest_777/tx/aa',
                   'sensors/rf433/gatetxtest_777/tx/bb'], txt
    # Три захвата — три нажатия; повторы внутри одного захвата им не считаются.
    same, sstat = run([fob[0], fob[0], fob[0]], dry_run=True, state=allow)
    assert sstat['sendable'] == 1, sstat

    u3 = usage_update(None, json.loads(fob[0]), '2026-09-07T15:00:00Z', True,
                      presses=3, codes_seen=['aa', 'bb'])
    assert u3['total'] == 3 and u3['n24'] == 3, u3
    assert u3['codes'] == {'aa': 1, 'bb': 1}, u3
    assert sum(u3['hours']) == 3, u3['hours']

    print(u'self-test: ок — 2 разбора, 1 шум, 2 объявления, 0 сущностей без '
          u'разрешения, пустой проход отмечен, в чужой status не пишем, '
          u'кнопка только у статического целого кадра, '
          u'брелок на две кнопки даёт две')


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
    # «опубликовано 0» на пустом проходе читается как «ворота ничего не
    # сделали» — то самое заблуждение, против которого и ставится отметка.
    # Счётчики тут про приборы; про сам проход надо сказать словами.
    # Три разных нуля, и путать их нельзя. Ноль строк — нода отдала пустой
    # журнал. Строки есть, разборов нет — та самая бесформенная полка, 82 кадра
    # в сутки по замеру ноды от 2026-09-07: эфир НЕ пуст, просто rtl_433 в нём
    # ничего не узнал, и это норма, а не сбой. Разборы есть, публикаций нет —
    # владелец эти приборы не разрешал, и это тоже норма.
    if not stat['lines']:
        tail = u' — журнал пуст, отметка о проходе поставлена'
    elif not stat['hits']:
        tail = u' — кадры были, узнавать нечего (шум), отметка поставлена'
    elif not stat['enabled']:
        tail = u' — разобрано, но ничего не разрешено владельцем'
    else:
        tail = u''
    print((u'строк %(lines)d, разборов %(hits)d, шума %(noise)d, устройств '
           u'%(devices)d, объявлено %(announced)d, опубликовано %(enabled)d '
           u'(пультов %(control)d)' % stat) + tail)


if __name__ == '__main__':
    main()
