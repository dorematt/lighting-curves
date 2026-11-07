"""Standalone light control; interception adapted from Adaptive Lighting.

Source: https://github.com/basnijholt/adaptive-lighting, Apache-2.0,
copyright its authors/contributors. Modified for Lighting Curves: only the
per-light routing/rewrite from switch.py _service_interceptor_turn_on_handler
and _service_interceptor_turn_on_single_light_handler. Capabilities are LC code.
"""
from __future__ import annotations

import logging

from homeassistant.core import Context
from homeassistant.util import color

from .hass_utils import setup_service_call_interceptor, target_entities

DOMAIN = "lighting_curves"
DEFAULTS = {
    "interval": 90,
    "transition": 45,
    "initial_transition": 1,
    "manual_control_mode": "pause_all",
    "manual_control_timeout": 0,
}
BRIGHTNESS_KEYS = {"brightness", "brightness_pct", "brightness_step", "brightness_step_pct", "white"}
COLOR_KEYS = {"color_temp_kelvin", "color_temp", "kelvin", "rgb_color", "rgbw_color", "rgbww_color",
              "xy_color", "hs_color", "color_name", "effect"}
CONTROL_KEYS = BRIGHTNESS_KEYS | COLOR_KEYS | {"profile", "flash"}
_LOGGER = logging.getLogger(__name__)


def own_context(parent=None):
    context = Context(parent_id=parent.id if parent else None)
    # Preserve a valid ULID for HA's recorder, with a recognizable random-section marker.
    context.id = context.id[:10] + "1C1C1C1C" + context.id[18:]
    return context


def is_ours(context):
    return context.id[10:18] == "1C1C1C1C"


def light_data(state, setting, paused=()):
    """Build supported light arguments, converting colour only when necessary."""
    attrs = state.attributes
    modes = set(attrs.get("supported_color_modes", []))
    data = {}
    if "brightness" not in paused and modes - {"onoff", "unknown"}:
        data["brightness"] = round(setting["brightness"] * 255 / 100)
    if "color" in paused:
        return data
    rgb_modes = modes & {"rgb", "rgbw", "rgbww", "hs", "xy"}
    rgb = setting.get("rgb_color")
    if rgb is not None and rgb_modes:
        # HA's light service converts RGB to the device's native colour mode.
        data["rgb_color"] = list(rgb)
    elif "color_temp" in modes:
        low = attrs.get("min_color_temp_kelvin", 2000)
        high = attrs.get("max_color_temp_kelvin", 6500)
        kelvin = setting.get("color_temp_kelvin")
        if kelvin is None:
            # ponytail: nearest white on a 50 K grid; use finer steps if devices need it.
            kelvin = min((*range(low, high, 50), high), key=lambda k: sum(
                (a - b) ** 2 for a, b in zip(rgb, color.color_temperature_to_rgb(k))
            ))
        data["color_temp_kelvin"] = max(low, min(high, kelvin))
    elif rgb_modes:
        data["rgb_color"] = [round(c) for c in color.color_temperature_to_rgb(setting["color_temp_kelvin"])]
    return data


def matches(state, data):
    """Compare device state with supported target arguments (allow rounding)."""
    for key, value in data.items():
        actual = state.attributes.get(key)
        if actual is None:
            return False
        if isinstance(value, (list, tuple)):
            if len(actual) != len(value) or any(abs(a - b) > 3 for a, b in zip(actual, value)):
                return False
        elif key == "color_temp_kelvin":
            if abs(1_000_000 / actual - 1_000_000 / value) > 1:
                return False
        elif abs(actual - value) > 1:
            return False
    return True


def with_transition(data, seconds):
    return {**data, "transition": seconds}


def register_curve(curve):
    """One hook shared by all entries; uninstall it only after the last unload."""
    hass = curve.hass
    shared = hass.data.setdefault(DOMAIN, {"curves": {}, "hooks": [], "warned": False})
    shared["curves"][curve.entry.entry_id] = curve
    if len(shared["curves"]) > 1:
        return

    async def intercept(call, data):
        if is_ours(call.context):
            return
        original = dict(data.get("params", {}))
        if "effect" in original or "flash" in original:
            return
        if original.get("brightness") == 0 or original.get("white") == 0:
            return  # HA treats brightness zero as turn_off.
        entities = sorted(entity for entity in target_entities(hass, data) if entity.startswith("light."))
        rewrites = {}
        for entity_id in entities:
            state = hass.states.get(entity_id)
            owner = next((c for c in shared["curves"].values() if entity_id in c.lights()), None)
            if not owner or not state or state.state != "off" or owner.in_preview(entity_id):
                continue
            owner.resume(entity_id)
            owner.calculate_target()
            rewrites[entity_id] = with_transition(
                light_data(state, owner.target), original.get("transition", owner.option("initial_transition"))
            )
            owner.intercepted[entity_id] = call.context.id
        if not rewrites:
            return
        # Like AL, keep one target in the original call and route the rest
        # separately. Each light gets its own capability-filtered curve target.
        first = next(iter(rewrites))
        for entity_id, rewrite in rewrites.items():
            params = {k: v for k, v in original.items() if k not in CONTROL_KEYS}
            params.update(rewrite)
            if entity_id == first:
                data.clear()
                data.update(entity_id=[entity_id], params=params)
            else:
                try:
                    await hass.services.async_call(
                        "light", call.service, {"entity_id": entity_id, **params},
                        blocking=True, context=own_context(call.context),
                    )
                except Exception:
                    # Do not replay a mixed toggle after some targets already succeeded.
                    _LOGGER.exception("Could not forward %s for %s", call.service, entity_id)
        remaining = [entity_id for entity_id in entities if entity_id not in rewrites]
        if remaining:
            try:
                await hass.services.async_call(
                    "light", call.service, {"entity_id": remaining, **original},
                    blocking=True, context=Context(parent_id=call.context.id),
                )
            except Exception:
                _LOGGER.exception("Could not forward %s for %s", call.service, remaining)

    try:
        for service in ("turn_on", "toggle"):
            shared["hooks"].append(setup_service_call_interceptor(hass, "light", service, intercept))
    except (RuntimeError, AttributeError, TypeError):
        while shared["hooks"]:
            shared["hooks"].pop()()
        if not shared["warned"]:
            _LOGGER.warning("Light turn-on interceptor unavailable; using state-change adaptation with initial_transition")
            shared["warned"] = True


def unregister_curve(curve):
    shared = curve.hass.data[DOMAIN]
    shared["curves"].pop(curve.entry.entry_id, None)
    if not shared["curves"]:
        while shared["hooks"]:
            shared["hooks"].pop()()
