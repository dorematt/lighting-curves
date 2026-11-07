"""Websocket commands for the card, and the card's static file."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from . import DOMAIN
from .api import create_schedule, get_curve, sample as _sample, schedules, set_lights, set_points
from .curve import POINTS, SETTING

CARD = Path(__file__).parent / "frontend" / "lighting-curves-card.js"
CARD_URL = f"/{DOMAIN}/lighting-curves-card.js"


def async_setup(hass: HomeAssistant) -> None:
    for command in (ws_schedules, ws_sample, ws_save, ws_preview, ws_preview_end, ws_create, ws_set_lights):
        websocket_api.async_register_command(hass, command)


async def async_register_card(hass: HomeAssistant) -> None:
    version = int((await hass.async_add_executor_job(CARD.stat)).st_mtime)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD), False)])
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={version}")


def _curve(hass: HomeAssistant, connection, msg):
    try:
        return get_curve(hass, msg["entry_id"])
    except ServiceValidationError as err:
        connection.send_error(msg["id"], err.translation_key, str(err))
        return None


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/schedules"})
@websocket_api.async_response
async def ws_schedules(hass, connection, msg):
    connection.send_result(msg["id"], schedules(hass))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/sample", vol.Required("points"): POINTS})
@callback
def ws_sample(hass, connection, msg):
    connection.send_result(msg["id"], _sample(hass, msg["points"]))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/save", vol.Required("entry_id"): str, vol.Required("points"): POINTS}
)
@callback
def ws_save(hass, connection, msg):
    if curve := _curve(hass, connection, msg):
        set_points(hass, curve.entry.entry_id, msg["points"])
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/preview", vol.Required("entry_id"): str, vol.Required("setting"): SETTING}
)
@websocket_api.async_response
async def ws_preview(hass, connection, msg):
    """Preview the schedule's original participants until preview_end or timeout."""
    if not (curve := _curve(hass, connection, msg)):
        return
    if curve.preview is None and not curve.lights():
        connection.send_error(msg["id"], "no_lights", "This schedule has no lights")
        return
    await curve.async_preview(msg["setting"])
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/preview_end", vol.Required("entry_id"): str})
@websocket_api.async_response
async def ws_preview_end(hass, connection, msg):
    if curve := _curve(hass, connection, msg):
        await curve.async_end_preview()
        connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/create", vol.Required("name"): cv.string})
@websocket_api.async_response
async def ws_create(hass, connection, msg):
    """A new schedule with no lights: a sensor until lights are set."""
    try:
        result = await create_schedule(hass, msg["name"])
    except ServiceValidationError as err:
        connection.send_error(msg["id"], err.translation_key, str(err))
        return
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_lights",
        vol.Required("entry_id"): str,
        vol.Required("lights"): [cv.entity_id],
    }
)
@websocket_api.async_response
async def ws_set_lights(hass, connection, msg):
    """Move existing lights between Lighting Curves schedules only."""
    if not (curve := _curve(hass, connection, msg)):
        return
    try:
        set_lights(hass, curve.entry, msg["lights"])
    except ServiceValidationError as err:
        connection.send_error(msg["id"], err.translation_key, str(err))
        return
    connection.send_result(msg["id"])
