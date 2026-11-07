"""Run: docker run --rm --entrypoint python3 -v "$PWD":/repo -w /repo \
    ghcr.io/home-assistant/home-assistant:2026.10.2 tests/test_curve.py"""
import sys

sys.path.insert(0, ".")
from custom_components.lighting_curves.config_flow import DEFAULT_POINTS
from custom_components.lighting_curves.curve import POINTS, minute_of_day, resolve, target

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

# A13: clock times normalise to HH:MM (6:00 -> 06:00) and duplicates are rejected,
# including the same sun anchor and offset written differently.
assert POINTS([{"time": "6:00", "brightness": 50, "color_temp_kelvin": 2700}])[0]["time"] == "06:00"
for dup in (
    [
        {"time": "06:00", "brightness": 50, "color_temp_kelvin": 2700},
        {"time": "6:00", "brightness": 60, "color_temp_kelvin": 3500},
    ],
    [
        {"time": "sunrise+30", "brightness": 50, "color_temp_kelvin": 2700},
        {"time": "sunrise+030", "brightness": 60, "color_temp_kelvin": 3500},
    ],
):
    try:
        POINTS(dup)
    except vol.Invalid:
        continue
    raise AssertionError(f"accepted duplicate {dup}")

# A5: a sun time carried past midnight clamps at the boundary it crossed (00:30),
# not the opposite one (23:00).
night = POINTS(
    [
        {"time": "00:30", "brightness": 10, "color_temp_kelvin": 2700},
        {"time": "sunrise-30", "brightness": 30, "color_temp_kelvin": 3500},
        {"time": "23:00", "brightness": 20, "color_temp_kelvin": 2700},
    ]
)
assert resolve(night, 40, 1080) == [30, 30, 1380]  # sunrise 00:40: clamped at 00:30
assert resolve(night, 20, 1080) == [30, 30, 1380]  # sunrise 00:20: still 00:30, was 23:00 before
assert resolve(night, 100, 1080) == [30, 70, 1380]  # past 00:30 it follows the sun again

# Sun-only schedules have no clock bounds: a late sunset wraps past midnight.
late = POINTS(
    [
        {"time": "sunset+30", "brightness": 30, "color_temp_kelvin": 2700},
        {"time": "sunset+60", "brightness": 10, "color_temp_kelvin": 2200},
    ]
)
assert resolve(late, SUNRISE, 1430) == [20, 50]

# A sun point past midnight on the reference day keeps its place between clocks.
after = POINTS(
    [
        {"time": "sunset+480", "brightness": 5, "color_temp_kelvin": 2200},
        {"time": "06:00", "brightness": 60, "color_temp_kelvin": 4000},
        {"time": "22:00", "brightness": 20, "color_temp_kelvin": 2700},
    ]
)
assert resolve(after, SUNRISE, 1080) == [120, 360, 1320]
assert resolve(after, SUNRISE, 1260) == [300, 360, 1320]  # sunset 21:00: 05:00
assert resolve(after, SUNRISE, 1380) == [360, 360, 1320]  # would be 07:00: clamped at 06:00

print("ok")
