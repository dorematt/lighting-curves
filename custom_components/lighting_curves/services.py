"""Discoverable Home Assistant actions, backed by the same operations as WS."""
import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.helpers.service import async_register_admin_service

from . import DOMAIN
from .api import create_schedule, get_curve, schedules, set_lights, set_points, validation_error


def _schema(fields):
    schema = vol.Schema(fields)

    def validate(data):
        try:
            return schema(data)
        except vol.Invalid as err:
            raise validation_error("invalid_input", error=str(err)) from err

    return validate


def async_setup(hass):
    async def create(call):
        return await create_schedule(hass, call.data["name"])

    async def points(call):
        set_points(hass, call.data["entry_id"], call.data["points"])

    async def lights(call):
        curve = get_curve(hass, call.data["entry_id"])
        set_lights(hass, curve.entry, call.data["lights"])

    async def get(call):
        return {"schedules": schedules(hass, call.data.get("entry_id"))}

    async_register_admin_service(hass, DOMAIN, "create_schedule", create,
                                 _schema({vol.Required("name"): str}), SupportsResponse.OPTIONAL)
    async_register_admin_service(hass, DOMAIN, "set_points", points,
                                 _schema({vol.Required("entry_id"): str, vol.Required("points"): object}))
    async_register_admin_service(hass, DOMAIN, "set_lights", lights,
                                 _schema({vol.Required("entry_id"): str, vol.Required("lights"): object}))
    hass.services.async_register(DOMAIN, "get_schedules", get,
                                 _schema({vol.Optional("entry_id"): str}), SupportsResponse.ONLY)
