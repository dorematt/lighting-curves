# Development

## Layout

- `custom_components/lighting_curves/`: the integration. `curve.py` holds the curve maths,
  `light_control.py` and `hass_utils.py` the light control and turn-on hook, `api.py` the shared
  operations behind the WebSocket commands and actions.
- `custom_components/lighting_curves/frontend/lighting-curves-card.js`: the card. The integration serves
  it, and its URL carries the file's modification time.
- `docs/api.md`: the actions and WebSocket commands.

## Try a change

Mount `custom_components/lighting_curves` into a test Home Assistant's `config/custom_components/`.
Restart Home Assistant after a code change. A restart is also what gives the card a new URL; refresh the
browser after it.

## Tests

Run from the repo root. The HA image gives the tests `homeassistant` and its libraries. Run them on the
oldest supported HA and on the current one:

```sh
for img in 2026.10.4 2026.11.0; do
  for t in tests/*.py; do
    docker run --rm --entrypoint python3 -v "$PWD":/repo -w /repo \
      ghcr.io/home-assistant/home-assistant:$img "$t" || echo "FAIL $img $t"
  done
done
node tests/test_card.js
```

`tests/test_parity.py` holds real schedules that replaced older template sensors, and checks them minute
by minute against those templates.

## Icon

`scripts/make_icon.py` draws `brand/icon.png` and `brand/icon@2x.png`. Run it the same way as the tests,
with `-u "$(id -u):$(id -g)"` so the files belong to you.
