"""Run: docker run --rm --entrypoint python3 -v "$PWD":/repo -w /repo \
    ghcr.io/home-assistant/home-assistant:2026.10.2 tests/test_curve.py"""
import sys

sys.path.insert(0, ".")
from custom_components.lighting_curves.config_flow import DEFAULT_POINTS
from custom_components.lighting_curves.curve import POINTS, minute_of_day, target

# After the HA imports: HA swaps voluptuous for a compatible module on import.
import voluptuous as vol  # noqa: E402

SUNRISE, SUNSET = 6 * 60 + 30, 19 * 60  # 06:30, 19:00
pts = POINTS(DEFAULT_POINTS)


def at(hhmm):
    h, m = map(int, hhmm.split(":"))
    return target(pts, h * 60 + m, SUNRISE, SUNSET)


# Sun-relative times, including a wrap past midnight.
assert minute_of_day("sunset+45", SUNRISE, SUNSET) == 19 * 60 + 45
assert minute_of_day("sunrise-30", 10, SUNSET) == 1420

# On a point, between points, and across midnight (23:00 -> 05:00 holds night).
assert at("06:00") == {"brightness": 100, "color_temp_kelvin": 6500}
assert at("05:30") == {"brightness": 52, "color_temp_kelvin": 4600}
assert at("02:00") == {"brightness": 3, "color_temp_kelvin": 2700}
assert at("23:59") == {"brightness": 3, "color_temp_kelvin": 2700}
assert at("19:00") == {"brightness": 68, "color_temp_kelvin": 4600}

# A fade that wraps past midnight: 22:00 -> 02:00.
wrap = [
    {"time": "02:00", "brightness": 10, "color_temp_kelvin": 2000},
    {"time": "22:00", "brightness": 50, "color_temp_kelvin": 3000},
]
assert target(wrap, 0, SUNRISE, SUNSET) == {"brightness": 30, "color_temp_kelvin": 2500}

# White to RGB fades in RGB; a single point holds all day.
mixed = [
    {"time": "05:00", "brightness": 1, "rgb_color": [255, 100, 0]},
    {"time": "05:10", "brightness": 11, "color_temp_kelvin": 2500},
]
assert target(mixed, 300, SUNRISE, SUNSET) == {"brightness": 1, "rgb_color": [255, 100, 0]}
mid = target(mixed, 305, SUNRISE, SUNSET)
assert mid["brightness"] == 6 and mid["rgb_color"][0] == 255 and 100 < mid["rgb_color"][1] < 255
assert target(mixed[:1], 900, SUNRISE, SUNSET)["rgb_color"] == [255, 100, 0]

# Validation at the trust boundary.
for bad in (
    [],
    [{"time": "25:00", "brightness": 50, "color_temp_kelvin": 2700}],
    [{"time": "noon", "brightness": 50, "color_temp_kelvin": 2700}],
    [{"time": "06:00", "brightness": 0, "color_temp_kelvin": 2700}],
    [{"time": "06:00", "brightness": 50}],
    [{"time": "06:00", "brightness": 50, "color_temp_kelvin": 2700, "rgb_color": [1, 2, 3]}],
    [{"time": "06:00", "brightness": 50, "rgb_color": [1, 2]}],
):
    try:
        POINTS(bad)
    except vol.Invalid:
        continue
    raise AssertionError(f"accepted {bad}")

print("ok")
