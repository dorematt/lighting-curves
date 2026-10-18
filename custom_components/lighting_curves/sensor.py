from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CurveSensor(entry)])


class CurveSensor(SensorEntity):
    """The schedule's target now: brightness as the state, colour as attributes."""

    _attr_has_entity_name = True
    _attr_name = "Lighting curve"
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_native_unit_of_measurement = "%"
    _attr_should_poll = False

    def __init__(self, entry):
        self._curve = entry.runtime_data
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title}

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"{DOMAIN}_{self._curve.entry.entry_id}", self._updated)
        )

    @callback
    def _updated(self):
        self.async_write_ha_state()

    @property
    def native_value(self):
        return self._curve.target and self._curve.target["brightness"]

    @property
    def extra_state_attributes(self):
        t = self._curve.target or {}
        return {k: v for k, v in t.items() if k != "brightness"}
