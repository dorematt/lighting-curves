"""A day's points, and the fade between them.

A point is a time plus a light setting:
    {"time": "06:00" | "sunrise-30" | "sunset+45",
     "brightness": 1-100,
     "color_temp_kelvin": 2700}  or  {"rgb_color": [255, 120, 0]}

The day is a loop: after the last point the light fades towards the first point
of the next day. All times are minutes since local midnight.
"""
from __future__ import annotations

from bisect import bisect_right
import re

import voluptuous as vol

from homeassistant.util.color import color_temperature_to_rgb

DAY = 1440
_TIME = re.compile(r"^(?:([01]?\d|2[0-3]):([0-5]\d)|(sunrise|sunset)([+-]\d{1,3}))$")


def _valid_time(value):
    if not isinstance(value, str) or not _TIME.match(value):
        raise vol.Invalid(f"expected HH:MM, sunrise±MIN or sunset±MIN, got {value!r}")
    return value


def _one_colour(point):
    if ("color_temp_kelvin" in point) == ("rgb_color" in point):
        raise vol.Invalid("a point needs exactly one of color_temp_kelvin or rgb_color")
    return point


POINT = vol.All(
    vol.Schema(
        {
            vol.Required("time"): _valid_time,
            vol.Required("brightness"): vol.All(int, vol.Range(1, 100)),
            vol.Optional("color_temp_kelvin"): vol.All(int, vol.Range(1000, 10000)),
            vol.Optional("rgb_color"): vol.All(
                [vol.All(int, vol.Range(0, 255))], vol.Length(min=3, max=3)
            ),
        }
    ),
    _one_colour,
)
POINTS = vol.All([POINT], vol.Length(min=1))


def minute_of_day(time: str, sunrise: float, sunset: float) -> float:
    m = _TIME.match(time)
    if m[1]:
        return int(m[1]) * 60 + int(m[2])
    return ((sunrise if m[3] == "sunrise" else sunset) + int(m[4])) % DAY


def _rgb(point) -> tuple:
    if "rgb_color" in point:
        return tuple(point["rgb_color"])
    return color_temperature_to_rgb(point["color_temp_kelvin"])


def target(points: list, now: float, sunrise: float, sunset: float) -> dict:
    """Brightness and colour at `now`, faded between the points either side of it."""
    timed = sorted(
        ((minute_of_day(p["time"], sunrise, sunset), p) for p in points),
        key=lambda tp: tp[0],
    )
    i = bisect_right([t for t, _ in timed], now)
    (t0, p0), (t1, p1) = timed[i - 1], timed[i % len(timed)]
    span = (t1 - t0) % DAY or DAY
    f = ((now - t0) % DAY) / span
    if p0 is p1 or f == 0:
        f, p1 = 0, p0

    out = {"brightness": round(p0["brightness"] + (p1["brightness"] - p0["brightness"]) * f)}
    if "color_temp_kelvin" in p0 and "color_temp_kelvin" in p1:
        k0, k1 = p0["color_temp_kelvin"], p1["color_temp_kelvin"]
        out["color_temp_kelvin"] = round(k0 + (k1 - k0) * f)
    else:
        # ponytail: straight RGB fade; mixed white/RGB fades may look muddy mid-way, fade in xy if so
        out["rgb_color"] = [round(a + (b - a) * f) for a, b in zip(_rgb(p0), _rgb(p1))]
    return out
