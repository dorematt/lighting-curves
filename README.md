# Lighting Curves

A Home Assistant integration for time-of-day lighting schedules, in the style of
Tapo's Auto Biorhythm. Each schedule is a list of points. A point is a time and a
light setting:

- **Time:** a clock time (`06:00`), or minutes from sunrise or sunset (`sunset-45`).
- **Brightness:** 1–100 %.
- **Colour:** a white temperature in kelvin, or an RGB colour.

The light fades in a straight line from one point to the next, and the day loops:
after the last point it fades towards the first point of the next day. A
sun-relative point cannot move past the clock-time points on each side of it, so a
late summer sunset cannot carry an evening point past a fixed 20:30 point.

Each minute the schedule sends its target to an
[Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting) switch, with
the minimum and maximum set to the same value. Adaptive Lighting still controls the
lights: turn-on interception, manual-control detection and transitions. RGB targets
go through Adaptive Lighting's sleep mode, which the integration turns on and off by
itself in a schedule that has RGB points.

You manage the lights from the card. When a schedule first gets lights, the
integration makes an Adaptive Lighting entry for it, with the settings of an existing
entry (or Adaptive Lighting's defaults). A light follows one schedule: adding it to
one takes it off any other Adaptive Lighting entry.

Untick **Use Adaptive Lighting** in a schedule's lights to drive them without it: a
light then gets the curve's setting each time it turns on (from a motion sensor, a
switch, anything), and keeps it until it next turns on. Use this for lights that are
on only briefly, or that Adaptive Lighting cannot drive.

A schedule with no lights only provides a sensor, for your own automations.

## Install

Requires Home Assistant 2026.10 or later. [Adaptive Lighting](https://my.home-assistant.io/redirect/hacs_repository/?owner=basnijholt&repository=adaptive-lighting&category=integration)
(HACS, or Marketplace from 2026.11) is needed for lights that follow the curve all
the time; without it, lights get the curve's setting when they turn on.

- **HACS:** add this repo as a custom repository, type **Integration**.
- **Manual:** copy `custom_components/lighting_curves` into `/config/custom_components/`.

Restart Home Assistant, add the card to a dashboard, and select **New** on it to add
a schedule. A new schedule starts with a default day curve and no lights. (Settings →
Devices & services → Lighting Curves also adds one, linked to an Adaptive Lighting
entry you already have.)

## What you get

- `sensor.<name>_lighting_curve`: the target now. The state is the brightness in %,
  and the attribute `color_temp_kelvin` or `rgb_color` is the colour.
- The **Lighting Curves card** (`type: custom:lighting-curves-card`). The integration
  loads it, so no dashboard resource is needed. It shows a tab per schedule, a day
  graph, the schedule's lights, and the list of points. Select **Lights** to choose
  the lights. Select a point to edit it with Home Assistant's own
  pickers. **Preview on the lights** shows the setting live on all the schedule's
  lights. Adaptive Lighting leaves those lights alone until you select Save or
  Cancel, or for 10 minutes after the last change.

  Card options: `schedule` (a schedule name, to show only that one) and `title`.

## Development

To try it, mount `custom_components/lighting_curves` into a test Home Assistant's
`config/custom_components/`. Restart HA after a code change; the card's URL carries
the file's modification time, so a restart is also what refreshes the card.

Tests (run from the repo root; uses the HA image so `homeassistant` imports work):

    for t in test_curve test_parity; do
      docker run --rm --entrypoint python3 -v "$PWD":/repo -w /repo \
        ghcr.io/home-assistant/home-assistant:2026.10.2 tests/$t.py
    done

`tests/test_parity.py` holds the schedules that replaced Matt's template sensors and
checks them minute by minute against the old templates.
