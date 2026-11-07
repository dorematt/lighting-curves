"""Shared schedule operations for actions, WebSocket commands and options."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import SOURCE_USER
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from . import DOMAIN, day_minutes, sun
from .curve import POINTS, ordered, resolve, target
from .light_control import DEFAULTS

SAMPLE_STEP = 10


def validation_error(key, **placeholders):
    return ServiceValidationError(
        translation_domain=DOMAIN, translation_key=key,
        translation_placeholders=placeholders or None,
    )


def get_curve(hass, entry_id):
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not getattr(entry, "runtime_data", None):
        raise validation_error("not_found")
    return entry.runtime_data


def sample(hass, points):
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


def schedules(hass, entry_id=None):
    entries = [get_curve(hass, entry_id).entry] if entry_id is not None else hass.config_entries.async_loaded_entries(DOMAIN)
    return [{
        "entry_id": entry.entry_id,
        "title": entry.title,
        "lights": entry.runtime_data.lights(),
        "target": entry.runtime_data.target,
        "options": {key: entry.runtime_data.option(key) for key in DEFAULTS},
        "preview": entry.runtime_data.preview_state(),
        **sample(hass, entry.runtime_data.points),
    } for entry in entries]


async def create_schedule(hass, name):
    name = name.strip()
    if not name:
        raise validation_error("empty_name")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data={"name": name}
    )
    if result["type"] != "create_entry":
        raise validation_error("exists", name=name)
    return {"entry_id": result["result"].entry_id}


def set_points(hass, entry_id, points):
    curve = get_curve(hass, entry_id)
    try:
        points = POINTS(points)
    except vol.Invalid as err:
        raise validation_error("invalid_points", error=str(err)) from err
    hass.config_entries.async_update_entry(curve.entry, options={**curve.entry.options, "points": ordered(points)})


def validate_lights(hass, lights):
    try:
        lights = list(dict.fromkeys(vol.Schema([cv.entity_id])(lights)))
    except vol.Invalid as err:
        raise validation_error("invalid_lights") from err
    if any(not light.startswith("light.") or hass.states.get(light) is None for light in lights):
        raise validation_error("invalid_lights")
    return lights


def set_lights(hass, entry, lights, options=None):
    """Replace membership, moving lights out of every other LC schedule."""
    lights = validate_lights(hass, lights)
    for other in hass.config_entries.async_entries(DOMAIN):
        old = other.options.get("lights", [])
        new = lights if other.entry_id == entry.entry_id else [light for light in old if light not in lights]
        if other.entry_id == entry.entry_id:
            updated = {**other.options, **(options or {}), "lights": new}
        elif new != old:
            updated = {**other.options, "lights": new}
        else:
            continue
        if updated != other.options:
            hass.config_entries.async_update_entry(other, options=updated)
    return {**entry.options, **(options or {}), "lights": lights}
