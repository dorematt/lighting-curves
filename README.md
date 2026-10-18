# Lighting Curves

A Home Assistant integration for time-of-day lighting schedules, in the style of
Tapo's Auto Biorhythm. Each room has a list of points. A point has a time (a
clock time, or an offset from sunrise or sunset), a brightness and a colour
temperature. The integration fades between the points and sends the result to
[Adaptive Lighting](https://github.com/basnijholt/adaptive-lighting), which
still does the light control: turn-on interception, manual-control detection,
sleep mode and transitions.

Status: early development. Not ready for use.

## Install (HACS)

Requires Adaptive Lighting. Add this repo as a custom repository, type
**Integration**, then add **Lighting Curves** from Settings → Devices & services.

## Development

`dev/compose.yaml` runs a test Home Assistant with demo lights and this
integration mounted read-only. Restart the container after a code change.
