"""Derived weather quantities — pure functions, no MQTT, no I/O.

Everything here is a documented deterministic formula or a documented rule.
There is no model, no fitting and no probability: where a real answer would
need history this module returns `None` and the caller publishes nothing.

Split out from `main.py` so it can be exercised without a broker
(`python /app/derive.py` runs the self-test, as does `main.py --selftest`).
"""

from __future__ import annotations

import math

# Magnus coefficients over water, WMO-recommended set (Sonntag 1990).
MAGNUS_A = 17.62
MAGNUS_B = 243.12


def dew_point(temp_c: float, humidity_pct: float) -> float | None:
    """Magnus-Tetens dew point. Returns None outside the formula's domain."""
    if humidity_pct <= 0 or humidity_pct > 100:
        return None
    if not -45.0 <= temp_c <= 60.0:
        return None
    gamma = (MAGNUS_A * temp_c) / (MAGNUS_B + temp_c) + math.log(humidity_pct / 100.0)
    return round((MAGNUS_B * gamma) / (MAGNUS_A - gamma), 2)


def wind_chill(temp_c: float, wind_ms: float) -> float | None:
    """Environment Canada / JAG-TI wind chill index.

    Valid only for T <= 10 C and wind >= 4.8 km/h; outside that the formula
    produces nonsense, so it returns None and the caller falls back.
    """
    wind_kmh = wind_ms * 3.6
    if temp_c > 10.0 or wind_kmh < 4.8:
        return None
    v = wind_kmh ** 0.16
    return round(13.12 + 0.6215 * temp_c - 11.37 * v + 0.3965 * temp_c * v, 2)


def heat_index(temp_c: float, humidity_pct: float) -> float | None:
    """Rothfusz heat index. Valid for T >= 26.7 C and RH >= 40%."""
    if temp_c < 26.7 or humidity_pct < 40.0:
        return None
    t = temp_c * 9.0 / 5.0 + 32.0
    r = humidity_pct
    hi = (
        -42.379
        + 2.04901523 * t
        + 10.14333127 * r
        - 0.22475541 * t * r
        - 6.83783e-3 * t * t
        - 5.481717e-2 * r * r
        + 1.22874e-3 * t * t * r
        + 8.5282e-4 * t * r * r
        - 1.99e-6 * t * t * r * r
    )
    return round((hi - 32.0) * 5.0 / 9.0, 2)


def feels_like(temp_c: float, humidity_pct: float | None, wind_ms: float | None):
    """Apparent temperature, and which formula produced it.

    Returns (value, method). `method` is part of the published metadata so a
    reading of -7.8 next to an air temperature of -2.5 is explainable rather
    than suspicious.
    """
    if wind_ms is not None:
        wc = wind_chill(temp_c, wind_ms)
        if wc is not None:
            return wc, "wind_chill_jag_ti"
    if humidity_pct is not None:
        hi = heat_index(temp_c, humidity_pct)
        if hi is not None:
            return hi, "heat_index_rothfusz"
    return round(temp_c, 2), "air_temperature"


# --------------------------------------------------------------------------
# Risk levels are categorical on purpose. A percentage here would be an
# if-statement wearing a probability's clothes; the rules below are stated in
# full so a `watch` can be argued with.  See DECISIONS.md D-012.
# --------------------------------------------------------------------------

LEVELS = ("none", "watch", "likely")


def frost_risk(temp_c: float, dew_c: float | None, surface_c: float | None):
    """Frost forming on surfaces.

    likely : reference temperature at or below 0 C
    watch  : at or below 3 C *and* the dew point is at or below 0 C, i.e. what
             condenses out will condense as ice
    none   : otherwise

    The reference temperature is the surface reading when there is one, because
    ground frost forms while the air is still above zero.
    """
    t_ref = surface_c if surface_c is not None else temp_c
    basis = "surface_temperature" if surface_c is not None else "air_temperature"
    if t_ref <= 0.0:
        return "likely", basis
    if t_ref <= 3.0 and dew_c is not None and dew_c <= 0.0:
        return "watch", basis
    return "none", basis


def ice_risk(
    temp_c: float,
    humidity_pct: float | None,
    rain_rate: float | None,
    surface_c: float | None,
):
    """Ice on surfaces — freezing wet, not frost.

    likely : reference temperature <= 0 C with liquid water present
             (measurable rain rate, or humidity at 95%+ i.e. fog/drizzle)
    watch  : <= 1 C with water present, or <= 0 C with humidity 85%+
    none   : otherwise

    Known blind spot: with no precipitation history this cannot see black ice
    left by rain that stopped before the freeze. That needs the recorder, and
    is deliberately not faked here.
    """
    t_ref = surface_c if surface_c is not None else temp_c
    wet = (rain_rate is not None and rain_rate > 0.0) or (
        humidity_pct is not None and humidity_pct >= 95.0
    )
    damp = humidity_pct is not None and humidity_pct >= 85.0
    if t_ref <= 0.0 and wet:
        return "likely"
    if (t_ref <= 1.0 and wet) or (t_ref <= 0.0 and damp):
        return "watch"
    return "none"


def _selftest() -> None:
    def close(a, b, eps=0.05):
        assert a is not None and abs(a - b) <= eps, f"{a} != {b}"

    # Wind chill against the live simulator sample: -2.5 C, 4.6 m/s = 16.56 km/h
    close(wind_chill(-2.5, 4.6), -7.80)
    # Validity boundary: 4.8 km/h is exactly 1.3333 m/s
    assert wind_chill(-5.0, 4.79 / 3.6) is None, "below 4.8 km/h must not apply"
    close(wind_chill(-5.0, 4.81 / 3.6), -7.16, eps=0.05)
    assert wind_chill(10.1, 10.0) is None, "above 10 C must not apply"
    # A brisk wind must make it feel colder, never warmer
    assert wind_chill(0.0, 10.0) < 0.0

    # Heat index: 32 C / 70% is a well-known ~41 C
    close(heat_index(32.0, 70.0), 41.0, eps=0.8)
    assert heat_index(26.0, 90.0) is None
    assert heat_index(30.0, 30.0) is None

    # Dew point: 20 C / 50% -> 9.26 C is the textbook Magnus value
    close(dew_point(20.0, 50.0), 9.26, eps=0.05)
    close(dew_point(-2.5, 70.0), -7.13, eps=0.3)   # the simulator's own pair
    assert dew_point(20.0, 0.0) is None
    assert dew_point(20.0, 100.0) == 20.0 or abs(dew_point(20.0, 100.0) - 20.0) < 0.01

    # feels_like dispatch
    v, m = feels_like(-2.5, 70.0, 4.6)
    close(v, -7.80)
    assert m == "wind_chill_jag_ti"
    v, m = feels_like(32.0, 70.0, 0.5)
    assert m == "heat_index_rothfusz"
    v, m = feels_like(18.0, 55.0, 1.0)
    close(v, 18.0)
    assert m == "air_temperature"

    # Frost
    assert frost_risk(-1.0, -5.0, None) == ("likely", "air_temperature")
    assert frost_risk(2.0, -1.0, None) == ("watch", "air_temperature")
    assert frost_risk(2.0, 1.5, None) == ("none", "air_temperature")
    # Air above zero, ground below: surface wins
    assert frost_risk(4.0, 0.0, -0.5) == ("likely", "surface_temperature")

    # Ice
    assert ice_risk(-1.0, 80.0, 0.5, None) == "likely"
    assert ice_risk(0.5, 96.0, 0.0, None) == "watch"
    assert ice_risk(-0.5, 88.0, 0.0, None) == "watch"
    assert ice_risk(-5.0, 40.0, 0.0, None) == "none"
    assert ice_risk(5.0, 99.0, 2.0, None) == "none"

    print("derive selftest: OK")


if __name__ == "__main__":
    _selftest()
