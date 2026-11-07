"""Websocket commands for the card, and the card's static file."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from . import DOMAIN, day_minutes, sun
from .curve import POINTS, SETTING, ordered, resolve, target

CARD = Path(__file__).parent / "frontend" / "lighting-curves-card.js"
CARD_URL = f"/{DOMAIN}/lighting-curves-card.js"
SAMPLE_STEP = 10  # minutes between samples for the card's graph


def async_setup(hass: HomeAssistant) -> None:
    for command in (ws_schedules, ws_sample, ws_save, ws_preview, ws_preview_end, ws_create, ws_set_lights):
        websocket_api.async_register_command(hass, command)


async def async_register_card(hass: HomeAssistant) -> None:
    version = int((await hass.async_add_executor_job(CARD.stat)).st_mtime)
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(CARD), False)])
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={version}")


def _curve(hass: HomeAssistant, connection, msg):
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        connection.send_error(msg["id"], "not_found", "No loaded lighting curve with that entry_id")
        return None
    return entry.runtime_data


def _sample(hass: HomeAssistant, points: list) -> dict:
    rise, sett = sun(hass)
    return {
        "points": ordered(points),
        "times": resolve(points, rise, sett),
        "samples": [target(points, m, rise, sett) for m in range(0, 1441, SAMPLE_STEP)],
        "sample_step": SAMPLE_STEP,
        "now": day_minutes(dt_util.now()),
        "sunrise": rise,
        "sunset": sett,
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/schedules"})
@websocket_api.async_response
async def ws_schedules(hass, connection, msg):
    connection.send_result(
        msg["id"],
        [
            {
                "entry_id": e.entry_id,
                "title": e.title,
                "lights": e.runtime_data.lights(),
                "target": e.runtime_data.target,
                **_sample(hass, e.runtime_data.points),
            }
            for e in hass.config_entries.async_loaded_entries(DOMAIN)
        ],
    )


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
        hass.config_entries.async_update_entry(
            curve.entry, options={**curve.entry.options, "points": ordered(msg["points"])}
        )
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
    name = msg["name"].strip()
    if not name:
        connection.send_error(msg["id"], "empty_name", "A schedule needs a name")
        return
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"name": name}
    )
    if result["type"] != "create_entry":
        connection.send_error(msg["id"], "exists", f"A schedule called {name} already exists")
        return
    connection.send_result(msg["id"], {"entry_id": result["result"].entry_id})


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
    lights = list(dict.fromkeys(msg["lights"]))
    if any(not light.startswith("light.") or hass.states.get(light) is None for light in lights):
        connection.send_error(msg["id"], "invalid_lights", "Choose existing light entities")
        return
    for entry in hass.config_entries.async_entries(DOMAIN):
        old = entry.options.get("lights", [])
        new = lights if entry is curve.entry else [light for light in old if light not in lights]
        if new != old:
            hass.config_entries.async_update_entry(entry, options={**entry.options, "lights": new})
    connection.send_result(msg["id"])
