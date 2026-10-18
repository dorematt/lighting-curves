"""Matt's schedules as points, checked against the template sensors they replace.

The OLD_* functions are the Jinja templates from the live configuration.yaml,
ported line for line. Run like test_curve.py.
"""
import sys

sys.path.insert(0, ".")
from custom_components.lighting_curves.curve import POINTS, target

AMBER = [236, 153, 9]


def shared(night):
    return [
        {"time": "05:00", "brightness": night, "color_temp_kelvin": 2700},
        {"time": "06:00", "brightness": 100, "color_temp_kelvin": 6500},
        {"time": "sunset-45", "brightness": 100, "color_temp_kelvin": 6500},
        {"time": "sunset+45", "brightness": 35, "color_temp_kelvin": 2700},
        {"time": "22:30", "brightness": 35, "color_temp_kelvin": 2700},
        {"time": "23:00", "brightness": night, "color_temp_kelvin": 2700},
    ]


SCHEDULES = {
    "kitchen": shared(3),
    "garage": shared(30),
    "stairwell": shared(5),
    "master_bedroom": [
        {"time": "04:58", "brightness": 10, "color_temp_kelvin": 2700},
        {"time": "04:59", "brightness": 1, "rgb_color": AMBER},
        {"time": "05:09", "brightness": 6, "rgb_color": AMBER},
        {"time": "05:10", "brightness": 2, "color_temp_kelvin": 2500},
        {"time": "06:00", "brightness": 80, "color_temp_kelvin": 6500},
        {"time": "06:30", "brightness": 100, "color_temp_kelvin": 6500},
        {"time": "sunset-45", "brightness": 100, "color_temp_kelvin": 6500},
        {"time": "sunset+45", "brightness": 30, "color_temp_kelvin": 2700},
        {"time": "20:30", "brightness": 30, "color_temp_kelvin": 2700},
        {"time": "21:00", "brightness": 10, "color_temp_kelvin": 2700},
    ],
}


def old_shared(m, s, night):
    if m < 300:
        a, b, f = "night", "night", 0
    elif m < 360:
        a, b, f = "night", "day", (m - 300) / 60
    elif m < s - 45:
        a, b, f = "day", "day", 0
    elif m < s + 45:
        a, b, f = "day", "evening", (m - s + 45) / 90
    elif m < 1350:
        a, b, f = "evening", "evening", 0
    elif m < 1380:
        a, b, f = "evening", "night", (m - 1350) / 30
    else:
        a, b, f = "night", "night", 0
    k = {"day": 6500, "evening": 2700, "night": 2700}
    br = {"day": 100, "evening": 35, "night": night}
    return round(br[a] + (br[b] - br[a]) * f), round((k[a] + (k[b] - k[a]) * f) / 50) * 50


def old_bedroom(m, s):
    if m < 299:
        return 10, 2700, False
    if m < 310:
        return max(1, 1 + (m - 300) / 2), None, True
    if m < 360:
        f = (m - 310) / 50
        return 2 + 78 * f, 2500 + 4000 * f, False
    if m < 390:
        return 80 + 20 * (m - 360) / 30, 6500, False
    f = 0 if m < s - 45 else (m - s + 45) / 90 if m < s + 45 else 1
    g = 0 if m < 1230 else (m - 1230) / 30 if m < 1260 else 1
    return min(100 - 70 * f, 100 - 90 * g), min(6500 - 3800 * f, 6500 - 3800 * g), False


for pts in SCHEDULES.values():
    POINTS(pts)

SUNRISE = 400  # no point uses sunrise
for sunset in (17 * 60 + 15, 20 * 60 + 30):  # Adelaide midwinter, midsummer
    for name, night in (("kitchen", 3), ("garage", 30), ("stairwell", 5)):
        for m in range(DAY := 1440):
            new = target(SCHEDULES[name], m, SUNRISE, sunset)
            ob, ok = old_shared(m, sunset, night)
            assert abs(new["brightness"] - ob) <= 1, (name, sunset, m, new, ob)
            assert abs(new["color_temp_kelvin"] - ok) <= 25, (name, sunset, m, new, ok)

    for m in range(1440):
        new = target(SCHEDULES["master_bedroom"], m, SUNRISE, sunset)
        ob, ok, amber = old_bedroom(m, sunset)
        if amber:
            assert new["rgb_color"] == AMBER and abs(new["brightness"] - ob) <= 1, (sunset, m, new, ob)
            continue
        # Known, accepted differences: the 20:30-21:00 wind-down is a straight
        # fade (old: held 30 % to 20:53), and in summer the sunset fade ends at
        # 20:30 (old: min() of two overlapping fades).
        if 1230 <= m < 1260 or (sunset > 1200 and sunset - 45 <= m < 1260):
            continue
        tol_k = 50 if 310 <= m < 360 else 25  # the 1-minute 05:09 -> 05:10 step
        assert abs(new["brightness"] - ob) <= 1, (sunset, m, new, ob)
        assert abs(new["color_temp_kelvin"] - ok) <= tol_k, (sunset, m, new, ok)

# Summer: sunset+45 (21:15) stops at the fixed 20:30 point, so the wind-down still ends at 21:00.
assert target(SCHEDULES["master_bedroom"], 1230, SUNRISE, 1230) == {"brightness": 30, "color_temp_kelvin": 2700}
assert target(SCHEDULES["master_bedroom"], 1260, SUNRISE, 1230) == {"brightness": 10, "color_temp_kelvin": 2700}

print("ok")
