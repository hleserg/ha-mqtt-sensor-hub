# weather-engine

Turns the measurement namespace into derived weather state. It publishes what
follows from physics and refuses to publish what would need a model it does not
have — see `../DECISIONS.md` D-012.

Part of the default stack since 2026-08-20:

```sh
docker compose up -d weather-engine
docker compose logs -f weather-engine
```

## What it publishes

Retained, under `weather_state/`:

| Topic | Payload | How |
|---|---|---|
| `feels_like` | °C | wind chill (JAG/TI) below 10 °C in wind ≥ 4.8 km/h, Rothfusz heat index above 26.7 °C at ≥ 40 % RH, otherwise the air temperature |
| `frost_risk` | `none` / `watch` / `likely` | `likely` at or below 0 °C; `watch` at or below 3 °C with a dew point at or below 0 °C. Uses the surface probe when there is one — ground frost forms while the air is still above zero |
| `ice_risk` | `none` / `watch` / `likely` | `likely` at or below 0 °C with liquid water present (rain rate > 0, or humidity ≥ 95 % i.e. fog); `watch` at or below 1 °C with water, or below 0 °C at ≥ 85 % humidity |
| `data_quality` | `ok` / `partial` / `degraded` / `no_data` | grades the inputs — see below |
| `computed_at` | ISO 8601 | the **measurement** timestamp the derivation used, never the wall clock |
| `meta` | JSON | inputs present, missing, declared and undelivered; which `feels_like` formula ran; where the dew point came from |
| `engine_status` | `online` / `offline` | set on connect, and by the MQTT last will on death |

## What it does not publish, and why

`condition`, `rain_risk` and `thunderstorm` have Home Assistant entities and
stay `unknown`.

- **condition** needs cloud cover and precipitation *type*. A tipping-bucket
  gauge cannot tell rain from snow, and `cloud_index` is a station-specific
  number with no agreed scale.
- **rain_risk** is a probability. Producing an honest one needs months of local
  pressure, humidity and rainfall history to fit against; the recorder holds
  days. A threshold rule dressed as a percentage is not the same thing.
- **thunderstorm** needs strike *rate*. Distance-to-last-strike cannot separate
  an approaching cell from a departing one.

Dew point is also absent on purpose: it belongs to `weather/outdoor/dew_point`,
which is the station's namespace. When the station omits it the engine computes
it internally (Magnus-Tetens) and says so in `meta.dew_point_source`.

Pressure tendency lives in Home Assistant, which already holds the pressure
history — `sensor.outdoor_pressure_tendency`.

## Freshness

Nothing is gated on freshness; everything is stamped with it. `computed_at`
carries the measurement time of the inputs, so a derivation from a four-hour-old
reading reads as four hours old. When the station goes quiet the outputs freeze
and age — they are never overwritten with `unknown`, which is a Home Assistant
state and not a measurement.

`data_quality` is the exception that keeps moving, because it describes the
inputs rather than being derived from them. It describes the inputs and nothing
else: if the engine dies, `data_quality` freezes at whatever it last said —
possibly `ok`. `engine_status` is the topic that answers "is the engine alive",
and they are separate on purpose.

| Value | Meaning |
|---|---|
| `ok` | every declared input present, measurement newer than `stale_after_seconds` |
| `partial` | measurement older than that, or a declared input missing |
| `degraded` | measurement older than `offline_after_seconds`, or no timestamp at all |
| `no_data` | no temperature at all |

**"Declared" means `expected_inputs:` in `config.yaml`** — what the owner says
this station is supposed to provide. Not everything the engine could use, which
is the whole distinction: a station without a rain gauge or a surface probe is
not degraded, it is a station without those sensors, and grading it `partial`
forever turns the badge into wallpaper.

Building sensors one at a time is the normal case, so list only what exists
today. A node measuring temperature, humidity and pressure is *complete* and
reads `ok`; `meta.inputs_missing` still names everything absent, so nothing is
hidden. The day the anemometer goes up, `wind_speed` joins the list and a dead
anemometer correctly drops the grade.

`temperature` is mandatory in code — nothing derives without it, and its absence
is `no_data` rather than a grade. A name in `expected_inputs:` that the
derivation does not read makes the engine refuse to start; one with no topic
under `inputs:` logs a warning at startup, because otherwise it is a permanent
`partial` discovered weeks later from a yellow badge.

## Configuration

`config.yaml`, mounted read-only. Credentials never appear in it — they come
from `.env` as `WEATHER_ENGINE_MQTT_USER` / `WEATHER_ENGINE_MQTT_PASSWORD`.

Adding an input is one line under `inputs:`. Whether the derivation uses it is a
separate question, answered in `app/derive.py`.

## Tests

The formulas are checked against published reference values — wind chill at its
4.8 km/h validity boundary, the textbook 32 °C / 70 % heat index, the textbook
20 °C / 50 % dew point, and every branch of both risk rules. The grader is
checked separately, against the property `expected_inputs:` exists for: a
station that delivers exactly what it declares reads `ok` no matter how much
else it lacks.

```sh
docker compose run --rm --no-deps weather-engine python /app/main.py --selftest
```

It exits non-zero on failure and needs no broker.

## Layout

| File | What |
|---|---|
| `app/derive.py` | the formulas and the rules — pure functions, no I/O, self-testing |
| `app/main.py` | MQTT wiring, freshness stamping, change-detecting publisher |
| `config.example.yaml` | the shape of the config, with the thresholds explained |
