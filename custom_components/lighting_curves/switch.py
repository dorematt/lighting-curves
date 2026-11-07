"""Per-light control of whether a schedule may adapt a light."""
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import async_get_current_platform
from homeassistant.helpers.restore_state import RestoreEntity

from . import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    curve = entry.runtime_data
    platform = async_get_current_platform()
    switches = {}

    async def sync_switches():
        lights = curve.lights()
        registry = er.async_get(hass)
        wanted = {f"{entry.entry_id}_{light}_curve_active" for light in lights}
        for light in list(switches):
            if light not in lights:
                entity = switches.pop(light)
                if entity.hass:
                    await entity.async_remove()
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.domain == "switch" and entity.unique_id not in wanted:
                registry.async_remove(entity.entity_id)
        added = [CurveActiveSwitch(entry, light) for light in lights if light not in switches]
        switches.update((entity.light, entity) for entity in added)
        # Await restoration before the options listener applies newly assigned lights.
        await platform.async_add_entities(added)

    curve.async_sync_switches = sync_switches
    await sync_switches()


class CurveActiveSwitch(SwitchEntity, RestoreEntity):
    """An off switch holds both axes, regardless of the manual timeout option."""

    _attr_has_entity_name = True
    _attr_translation_key = "curve_active"
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_should_poll = False

    def __init__(self, entry, light):
        self._curve = entry.runtime_data
        self.light = light
        state = self._curve.hass.states.get(light)
        registered = er.async_get(self._curve.hass).async_get(light)
        name = state.name if state else (
            er.async_get_legacy_friendly_name(self._curve.hass, registered) if registered else ""
        )
        name = name or light.removeprefix("light.").replace("_", " ")
        self._attr_translation_placeholders = {"light": name}
        self._attr_unique_id = f"{entry.entry_id}_{light}_curve_active"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if (state := await self.async_get_last_state()) and state.state == STATE_OFF:
            self._curve.hold(self.light)
        self.async_on_remove(async_dispatcher_connect(
            self.hass, f"{DOMAIN}_{self._curve.entry.entry_id}_{self.light}_active", self._updated,
        ))

    @callback
    def _updated(self):
        self.async_write_ha_state()

    @property
    def is_on(self):
        return self.light not in self._curve.manual

    async def async_turn_on(self, **kwargs):
        self._curve.resume(self.light)
        self._curve.calculate_target()
        await self._curve._async_apply([self.light])

    async def async_turn_off(self, **kwargs):
        self._curve.hold(self.light)
