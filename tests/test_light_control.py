"""Plain-assert capability, interceptor, manual and preview regression checks."""
import asyncio
from copy import deepcopy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")
from homeassistant.core import Context, Event, HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from custom_components.lighting_curves import Curve, async_migrate_entry, async_setup_entry, async_unload_entry
from custom_components.lighting_curves.config_flow import LightingCurvesOptionsFlow
from custom_components.lighting_curves.light_control import DEFAULTS, is_ours, light_data, matches, own_context, with_transition
from homeassistant.util.ulid import ulid_to_bytes

def test_context_ownership():
    context = own_context()
    assert len(ulid_to_bytes(context.id)) == 16 and is_ours(context)
    assert not is_ours(Context(parent_id=context.id)) and not is_ours(Context())

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


def test_round_trip_tolerances():
    for actual, expected in ((2702, 2700), (2197, 2200)):
        assert matches(State("light.ct", "on", {"color_temp_kelvin": actual}), {"color_temp_kelvin": expected})
    assert matches(State("light.rgb", "on", {"rgb_color": [252, 3, 1], "brightness": 152}),
                   {"rgb_color": [255, 0, 0], "brightness": 153})
    assert not matches(State("light.ct", "on", {"color_temp_kelvin": 2800}), {"color_temp_kelvin": 2700})
    assert not matches(State("light.rgb", "on", {"rgb_color": [251, 0, 0]}), {"rgb_color": [255, 0, 0]})
    assert not matches(State("light.rgb", "on", {"brightness": 151}), {"brightness": 153})
    assert with_transition({}, 600) == {"transition": 600}


async def test_remaining_group_context(hass, calls, service_calls):
    for entity in ("light.ct", "light.rgb"):
        hass.states.async_set(entity, "off", hass.states.get(entity).attributes)
    hass.states.async_set("light.group", "off", {"entity_id": ["light.rgb"]})
    await hass.async_block_till_done()
    calls.clear()
    service_calls.clear()
    parent = Context()
    await hass.services.async_call("light", "turn_on", {
        "entity_id": ["light.ct", "light.group"], "brightness": 12,
    }, blocking=True, context=parent)
    await hass.async_block_till_done()
    group_context = next(context for entities, _, _, context in service_calls if entities == ["light.group"])
    assert group_context.parent_id == parent.id and not is_ours(group_context)
    rgb_calls = [data for entity, _, data in calls if entity == "light.rgb"]
    assert len(rgb_calls) == 1 and rgb_calls[0]["brightness"] == 153 and "rgb_color" in rgb_calls[0]


async def test_area_targets_batched(hass, service_calls):
    hass.states.async_set("light.rgb", "off", RGB)
    hass.states.async_set("light.other_two", "off", CT)
    hass.states.async_set("sensor.room", "12")
    await hass.async_block_till_done()
    service_calls.clear()
    from custom_components.lighting_curves.hass_utils import target_entities

    def area_targets(hass, data):
        if data.get("area_id") == "room":
            return {"sensor.room", "light.rgb", "light.other", "light.other_two"}
        return target_entities(hass, data)

    with patch("custom_components.lighting_curves.light_control.target_entities", side_effect=area_targets), \
         patch("custom_components.lighting_curves.target_entities", side_effect=area_targets):
        await hass.services.async_call("light", "turn_on", {"area_id": "room", "brightness": 12}, blocking=True)
        await hass.async_block_till_done()
    assert len(service_calls) == 2
    assert any(entities == ["light.other", "light.other_two"] and params == {"brightness": 12}
               for entities, _, params, _ in service_calls)
    assert all(entity.startswith("light.") for entities, _, _, _ in service_calls for entity in entities)


async def test_effect_flash_pass_through(hass, service_calls, curve):
    for params in ({"effect": "colorloop", "brightness": 12}, {"flash": "short", "brightness": 12}):
        hass.states.async_set("light.rgb", "off", RGB)
        await hass.async_block_till_done()
        service_calls.clear()
        context = Context()
        await hass.services.async_call("light", "turn_on", {"entity_id": "light.rgb", **params},
                                       blocking=True, context=context)
        await hass.async_block_till_done()
        assert service_calls == [(["light.rgb"], "turn_on", params, context)]
        assert curve.manual["light.rgb"] == {"brightness", "color"}
        service_calls.clear()
        await curve.async_update(force=True)
        await hass.async_block_till_done()
        assert not service_calls, "the curve must not overwrite an effect or flash"
        curve.resume("light.rgb")


async def test_caller_transition(hass, service_calls):
    hass.states.async_set("light.rgb", "off", RGB)
    await hass.async_block_till_done()
    service_calls.clear()
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.rgb", "transition": 600}, blocking=True)
    await hass.async_block_till_done()
    assert len(service_calls) == 1 and service_calls[0][2]["transition"] == 600


async def test_busy_tick_skipped(curve):
    with patch.object(curve, "async_update", new_callable=AsyncMock) as update:
        async with curve._apply_lock:
            await asyncio.wait_for(curve._async_tick(None), timeout=1)
        update.assert_not_awaited()
        await curve._async_tick(None)
        update.assert_awaited_once_with(force=True)


async def test_migration_duplicate_ownership():
    al = SimpleNamespace(domain="adaptive_lighting", options={"lights": ["light.shared", "light.new"]})
    al_second = SimpleNamespace(domain="adaptive_lighting", options={"lights": ["light.new", "light.second"]})
    direct = SimpleNamespace(entry_id="direct", version=1, title="Direct", data={}, options={
        "points": [], "lights": ["light.shared"],
    })
    first = SimpleNamespace(entry_id="first", version=1, title="First", data={"switch": "switch.al"}, options={"points": []})
    second = SimpleNamespace(entry_id="second", version=1, title="Second", data={"switch": "switch.al_second"}, options={"points": []})
    original = deepcopy(al.options)
    original_second = deepcopy(al_second.options)

    def update(entry, **changes):
        assert entry is not al and entry is not al_second
        for key, value in changes.items():
            setattr(entry, key, value)

    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_entries=lambda domain: [direct, first, second],
        async_get_entry=lambda key: al if key == "al" else al_second, async_update_entry=update,
    ))
    registry = SimpleNamespace(async_get=lambda switch: SimpleNamespace(config_entry_id=switch.removeprefix("switch.")))
    with patch("custom_components.lighting_curves.er.async_get", return_value=registry), \
         patch("custom_components.lighting_curves._LOGGER.warning") as warning:
        assert await async_migrate_entry(hass, first)
        assert first.options["lights"] == ["light.new"]
        assert await async_migrate_entry(hass, second)
        assert second.options["lights"] == ["light.second"]
        assert await async_migrate_entry(hass, direct)
        assert direct.options["lights"] == ["light.shared"]
        assert warning.call_count == 2
    assert al.options == original and al_second.options == original_second


async def test_setup_cleanup_and_overlap(hass):
    callbacks = []
    entry = SimpleNamespace(entry_id="lifecycle", title="Lifecycle", options={
        **DEFAULTS, "points": [{"time": "06:00", **WHITE}], "lights": ["light.rgb", "light.ct"],
    }, async_on_unload=callbacks.append, add_update_listener=lambda _: lambda: None)
    al = SimpleNamespace(options={"lights": ["light.rgb", "light.ct"]})
    original = deepcopy(al.options)
    hass.config_entries.async_entries = lambda _: [al, al]
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    with patch.object(Curve, "async_update", side_effect=HomeAssistantError("start failed")), \
         patch("custom_components.lighting_curves._LOGGER.warning") as warning:
        try:
            await async_setup_entry(hass, entry)
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("start should fail")
        assert callbacks == [entry.runtime_data.async_stop]
        assert warning.call_count == 1
        assert "light.ct, light.rgb" in warning.call_args.args
    callbacks.pop()()  # HA runs registered cleanup after failed setup.
    assert not entry.runtime_data._listeners and entry.runtime_data._interval is None
    assert entry.entry_id not in hass.data["lighting_curves"]["curves"]
    await async_setup_entry(hass, entry)
    curve = entry.runtime_data
    curve.preview = {"light.rgb": True}
    curve.async_restart_preview_timeout()
    hass.states.async_set("light.rgb", "unavailable", RGB)
    await hass.async_block_till_done()
    interval, retry = curve._interval, curve._preview_timeout

    async def unload_platforms(*_):
        while callbacks:
            callbacks.pop()()  # Model HA config-entry unload callbacks.
        return True

    with patch.object(hass.config_entries, "async_unload_platforms", side_effect=unload_platforms) as unload, \
         patch.object(curve, "async_end_preview", wraps=curve.async_end_preview) as rollback:
        assert await async_unload_entry(hass, entry)
        rollback.assert_awaited_once()
        unload.assert_awaited_once()
    assert interval and retry and curve._interval is None and curve._preview_timeout is None
    assert not curve._listeners and not curve._manual_timeouts
    assert entry.entry_id not in hass.data["lighting_curves"]["curves"]
    assert not hass.data["lighting_curves"]["hooks"]
    assert al.options == original


async def main():
    hass = HomeAssistant("/tmp/lighting-curves-test")
    hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
    calls = []
    service_calls = []
    fail = set()

    def schema(data):
        targets = {"entity_id", "area_id", "device_id"}
        return {**{k: v for k, v in data.items() if k in targets},
                "params": {k: v for k, v in data.items() if k not in targets}}

    async def handler(call):
        entities = call.data["entity_id"]
        if isinstance(entities, str):
            entities = [entities]
        service_calls.append((list(entities), call.service, dict(call.data["params"]), call.context))
        for entity_id in entities:
            params = call.data["params"]
            calls.append((entity_id, call.service, dict(params)))
            if entity_id in fail:
                raise HomeAssistantError("test failure")
            old = hass.states.get(entity_id)
            if members := old.attributes.get("entity_id"):
                await hass.services.async_call("light", call.service, {"entity_id": members, **params},
                                               blocking=True, context=call.context)
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

    await test_remaining_group_context(hass, calls, service_calls)
    await test_area_targets_batched(hass, service_calls)
    await test_effect_flash_pass_through(hass, service_calls, curve)
    await test_caller_transition(hass, service_calls)
    await test_busy_tick_skipped(curve)
    hass.states.async_set("light.rgb", "off", RGB)
    await hass.async_block_till_done()
    calls.clear()

    # Mixed target routing preserves unrelated lights and rewrites off LC lights before turn-on.
    await hass.services.async_call("light", "turn_on", {"entity_id": ["light.rgb", "light.other"], "brightness": 12}, blocking=True)
    await hass.async_block_till_done()
    rgb_calls = [data for entity, service, data in calls if entity == "light.rgb"]
    assert len(rgb_calls) == 1 and rgb_calls[0]["brightness"] == 153 and "rgb_color" in rgb_calls[0]
    assert hass.states.get("light.other").attributes["brightness"] == 12

    await hass.services.async_call("light", "turn_on", {"entity_id": "light.ct", "brightness": 10}, blocking=True)
    await hass.async_block_till_done()
    assert curve.manual["light.ct"] == {"brightness", "color"}
    # A reconnect is not an off->on cycle and must retain the user's override.
    await curve._async_light_changed(Event("state_changed", {
        "old_state": State("light.ct", "unavailable", CT),
        "new_state": hass.states.get("light.ct"),
    }))
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
    assert curve.preview_state()["active"] and curve.preview_state()["expires_at"]
    entry.options["lights"] = ["light.rgb", "light.other"]  # originally-on light removed; other added
    calls.clear()
    await curve.async_update(force=True)
    assert not calls
    await curve.async_end_preview()
    await hass.async_block_till_done()
    assert hass.states.get("light.ct").attributes["brightness"] == 153
    assert curve.preview_state() == {"active": False, "expires_at": None}
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
    # M1: effect/flash passthrough must also work without an installed interceptor.
    await test_effect_flash_pass_through(hass, service_calls, curve)
    # M2: an effect call that left the light off must not poison a later plain
    # turn-on from the same script context, even when no control fields are sent.
    hass.states.async_set("light.rgb", "off", RGB)
    await hass.async_block_till_done()
    context = Context()
    curve._service_called(Event("call_service", {"domain": "light", "service": "turn_on",
        "service_data": {"entity_id": "light.rgb", "effect": "rainbow"}}, context=context))
    assert curve.passthrough["light.rgb"] == context.id
    await hass.services.async_call("light", "turn_on", {"entity_id": "light.rgb"}, blocking=True, context=context)
    await hass.async_block_till_done()
    assert "light.rgb" not in curve.manual and "light.rgb" not in curve.passthrough
    assert hass.states.get("light.rgb").attributes["brightness"] == 153
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
    hass.config_entries.async_entries = lambda _: [entry]
    flow = LightingCurvesOptionsFlow()
    flow.hass, flow.handler = hass, entry.entry_id
    form = await flow.async_step_init()
    assert len(form["data_schema"].schema) == 6
    lights_key = next(k for k in form["data_schema"].schema if k == "lights")
    assert lights_key.description["suggested_value"] == ["light.ct"]  # existing lights only
    result = await flow.async_step_init({**DEFAULTS, "lights": ["light.ct"], "transition": 20})
    assert result["data"]["transition"] == 20 and result["data"]["lights"] == ["light.ct"]
    result = await flow.async_step_init({**DEFAULTS})  # selector emptied: the key is left out
    assert result["data"]["lights"] == []
    entry.options = {**entry.options, "lights": ["light.ct"]}
    await test_migration_duplicate_ownership()
    await test_setup_cleanup_and_overlap(hass)
    print("ok: capabilities, contexts, batched targets, effect/flash, transitions, tolerances, busy ticks, migration ownership, setup cleanup, manual control, preview rollback/unload, fallback")


test_context_ownership()
test_round_trip_tolerances()
asyncio.run(main())
