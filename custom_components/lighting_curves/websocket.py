"""Websocket commands for the card, and the card's static file."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import IntegrationNotFound, async_get_integration
from homeassistant.util import dt as dt_util

from . import DOMAIN, day_minutes, sun
from .curve import POINTS, SETTING, ordered, resolve, target

CARD = Path(__file__).parent / "frontend" / "lighting-curves-card.js"
CARD_URL = f"/{DOMAIN}/lighting-curves-card.js"
SAMPLE_STEP = 10  # minutes between samples for the card's graph
AL = "adaptive_lighting"


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


async def _al_available(hass: HomeAssistant) -> bool:
    """Adaptive Lighting is installed. Checked each time: Marketplace installs it without a restart."""
    try:
        await async_get_integration(hass, AL)
    except IntegrationNotFound:
        return False
    return True


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/schedules"})
@websocket_api.async_response
async def ws_schedules(hass, connection, msg):
    al_available = await _al_available(hass)
    connection.send_result(
        msg["id"],
        [
            {
                "entry_id": e.entry_id,
                "title": e.title,
                "switch": e.runtime_data.switch,
                "lights": e.runtime_data.lights(),
                "adaptive": bool(e.runtime_data.switch),
                "al_available": al_available,
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
    """Show a setting on all the schedule's lights. Adaptive Lighting leaves them
    alone until preview_end."""
    if not (curve := _curve(hass, connection, msg)):
        return
    if not (lights := curve.lights()):
        connection.send_error(msg["id"], "no_lights", "This schedule has no lights")
        return
    call = hass.services.async_call
    if curve.preview_off is None:
        curve.preview_off = [l for l in lights if (s := hass.states.get(l)) is None or s.state != STATE_ON]
    if curve.switch:
        await call(
            "adaptive_lighting",
            "set_manual_control",
            {"entity_id": curve.switch, "lights": lights, "manual_control": True},
            blocking=True,
        )
    setting = dict(msg["setting"])
    setting["brightness_pct"] = setting.pop("brightness")
    await call(
        "light",
        "turn_on",
        {"entity_id": lights, "transition": 0.3, **setting},
        blocking=True,
    )
    curve.async_restart_preview_timeout()
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
        vol.Optional("adaptive", default=True): bool,
    }
)
@websocket_api.async_response
async def ws_set_lights(hass, connection, msg):
    """Set the schedule's lights. With Adaptive Lighting they go on the schedule's
    Adaptive Lighting entry (made, or found by name, if the schedule has none);
    without, the schedule keeps them and sets them when they turn on. A light
    follows one schedule, so it leaves every other one."""
    if not (curve := _curve(hass, connection, msg)):
        return
    entry, lights, adaptive = curve.entry, msg["lights"], msg["adaptive"]
    if adaptive and lights and not await _al_available(hass):
        connection.send_error(msg["id"], "no_al", "Adaptive Lighting is not installed")
        return
    old_al = _al_entry(hass, curve.switch)
    al, base = old_al, None
    if adaptive and lights and al is None:
        # Found by name: a schedule that had Adaptive Lighting turned off keeps its entry.
        al = next((e for e in hass.config_entries.async_entries(AL) if e.unique_id == entry.title), None)
        if al is None:
            # New entries take the settings of an existing one (transitions, take-over
            # control...), or Adaptive Lighting's defaults if there is none.
            base = next((e.options for e in hass.config_entries.async_entries(AL)), {})
            result = await hass.config_entries.flow.async_init(
                AL, context={"source": SOURCE_USER}, data={"name": entry.title}
            )
            if result["type"] != "create_entry":
                connection.send_error(msg["id"], "al_failed", f"Could not make an Adaptive Lighting entry for {entry.title}")
                return
            al = result["result"]
    if not adaptive:
        al = None
    for other in hass.config_entries.async_entries(AL):
        old = other.options.get("lights", [])
        if other is al:
            new = lights
        elif other is old_al:
            new = []  # the schedule has left Adaptive Lighting
        else:
            new = [l for l in old if l not in lights]
        if new != old:
            # Adaptive Lighting reloads the entry on an options change.
            options = base if other is al and base is not None else other.options
            hass.config_entries.async_update_entry(other, options={**options, "lights": new})
    for other in hass.config_entries.async_entries(DOMAIN):
        old = other.options.get("lights", [])
        new = (lights if al is None else []) if other is entry else [l for l in old if l not in lights]
        if new != old:
            hass.config_entries.async_update_entry(other, options={**other.options, "lights": new})
    # The main switch's unique_id is the entry's name.
    switch = er.async_get(hass).async_get_entity_id("switch", AL, al.unique_id) if al else None
    if switch != curve.switch:
        hass.config_entries.async_update_entry(entry, data={"switch": switch} if switch else {})
        await hass.config_entries.async_reload(entry.entry_id)
    connection.send_result(msg["id"])


def _al_entry(hass: HomeAssistant, switch: str | None):
    ent = er.async_get(hass).async_get(switch) if switch else None
    return hass.config_entries.async_get_entry(ent.config_entry_id) if ent else None

