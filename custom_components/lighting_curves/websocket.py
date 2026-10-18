"""Websocket commands for the card, and the card's static file."""
from __future__ import annotations

from pathlib import Path

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from . import DOMAIN, day_minutes, sun
from .curve import POINTS, SETTING, ordered, resolve, target

CARD = Path(__file__).parent / "frontend" / "lighting-curves-card.js"
CARD_URL = f"/{DOMAIN}/lighting-curves-card.js"
SAMPLE_STEP = 10  # minutes between samples for the card's graph


def async_setup(hass: HomeAssistant) -> None:
    for command in (ws_schedules, ws_sample, ws_save, ws_preview, ws_preview_end):
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
@callback
def ws_schedules(hass, connection, msg):
    connection.send_result(
        msg["id"],
        [
            {
                "entry_id": e.entry_id,
                "title": e.title,
                "switch": e.runtime_data.switch,
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
        hass.config_entries.async_update_entry(curve.entry, options={"points": ordered(msg["points"])})
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
        connection.send_error(msg["id"], "no_lights", "This schedule has no Adaptive Lighting lights")
        return
    call = hass.services.async_call
    if curve.preview_off is None:
        curve.preview_off = [l for l in lights if (s := hass.states.get(l)) is None or s.state != STATE_ON]
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
