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
    m = _TIME.match(value) if isinstance(value, str) else None
    if not m:
        raise vol.Invalid(f"expected HH:MM, sunrise±MIN or sunset±MIN, got {value!r}")
    if m[1]:
        return f"{int(m[1]):02d}:{m[2]}"  # 6:00 -> 06:00, so duplicates compare equal
    return value


def _one_colour(point):
    if ("color_temp_kelvin" in point) == ("rgb_color" in point):
        raise vol.Invalid("a point needs exactly one of color_temp_kelvin or rgb_color")
    return point


_SETTING = {
    vol.Required("brightness"): vol.All(int, vol.Range(1, 100)),
    vol.Optional("color_temp_kelvin"): vol.All(int, vol.Range(1000, 10000)),
    vol.Optional("rgb_color"): vol.All([vol.All(int, vol.Range(0, 255))], vol.Length(min=3, max=3)),
}


def _unique_times(points):
    """Two points cannot share an instant, fixed or sun-relative."""
    seen = []
    for p in points:
        m = _TIME.match(p["time"])
        key = (m[3], int(m[4])) if m[3] else p["time"]
        if key in seen:
            raise vol.Invalid(f"two points at {p['time']}: merge them into one")
        seen.append(key)
    return points


SETTING = vol.All(vol.Schema(_SETTING), _one_colour)
POINT = vol.All(vol.Schema({vol.Required("time"): _valid_time, **_SETTING}), _one_colour)
POINTS = vol.All([POINT], vol.Length(min=1), _unique_times)


def minute_of_day(time: str, sunrise: float, sunset: float) -> float:
    m = _TIME.match(time)
    if m[1]:
        return int(m[1]) * 60 + int(m[2])
    return ((sunrise if m[3] == "sunrise" else sunset) + int(m[4])) % DAY


# The order of points does not change with the seasons: it is their order on a day
# with sunrise at 06:00 and sunset at 18:00.
def ordered(points: list) -> list:
    return sorted(points, key=lambda p: minute_of_day(p["time"], 360, 1080))


def resolve(points: list, sunrise: float, sunset: float) -> list:
    """Today's minute (0-1439) for each point of ordered(points)."""
    return [t % DAY for t in _unwrapped(points, sunrise, sunset)]


def _unwrapped(points: list, sunrise: float, sunset: float) -> list:
    """Each point's minute in ordered()'s frame, before wrapping to 0-1439.

    A sun-relative point cannot move past the clock-time points on each side of it,
    so a late summer sunset cannot carry an evening point past a fixed 20:30.
    Clamping happens in the reference day's frame, before wrapping to 0-1439, so a
    point carried past midnight stays at the boundary it crossed. The neighbours
    are circular: the clock point before the first one is the last, a day earlier.
    """
    pts = ordered(points)
    times = [minute_of_day(p["time"], sunrise, sunset) for p in pts]
    clock = [t if _TIME.match(p["time"])[1] else None for p, t in zip(pts, times)]
    clocks = [c for c in clock if c is not None]
    for i, p in enumerate(pts):
        if clock[i] is not None:
            continue
        m = _TIME.match(p["time"])
        off = int(m[4])
        ref = (360 if m[3] == "sunrise" else 1080) + off
        t = (sunrise if m[3] == "sunrise" else sunset) + off - (ref - ref % DAY)  # ordered()'s frame
        if clocks:
            lo = max((c for c in clock[:i] if c is not None), default=clocks[-1] - DAY)
            hi = min((c for c in clock[i + 1 :] if c is not None), default=clocks[0] + DAY)
            t = min(max(t, lo), hi)
        times[i] = t
    return times


def _rgb(point) -> tuple:
    if "rgb_color" in point:
        return tuple(point["rgb_color"])
    return color_temperature_to_rgb(point["color_temp_kelvin"])


def target(points: list, now: float, sunrise: float, sunset: float) -> dict:
    """Brightness and colour at `now`, faded between the points either side of it."""
    now %= DAY  # 24:00 is 00:00: the graph's last sample must match its first
    # Sort by minute. A sun point clamped onto a clock point shares its minute: one
    # carried from the day before (u >= DAY) goes first, one carried back (u < 0)
    # last, so the later of the two takes over at that minute.
    timed = sorted(
        ((u % DAY, -(u // DAY), i, p) for i, (u, p) in enumerate(zip(_unwrapped(points, sunrise, sunset), ordered(points)))),
        key=lambda x: x[:3],
    )
    i = bisect_right([t for t, *_ in timed], now)
    (t0, *_, p0), (t1, *_, p1) = timed[i - 1], timed[i % len(timed)]
    span = (t1 - t0) % DAY  # 0: one point, or every point at the same minute
    f = ((now - t0) % DAY) / span if span else 0
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
