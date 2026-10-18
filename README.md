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

A schedule without an Adaptive Lighting switch only provides a sensor, for your own
automations.

## Install

Requires Home Assistant 2026.10 or later. Adaptive Lighting is needed for schedules
that drive lights.

- **HACS:** add this repo as a custom repository, type **Integration**.
- **Manual:** copy `custom_components/lighting_curves` into `/config/custom_components/`.

Restart Home Assistant, then add **Lighting Curves** in Settings → Devices &
services. Add it once per schedule: give it a name and, optionally, the main switch
of an Adaptive Lighting entry. A new schedule starts with a default day curve.

## What you get

- `sensor.<name>_lighting_curve`: the target now. The state is the brightness in %,
  and the attribute `color_temp_kelvin` or `rgb_color` is the colour.
- The **Lighting Curves card** (`type: custom:lighting-curves-card`). The integration
  loads it, so no dashboard resource is needed. It shows a tab per schedule, a day
  graph, and the list of points. Select a point to edit it with Home Assistant's own
  pickers. **Preview on the lights** shows the setting live on all the schedule's
  lights. Adaptive Lighting leaves those lights alone until you select Save or
  Cancel, or for 10 minutes after the last change.

  Card options: `schedule` (a schedule name, to show only that one) and `title`.

## Development

`dev/compose.yaml` runs a test Home Assistant with demo lights and this integration
mounted read-only. Restart the container after a code change; the card's URL carries
the file's modification time, so a restart is also what refreshes the card.
`dev/ws.py` sends one websocket message to it (copy it into `dev/config/` first).

Tests (run from the repo root; uses the HA image so `homeassistant` imports work):

    for t in test_curve test_parity; do
      docker run --rm --entrypoint python3 -v "$PWD":/repo -w /repo \
        ghcr.io/home-assistant/home-assistant:2026.10.2 tests/$t.py
    done

`tests/test_parity.py` holds the schedules that replaced Matt's template sensors and
checks them minute by minute against the old templates.
