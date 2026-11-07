"""Plain-assert capability, interceptor, manual and preview regression checks."""
import asyncio
from copy import deepcopy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")
from homeassistant.core import Context, Event, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from custom_components.lighting_curves import Curve, async_unload_entry
from custom_components.lighting_curves.config_flow import LightingCurvesOptionsFlow
from custom_components.lighting_curves.light_control import DEFAULTS, is_ours, light_data, own_context
from homeassistant.util.ulid import ulid_to_bytes

context = own_context()
assert len(ulid_to_bytes(context.id)) == 16 and is_ours(context)
assert is_ours(Context(parent_id=context.id)) and not is_ours(Context())

CT = {"supported_color_modes": ["color_temp"], "min_color_temp_kelvin": 2200, "max_color_temp_kelvin": 5000,
      "supported_features": 32}
RGB = {"supported_color_modes": ["rgb"], "supported_features": 32}
WHITE = {"brightness": 60, "color_temp_kelvin": 6500}
RED = {"brightness": 20, "rgb_color": [255, 0, 0]}
assert light_data(State("light.ct", "on", CT), WHITE) == {"brightness": 153, "color_temp_kelvin": 5000}
assert light_data(State("light.ct", "on", CT), {**WHITE, "color_temp_kelvin": 1000})["color_temp_kelvin"] == 2200
assert light_data(State("light.rgb", "on", RGB), RED) == {"brightness": 51, "rgb_color": [255, 0, 0]}
assert "rgb_color" not in light_data(State("light.ct", "on", CT), RED)
assert 2200 <= light_data(State("light.ct", "on", CT), RED)["color_temp_kelvin"] <= 5000
assert "rgb_color" in light_data(State("light.rgb", "on", RGB), WHITE)
assert light_data(State("light.onoff", "on", {"supported_color_modes": ["onoff"]}), WHITE) == {}
assert light_data(State("light.dim", "on", {"supported_color_modes": ["brightness"]}), WHITE) == {"brightness": 153}
assert light_data(State("light.ct", "on", CT), WHITE, {"brightness", "color"}) == {}


async def main():
    hass = HomeAssistant("/tmp/lighting-curves-test")
    hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
    calls = []
    fail = set()

    def schema(data):
        return {"entity_id": data["entity_id"], "params": {k: v for k, v in data.items() if k != "entity_id"}}

    async def handler(call):
        entities = call.data["entity_id"]
        if isinstance(entities, str):
            entities = [entities]
        for entity_id in entities:
            params = call.data["params"]
            calls.append((entity_id, call.service, dict(params)))
            if entity_id in fail:
                raise HomeAssistantError("test failure")
            old = hass.states.get(entity_id)
            new = "off" if call.service == "turn_off" or (call.service == "toggle" and old.state == "on") else "on"
            hass.states.async_set(entity_id, new, {**old.attributes, **params}, context=call.context)

    for service in ("turn_on", "toggle", "turn_off"):
        hass.services.async_register("light", service, handler, schema)
    for entity_id, attrs, state in (("light.ct", CT, "on"), ("light.rgb", RGB, "off"), ("light.other", CT, "off")):
        hass.states.async_set(entity_id, state, attrs)
    await hass.async_block_till_done()
    entry = SimpleNamespace(entry_id="test", title="Test", options={**DEFAULTS, "points": [], "lights": ["light.ct", "light.rgb"]})
    curve = Curve(hass, entry)
    entry.runtime_data = curve
    curve.calculate_target = lambda: setattr(curve, "target", deepcopy(WHITE))
    await curve.async_start()
    await hass.async_block_till_done()
    assert hass.states.get("light.ct").attributes["brightness"] == 153  # already-on reconciled
    assert hass.states.get("light.rgb").state == "off"
    calls.clear()
    await curve.async_update(force=True)
    assert not calls, "matching steady target must not send again"

    # Mixed target routing preserves unrelated lights and rewrites off LC lights before turn-on.
    await hass.services.async_call("light", "turn_on", {"entity_id": ["light.rgb", "light.other"], "brightness": 12}, blocking=True)
    await hass.async_block_till_done()
    rgb_calls = [data for entity, service, data in calls if entity == "light.rgb"]
    assert len(rgb_calls) == 1 and rgb_calls[0]["brightness"] == 153 and "rgb_color" in rgb_calls[0]
    assert hass.states.get("light.other").attributes["brightness"] == 12

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.ct", "brightness": 10}, blocking=True)
    await hass.async_block_till_done()
    assert curve.manual["light.ct"] == {"brightness", "color"}
    calls.clear()
    await curve.async_update(force=True)
    assert not calls
    curve.resume("light.ct")
    entry.options["manual_control_mode"] = "pause_changed"
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.ct", "brightness": 11}, blocking=True)
    await hass.async_block_till_done()
    assert curve.manual["light.ct"] == {"brightness"}
    await curve.async_update(force=True)
    assert hass.states.get("light.ct").attributes["brightness"] == 11
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.ct"}, blocking=True)
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.ct"}, blocking=True)
    await hass.async_block_till_done()
    assert "light.ct" not in curve.manual and hass.states.get("light.ct").attributes["brightness"] == 153

    # Preview snapshots participants, blocks applies, and restores original off/on intent.
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.rgb"}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("light", "turn_off", {"entity_id": "light.other"}, blocking=True)
    await hass.async_block_till_done()
    await curve.async_preview(RED)
    await hass.async_block_till_done()
    assert curve.preview == {"light.ct": True, "light.rgb": False} and curve._preview_timeout
    entry.options["lights"] = ["light.rgb", "light.other"]  # originally-on light removed; other added
    calls.clear()
    await curve.async_update(force=True)
    assert not calls
    await curve.async_end_preview()
    await hass.async_block_till_done()
    assert hass.states.get("light.ct").attributes["brightness"] == 153
    assert hass.states.get("light.rgb").state == "off" and hass.states.get("light.other").state == "off"
    assert not any(entity == "light.other" for entity, _, _ in calls)

    fail.add("light.rgb")
    try:
        await curve.async_preview(RED)
    except HomeAssistantError:
        pass
    else:
        raise AssertionError("preview should fail")
    assert curve.preview is not None and curve._preview_timeout, "failed preview must retain armed rollback"
    try:
        await curve.async_end_preview()
    except HomeAssistantError:
        pass
    assert curve.preview == {"light.rgb": False} and curve._preview_timeout
    fail.clear()
    with patch.object(hass.config_entries, "async_unload_platforms", return_value=True):
        assert await async_unload_entry(hass, entry)
    assert curve.preview is None and hass.states.get("light.rgb").state == "off"
    curve.async_stop()
    assert hass.services._services["light"]["turn_on"].job.target is handler

    # Missing private hook falls back to state-change adaptation, with initial transition.
    with patch("custom_components.lighting_curves.light_control.setup_service_call_interceptor", side_effect=RuntimeError("changed API")):
        await curve.async_start()
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.rgb"}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("light.rgb").attributes["brightness"] == 153
    assert calls[-1][2]["transition"] == 1
    # Registry renames/removals preserve options and update preview participant IDs.
    def update(updated, **kwargs):
        for key, value in kwargs.items():
            setattr(updated, key, value)
    hass.config_entries.async_update_entry = update
    curve.manual["light.rgb"] = {"color"}
    curve._restart_manual_timeout("light.rgb", 30)
    deadline = curve._manual_timeouts["light.rgb"][1]
    await curve._registry_changed(Event("entity_registry_updated", {
        "action": "update", "entity_id": "light.renamed", "changes": {"entity_id": "light.rgb"},
    }))
    assert entry.options["lights"] == ["light.renamed", "light.other"]
    assert curve.manual["light.renamed"] == {"color"} and "light.rgb" not in curve.manual
    assert abs(curve._manual_timeouts["light.renamed"][1] - deadline) < .1
    await curve._registry_changed(Event("entity_registry_updated", {"action": "remove", "entity_id": "light.renamed"}))
    assert entry.options["lights"] == ["light.other"] and "light.renamed" not in curve.manual
    curve.async_stop()

    # A timeout resumes adaptation without requiring the light to turn off.
    entry.options["lights"] = ["light.ct"]
    entry.options["manual_control_timeout"] = 30
    entry.options["manual_control_mode"] = "pause_all"
    hass.states.async_set("light.ct", "on", {**CT, "brightness": 11})
    curve.calculate_target()
    with patch("custom_components.lighting_curves.async_call_later") as later:
        curve._service_called(Event("call_service", {"domain": "light", "service": "turn_on",
            "service_data": {"entity_id": "light.ct", "brightness": 11}}, context=Context()))
        assert curve.manual["light.ct"] == {"brightness", "color"} and later.call_args.args[1] == 30
        await later.call_args.args[2](None)
    assert "light.ct" not in curve.manual and hass.states.get("light.ct").attributes["brightness"] == 153

    # Multiple schedules share hooks. Unloading either order leaves no stale active hook.
    await curve.async_start()
    second_entry = SimpleNamespace(entry_id="second", title="Second", options={**DEFAULTS, "points": [], "lights": []})
    second = Curve(hass, second_entry)
    second.calculate_target = lambda: setattr(second, "target", deepcopy(WHITE))
    await second.async_start()
    curve.async_stop()
    assert hass.services._services["light"]["turn_on"].job.target is not handler
    second.async_stop()
    assert hass.services._services["light"]["turn_on"].job.target is handler

    # The options form uses native selectors and preserves points/lights on save.
    hass.config_entries.async_get_known_entry = lambda _: entry
    flow = LightingCurvesOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    form = await flow.async_step_init()
    assert len(form["data_schema"].schema) == 5
    result = await flow.async_step_init({**DEFAULTS, "transition": 20})
    assert result["data"]["transition"] == 20 and result["data"]["lights"] == ["light.ct"]
    print("ok: capabilities, startup, steady-state skip, mixed interception, manual control, preview rollback/unload, fallback")


asyncio.run(main())
