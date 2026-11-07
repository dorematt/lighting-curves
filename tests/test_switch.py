"""Plain-assert Curve Active lifecycle and light-control checks in an HA image."""
import asyncio
from types import SimpleNamespace
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, ".")
from homeassistant.core import Context, Event, HomeAssistant, State, callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import area_registry as ar, entity_registry as er
from custom_components.lighting_curves import Curve, _async_options_updated, async_setup_entry
from custom_components.lighting_curves.api import schedules, set_lights
from custom_components.lighting_curves.diagnostics import async_get_config_entry_diagnostics
from custom_components.lighting_curves.light_control import DEFAULTS
from custom_components.lighting_curves.switch import CurveActiveSwitch, async_setup_entry as setup_switches


async def main():
    hass = HomeAssistant("/tmp/lighting-curves-switch-test")
    setting = {"brightness": 60, "color_temp_kelvin": 4000}
    attrs = {"friendly_name": "Demo Lamp", "supported_color_modes": ["color_temp"],
             "min_color_temp_kelvin": 2000, "max_color_temp_kelvin": 6500,
             "brightness": 11, "color_temp_kelvin": 2700}
    calls, entities, cleanup = [], {}, []
    restored = {"light.demo": ("off", {"held": True, "paused_axes": ["brightness", "color"]}),
                "light.other": ("on", {})}
    entry = SimpleNamespace(entry_id="schedule", domain="lighting_curves", title="Schedule", options={
        **DEFAULTS, "manual_control_timeout": 30, "points": [{"time": "00:00", **setting}],
        "lights": ["light.demo"],
    }, pref_disable_new_entities=False, disabled_by=None,
        async_on_unload=cleanup.append, add_update_listener=lambda _: lambda: None)

    async def light_service(call):
        calls.append(dict(call.data))
        old = hass.states.get(call.data["entity_id"])
        hass.states.async_set(old.entity_id, "on", {**old.attributes, **call.data}, context=call.context)

    hass.services.async_register("light", "turn_on", light_service)

    def update(updated, **changes):
        for key, value in changes.items():
            setattr(updated, key, value)

    async def last_state(entity):
        saved = restored.get(entity.entity_id, restored.get(entity.light))
        return State(entity.entity_id, *saved) if saved else None

    async def add_entities(added):
        for entity in added:
            entity.hass = hass
            registered = registry.async_get_or_create("switch", "lighting_curves", entity.unique_id,
                config_entry=entry, suggested_object_id=f"{entity.light.split('.')[1]}_curve_active")
            entity.entity_id = registered.entity_id

            @callback
            def write(entity=entity):
                hass.states.async_set(entity.entity_id, "on" if entity.is_on else "off", entity.extra_state_attributes)

            async def remove(entity=entity):
                state = hass.states.get(entity.entity_id)
                restored[entity.entity_id] = (state.state, dict(state.attributes))
                await Entity.async_internal_will_remove_from_hass(entity)
                hass.states.async_remove(entity.entity_id)
                entities.pop(entity.light)

            entity.async_write_ha_state = write
            entity.async_remove = remove
            entities[entity.light] = entity
            await entity.async_added_to_hass()
            write()

    registry = er.EntityRegistry(hass)
    registry.entities = er.EntityRegistryItems(hass)
    registry._entities_data = registry.entities.data
    registry.deleted_entities = {}
    registry.settings = er.EntityRegistrySettings()
    await ar.async_load(hass, load_empty=True)
    platform = SimpleNamespace(async_add_entities=add_entities)

    async def forward(updated, platforms):
        assert {p.value for p in platforms} == {"sensor", "switch"}
        await setup_switches(hass, updated, None)

    hass.config_entries = SimpleNamespace(
        async_forward_entry_setups=forward, async_entries=lambda domain: [entry] if domain == entry.domain else [],
        async_update_entry=update, async_loaded_entries=lambda domain: [entry], async_get_entry=lambda _: entry,
    )
    registered_light = registry.async_get_or_create("light", "demo", "lamp", suggested_object_id="demo")
    old_switch = registry.async_get_or_create("switch", "lighting_curves", "schedule_light.demo_curve_active",
        config_entry=entry, suggested_object_id="my_curve_control", hidden_by=er.RegistryEntryHider.USER)
    area = ar.async_get(hass).async_create("Bedroom")
    old_switch = registry.async_update_entity(old_switch.entity_id, name="My curve", area_id=area.id)
    hass.states.async_set("light.demo", "on", attrs)
    hass.states.async_set("light.other", "off", attrs)
    with patch("custom_components.lighting_curves.switch.async_get_current_platform", return_value=platform), \
         patch("custom_components.lighting_curves.switch.er.async_get", return_value=registry), \
         patch.object(RestoreEntity, "async_added_to_hass", AsyncMock()), \
         patch.object(CurveActiveSwitch, "async_get_last_state", last_state), \
         patch.object(Curve, "calculate_target", lambda curve: setattr(curve, "target", dict(setting))):
        # Light state may not exist yet at startup; use its registry/device friendly name.
        entry.runtime_data = Curve(hass, entry)
        with patch.object(registry, "async_get", return_value=SimpleNamespace(id="not_loaded")), \
             patch("custom_components.lighting_curves.switch.er.async_get_legacy_friendly_name", return_value="Demo Lamp"):
            assert CurveActiveSwitch(entry, "light.not_loaded").translation_placeholders == {"light": "Demo Lamp"}

        # Exercise actual integration setup ordering: restored off must precede its first apply.
        await async_setup_entry(hass, entry)
        curve = entry.runtime_data
        switch = entities["light.demo"]
        assert not calls and not switch.is_on and curve.manual["light.demo"] == {"brightness", "color"}
        assert "light.demo" in curve._held and not curve._manual_timeouts
        assert switch.unique_id == f"schedule_{registered_light.id}_curve_active"
        migrated = registry.async_get(switch.entity_id)
        assert migrated.id == old_switch.id and migrated.entity_id == old_switch.entity_id
        assert migrated.name == "My curve" and migrated.area_id == area.id and migrated.hidden_by == er.RegistryEntryHider.USER
        assert CurveActiveSwitch(entry, "light.unregistered").unique_id == "schedule_light.unregistered_curve_active"
        assert switch.device_info["identifiers"] == {("lighting_curves", entry.entry_id)}
        assert switch.has_entity_name and switch.translation_placeholders == {"light": "Demo Lamp"}

        # A restart retains a manual pause's axes and starts a full configured timeout.
        curve.resume("light.demo")
        with patch("custom_components.lighting_curves.async_call_later") as later:
            await switch.async_remove()
            restored[switch.entity_id] = ("off", {"held": False, "paused_axes": ["brightness"]})
            await add_entities([CurveActiveSwitch(entry, "light.demo")])
            switch = entities["light.demo"]
            assert curve.manual == {"light.demo": {"brightness"}} and not curve._held
            assert later.call_args.args[1] == 30 and not switch.is_on
            await later.call_args.args[2](None)
            await hass.async_block_till_done()
            assert switch.is_on and not curve.manual and not curve._manual_timeouts
        calls.clear()
        hass.states.async_set("light.demo", "on", attrs)
        await hass.async_block_till_done()
        curve.hold("light.demo")

        # Resume applies once with regular transition; timeout is cancelled too.
        await switch.async_turn_on()
        await hass.async_block_till_done()
        assert switch.is_on and not curve.manual and not curve._held
        assert len(calls) == 1 and calls[0]["transition"] == 45 and calls[0]["brightness"] == 153
        assert hass.states.get(switch.entity_id).state == "on"
        calls.clear()

        # Existing manual detection updates the entity, including a single paused axis.
        entry.options["manual_control_mode"] = "pause_changed"
        with patch("custom_components.lighting_curves.async_call_later") as later:
            curve._service_called(Event("call_service", {"domain": "light", "service": "turn_on",
                "service_data": {"entity_id": "light.demo", "brightness": 11}}, context=Context()))
            await hass.async_block_till_done()
            assert curve.manual["light.demo"] == {"brightness"} and not switch.is_on
            assert hass.states.get(switch.entity_id).state == "off"
            cancel = later.return_value
            await switch.async_turn_on()
            cancel.assert_called_once()
            assert not curve._manual_timeouts and switch.is_on

            # Timer expiry also updates the switch and reapplies the curve.
            curve._service_called(Event("call_service", {"domain": "light", "service": "turn_on",
                "service_data": {"entity_id": "light.demo", "brightness": 11}}, context=Context()))
            await later.call_args.args[2](None)
            await hass.async_block_till_done()
            assert switch.is_on and hass.states.get(switch.entity_id).state == "on"

        await switch.async_turn_off()
        curve._service_called(Event("call_service", {"domain": "light", "service": "turn_on",
            "service_data": {"entity_id": "light.demo", "brightness": 12}}, context=Context()))
        calls.clear()
        await curve._async_tick(None)
        await hass.async_block_till_done()
        assert not switch.is_on and not calls and not curve._manual_timeouts
        assert schedules(hass)[0]["curve_active"] == {"light.demo": False}
        assert (await async_get_config_entry_diagnostics(hass, entry))["curve_active"] == {"light.demo": False}

        # Resume never turns an off light on. The next off->on resumes a held light.
        hass.states.async_set("light.demo", "off", attrs)
        await hass.async_block_till_done()
        await switch.async_turn_on()
        assert not calls and switch.is_on and hass.states.get("light.demo").state == "off"
        await switch.async_turn_off()
        await hass.services.async_call("light", "turn_on", {"entity_id": "light.demo"}, blocking=True)
        await hass.async_block_till_done()
        assert switch.is_on and hass.states.get(switch.entity_id).state == "on"
        assert hass.states.get("light.demo").attributes["brightness"] == 153

        # Preview end takes a held participant back and updates its switch.
        await switch.async_turn_off()
        await curve.async_preview({"brightness": 20, "color_temp_kelvin": 2700})
        assert not switch.is_on
        await curve.async_end_preview()
        await hass.async_block_till_done()
        assert switch.is_on and not curve.manual and not curve._held
        assert hass.states.get(switch.entity_id).state == "on"
        assert hass.states.get("light.demo").attributes["brightness"] == 153

        # Options/API assignment changes create and remove entities and registry entries.
        set_lights(hass, entry, ["light.demo", "light.other"])
        await _async_options_updated(hass, entry)
        assert entities["light.other"].is_on and len(er.async_entries_for_config_entry(registry, entry.entry_id)) == 2
        set_lights(hass, entry, ["light.demo"])
        await _async_options_updated(hass, entry)
        assert "light.other" not in entities and registry.async_get("switch.other_curve_active") is None

        # Registry renames carry explicit holds with no timer; registry deletion removes the switch.
        await switch.async_turn_off()
        await hass.async_block_till_done()
        with patch.object(curve, "_manual_changed", wraps=curve._manual_changed) as changed:
            registry.async_update_entity("light.demo", new_entity_id="light.renamed")
            hass.states.async_set("light.renamed", "on", attrs)
            await hass.async_block_till_done()
            assert all(call.args[0] != "light.demo" for call in changed.call_args_list)
        await _async_options_updated(hass, entry)
        assert "light.demo" not in entities and not entities["light.renamed"].is_on
        renamed = entities["light.renamed"]
        assert renamed.entity_id == switch.entity_id and renamed.unique_id == switch.unique_id
        assert registry.async_get(renamed.entity_id).id == old_switch.id
        assert registry.async_get(renamed.entity_id).name == "My curve"
        assert registry.async_get(renamed.entity_id).area_id == area.id
        assert registry.async_get(renamed.entity_id).hidden_by == er.RegistryEntryHider.USER
        assert curve._held == {"light.renamed"} and not curve._manual_timeouts
        with patch.object(curve, "_manual_changed", wraps=curve._manual_changed) as changed:
            registry.async_remove("light.renamed")
            await hass.async_block_till_done()
            changed.assert_not_called()
        await _async_options_updated(hass, entry)
        assert not entities and not er.async_entries_for_config_entry(registry, entry.entry_id) and not curve.manual and not curve._held

    while cleanup:
        cleanup.pop()()
    print("ok: held/manual-axis restore and timeout, registry UUID migration and rename preserve custom entity/hold, no old-ID signals, preview resumes switch, light control and membership")


asyncio.run(main())
