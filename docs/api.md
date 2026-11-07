# Lighting Curves API

First install the integration and restart HA, then use **Settings → Devices & services → Add integration → Lighting Curves** to create the first schedule. Installation alone does not register the card or API. Choose lights in the schedule's options; add the **Lighting Curves** dashboard card to edit the curve visually.

Each schedule is one HA config entry. Use its `entry_id` as the stable key; its name is `title` in responses. Only loaded schedules are listed. Creation returns an ID even if HA subsequently fails to load the entry: check `get_schedules` before editing. Rename/delete through HA's integration settings (or HA's standard `config_entries/update` and `config_entries/delete` WebSocket commands).

## Actions (services)

HA authentication is required. All three writes require an administrator when called by a user. Automations can call them, because they run without a user. A script started from a dashboard by a non-admin user runs as that user, so its write calls fail with `Unauthorized`. The actions appear in Developer Tools → Actions and `/api/services`.

| Action | Fields | Response |
| --- | --- | --- |
| `lighting_curves.create_schedule` | `name` (nonempty, trimmed, case-insensitively unique) | `{ "entry_id": "…" }` when requested; default curve, no lights |
| `lighting_curves.set_points` | `entry_id`, `points` | No response; replaces **all** points |
| `lighting_curves.set_lights` | `entry_id`, `lights` (array of existing `light.*` IDs) | No response; replaces **all** assignments, moving those lights out of other LC schedules |
| `lighting_curves.get_schedules` | Optional `entry_id` | **Response required:** `{ "schedules": [ … ] }`; omit ID for all loaded schedules |

`[]` clears lights, but points must not be empty. Light IDs are deduplicated. Unavailable entities can be assigned if they exist; missing/non-light entities are rejected. Light moves affect only Lighting Curves entries. Points and lights preserve the other schedule options. These are full replacements with no revision check: read before editing; concurrent saves overwrite earlier saves.

REST examples (use your HA URL and token):

```sh
curl -s -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$HA_URL/api/services/lighting_curves/create_schedule?return_response" \
  -d '{"name":"Desk"}'
# {"changed_states":[],"service_response":{"entry_id":"…"}}

curl -s -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$HA_URL/api/services/lighting_curves/set_points" \
  -d '{"entry_id":"…","points":[{"time":"06:00","brightness":80,"color_temp_kelvin":4000},{"time":"sunset+30","brightness":20,"rgb_color":[255,120,0]}]}'

curl -s -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$HA_URL/api/services/lighting_curves/set_lights" \
  -d '{"entry_id":"…","lights":["light.desk"]}'

curl -s -H "Authorization: Bearer $HA_TOKEN" -H 'Content-Type: application/json' \
  -X POST "$HA_URL/api/services/lighting_curves/get_schedules?return_response" -d '{}'
```

In an HA script, use `response_variable` for `get_schedules` (and optionally creation):

```yaml
- action: lighting_curves.get_schedules
  data: {}
  response_variable: curves
# curves.schedules is the list
```

Each schedule has this shape (graph arrays shortened):

```json
{
  "entry_id": "…", "title": "Desk",
  "points": [{"time":"06:00","brightness":80,"color_temp_kelvin":4000}],
  "times": [360], "sunrise": 360, "sunset": 1080, "now": 720,
  "lights": ["light.desk"], "curve_active": {"light.desk": true},
  "target": {"brightness":80,"color_temp_kelvin":4000},
  "options": {"interval":90,"transition":45,"initial_transition":1,"manual_control_mode":"pause_all","manual_control_timeout":0,"separate_turn_on":false,"turn_on_floor":5},
  "preview": {"active":false,"expires_at":null},
  "samples": [{"brightness":80,"color_temp_kelvin":4000}], "sample_step": 10
}
```

`times` aligns with returned `points`; times, sun times and `now` are minutes since local midnight in HA's timezone. `samples` contains 145 settings at minutes 0, 10, …, 1440. `target` is the current setting (possibly null during startup), not a device's actual state. Active preview has an ISO 8601 UTC `expires_at`; it is the next rollback attempt, extended if restoration fails.

## WebSocket commands

Use HA's authenticated `/api/websocket`. Every request needs a unique integer `id`; the standard success envelope is `{"id":1,"type":"result","success":true,"result":…}`. Writes/preview require an administrator; reads/sample do not.

| `type` | Additional request fields | `result` |
| --- | --- | --- |
| `lighting_curves/schedules` | None | Schedule array above (no `schedules` wrapper) |
| `lighting_curves/create` | `name` | `{ "entry_id": "…" }` |
| `lighting_curves/save` | `entry_id`, `points` | `null`; full replacement |
| `lighting_curves/set_lights` | `entry_id`, `lights` | `null`; full replacement and moves, as the action |
| `lighting_curves/sample` | `points` | `points`, `times`, `samples`, `sample_step`, `now`, `sunrise`, `sunset`; no mutation |
| `lighting_curves/preview` | `entry_id`, `setting` | `null`; applies a setting to real lights |
| `lighting_curves/preview_end` | `entry_id` | `null`; restores preview participants |

```json
{"id":1,"type":"lighting_curves/schedules"}
{"id":2,"type":"lighting_curves/sample","points":[{"time":"sunrise+0","brightness":100,"color_temp_kelvin":5000}]}
{"id":3,"type":"lighting_curves/save","entry_id":"…","points":[{"time":"06:00","brightness":80,"color_temp_kelvin":4000}]}
{"id":4,"type":"lighting_curves/preview","entry_id":"…","setting":{"brightness":20,"rgb_color":[255,0,0]}}
{"id":5,"type":"lighting_curves/preview_end","entry_id":"…"}
```

Preview **turns on off lights** and changes real brightness/colour. The first call snapshots participants and their on/off intent. Each call resets a 600-second timeout. Interval updates leave participants alone. Ending/expiry restores originally-on lights to the **current curve**, including lights since removed from the schedule; originally-off lights turn off again. Newly assigned lights are not added to an existing preview. Failed restores remain active and retry after 600 seconds. Graceful unload attempts rollback; abrupt HA termination loses the in-memory snapshot. Read `preview` to discover backend state; the card refreshes it every minute.

## Point grammar and daily timing

```json
{"time":"sunset-45","brightness":35,"color_temp_kelvin":2700}
```

- `time`: `HH:MM` (00:00–23:59; `6:00` normalizes to `06:00`), or `sunrise±MIN` / `sunset±MIN` with an explicit sign and 1–3 decimal offset digits. Use `sunrise+0`, not `sunrise`.
- `brightness`: integer **1–100** (not HA's 0–255 scale).
- Exactly one colour: integer `color_temp_kelvin` **1000–10000**, or `rgb_color` of exactly three integers **0–255**.
- Unknown fields and duplicate anchors are rejected, including `6:00` / `06:00` and `sunset+0` / `sunset-0`. A `setting` is the same grammar without `time`. One point is a constant day.

Point order uses a reference day with sunrise 06:00 and sunset 18:00. Each day, sun points move with the sun but clamp between their neighbouring fixed clock points, including neighbours across midnight. Example: a `sunset+45` point before `20:30` cannot drift past `20:30` in summer. With no fixed clocks, sun points wrap freely. The API returns effective minutes in `times` so agents can see clamping.

Seasonal collisions are allowed: zero-length fades are skipped and the last point at that resolved instant takes over. Ties use reference-day order, except a point carried forward across midnight comes first and one carried backward comes last. The last point fades towards the first across midnight (23:00 → 01:00 is a two-hour fade). Brightness and white temperature fade linearly; mixed white/RGB fades use RGB. Device capability conversion may clamp white temperature further to the light's supported range.

Timing follows HA's local wall clock. DST forward skips minutes; DST backward revisits them. Polar day/night without sun events falls back to sunrise 06:00 / sunset 18:00.

## Five control options

Set these through the integration's native options flow; actions/WS preserve them.

| Option | Default | Meaning |
| --- | --- | --- |
| `interval` | 90 s | Reconcile on lights; minimum 1 s |
| `transition` | 45 s | Fade for regular curve updates; minimum 0 |
| `initial_transition` | 1 s | Fade on turn-on / restoration; minimum 0; caller's explicit transition wins on intercepted turn-on |
| `manual_control_mode` | `pause_all` | Manual change pauses brightness and colour; `pause_changed` pauses only changed axes |
| `manual_control_timeout` | 0 s | Resume after this delay; 0 disables the timer; off/on also resumes |
| `separate_turn_on` | `false` | Turn-on stays plain (brightness/colour fields dropped); the curve applies after the light reports on, with `initial_transition` |
| `turn_on_floor` | 5 % | With `separate_turn_on`: `turn_off` (and `toggle` of an on light) first sets this brightness, unless the light is manually paused or in preview |

Each assigned light has a **Light name Curve Active** switch on its schedule device. It is off
whenever any axis is manually paused, including in `pause_changed` mode. Use `switch.turn_on` to clear
that light's pauses and timeout and apply the current target with `transition` if the light is on;
it never turns an off light on. Use `switch.turn_off` to hold both axes without a timeout, including
after restart. A light's off→on cycle still resumes the curve. These standard switch actions replace
an Adaptive Lighting manual-control reset in automations. `curve_active` in schedules/get_schedules
and diagnostics maps each light ID to this boolean state.

Saving points/assignments reconciles currently-on lights; it does not turn previously-off lights on. An effect or flash turn-on of an off light passes through and pauses both axes until the light next turns off and on (or the manual-control timeout ends). On a light that is already on, an effect counts as a colour change for manual control; a flash does not pause anything. An unavailable interceptor falls back to adaptation after turn-on, which can produce a flash. Another controller (including Adaptive Lighting) on the same lights can conflict; remove those assignments yourself.

## Errors and diagnostics

Action validation raises translated HA `ServiceValidationError`. WebSocket errors use `{"id":1,"type":"result","success":false,"error":{"code":"…","message":"…"}}`.

| Condition | Action translation key / WS code |
| --- | --- |
| Empty name / duplicate name | `empty_name` / `exists` |
| Bad action fields or missing required field | `invalid_input` |
| Invalid points | action `invalid_points`; WS `invalid_format` (HA schema validation) |
| Missing/non-light assignment | `invalid_lights`; malformed WS fields may be `invalid_format` |
| Unknown, wrong-domain or unloaded entry | `not_found` |
| Non-admin mutation | HA authorization error / `unauthorized` |
| Preview without lights | WS `no_lights` |
| Preview service failure / unavailable participant / failed restoration | WS `unknown_error` with HA error message; rollback remains armed |

Unknown entries are never silently created. An unavailable assigned light is skipped during ordinary adaptation, but failed preview restoration is reported and retried. The download is not redacted: it holds schedule names (often room names) and light entity IDs, as most HA diagnostics do. Check it before you attach it to a public issue. Get a config-entry diagnostic download from the integration's menu for points, resolved/sun times, current target/options, light state/availability, manual pauses/timeouts, preview expiry and interceptor/fallback status.
