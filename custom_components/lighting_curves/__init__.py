"""Lighting Curves: time-of-day brightness and colour schedules for Adaptive Lighting."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET, STATE_ON, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .curve import target

DOMAIN = "lighting_curves"
PREVIEW_TIMEOUT = 600  # seconds without a preview change before the lights go back
_LOGGER = logging.getLogger(__name__)


def day_minutes(dt) -> float:
    dt = dt_util.as_local(dt)
    return dt.hour * 60 + dt.minute + dt.second / 60


def sun(hass: HomeAssistant) -> tuple[float, float]:
    """Today's sunrise and sunset, in minutes since local midnight."""
    today = dt_util.now().date()
    rise = get_astral_event_date(hass, SUN_EVENT_SUNRISE, today)
    sett = get_astral_event_date(hass, SUN_EVENT_SUNSET, today)
    # ponytail: polar day/night falls back to 06:00/18:00
    return (day_minutes(rise) if rise else 360, day_minutes(sett) if sett else 1080)


class Curve:
    """One schedule: works out the target each minute and sends it to Adaptive Lighting."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.switch: str | None = entry.data.get("switch")
        self.sleep_switch: str | None = None
        self.target: dict | None = None
        self._sent: dict | None = None
        self.preview_off: list[str] | None = None  # lights that were off when a preview began
        self._listeners: list = []
        self._preview_timeout = None

    @property
    def points(self) -> list:
        return self.entry.options["points"]

    def lights(self) -> list[str]:
        """The lights of the Adaptive Lighting switch."""
        if not self.switch or not (ent := er.async_get(self.hass).async_get(self.switch)):
            return []
        al = self.hass.config_entries.async_get_entry(ent.config_entry_id)
        return list(al.options.get("lights", [])) if al else []

    async def async_start(self) -> None:
        if self.switch and (ent := er.async_get(self.hass).async_get(self.switch)):
            self.sleep_switch = next(
                (
                    e.entity_id
                    for e in er.async_entries_for_config_entry(er.async_get(self.hass), ent.config_entry_id)
                    if e.domain == "switch" and e.unique_id.endswith("_sleep_mode")
                ),
                None,
            )
        self._listeners.append(async_track_time_change(self.hass, self._async_tick, second=0))
        if self.switch:
            self._listeners.append(
                async_track_state_change_event(self.hass, [self.switch], self._async_switch_changed)
            )
        await self.async_update()

    @callback
    def async_stop(self) -> None:
        while self._listeners:
            self._listeners.pop()()
        if self._preview_timeout:
            self._preview_timeout()

    @callback
    def async_restart_preview_timeout(self) -> None:
        """A preview the card never ended (closed tab) ends by itself."""
        if self._preview_timeout:
            self._preview_timeout()
        self._preview_timeout = async_call_later(self.hass, PREVIEW_TIMEOUT, self._async_preview_timed_out)

    async def _async_preview_timed_out(self, _now) -> None:
        self._preview_timeout = None
        await self.async_end_preview()

    async def async_end_preview(self) -> None:
        """Put the lights back: off if they were off, else back on the schedule."""
        if self._preview_timeout:
            self._preview_timeout()
            self._preview_timeout = None
        off, self.preview_off = self.preview_off, None
        if off is None:
            return
        call = self.hass.services.async_call
        lights = self.lights()
        if off:
            await call("light", "turn_off", {"entity_id": off}, blocking=True)
        await call(
            "adaptive_lighting",
            "set_manual_control",
            {"entity_id": self.switch, "lights": lights, "manual_control": False},
            blocking=True,
        )
        if on := [l for l in lights if l not in off]:
            await call(
                "adaptive_lighting",
                "apply",
                {"entity_id": self.switch, "lights": on, "turn_on_lights": False, "transition": 1},
                blocking=True,
            )

    async def _async_tick(self, _now) -> None:
        await self.async_update()

    async def _async_switch_changed(self, event: Event) -> None:
        # A switch that turns on (HA start, Adaptive Lighting reload) is back on its own
        # settings, so send the target again.
        old, new = event.data["old_state"], event.data["new_state"]
        if new and new.state == STATE_ON and (old is None or old.state != STATE_ON):
            self._sent = None
            await self.async_update()

    async def async_update(self, force: bool = False) -> None:
        self.target = target(self.points, day_minutes(dt_util.now()), *sun(self.hass))
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.entry.entry_id}")
        if force:
            self._sent = None
        state = self.hass.states.get(self.switch) if self.switch else None
        if state is None or state.state != STATE_ON or self.target == self._sent:
            return
        try:
            await self._async_send(self.target)
        except Exception:  # noqa: BLE001 - log and retry next minute, never kill the timer
            _LOGGER.exception("Could not send %s to %s", self.target, self.switch)
            return
        self._sent = self.target

    async def _async_send(self, t: dict) -> None:
        call = self.hass.services.async_call
        if "rgb_color" in t:
            # Adaptive Lighting sends RGB only in sleep mode.
            await call(
                "adaptive_lighting",
                "change_switch_settings",
                {
                    "entity_id": self.switch,
                    "use_defaults": "current",
                    "sleep_rgb_or_color_temp": "rgb_color",
                    "sleep_rgb_color": t["rgb_color"],
                    "sleep_brightness": t["brightness"],
                },
                blocking=True,
            )
            if self.sleep_switch and not self._is_on(self.sleep_switch):
                await call("switch", "turn_on", {"entity_id": self.sleep_switch}, blocking=True)
            return
        await call(
            "adaptive_lighting",
            "change_switch_settings",
            {
                "entity_id": self.switch,
                "use_defaults": "current",
                "min_brightness": t["brightness"],
                "max_brightness": t["brightness"],
                "min_color_temp": t["color_temp_kelvin"],
                "max_color_temp": t["color_temp_kelvin"],
            },
            blocking=True,
        )
        # A schedule with RGB points owns sleep mode; one without leaves it to you.
        if (
            self.sleep_switch
            and self._is_on(self.sleep_switch)
            and any("rgb_color" in p for p in self.points)
        ):
            await call("switch", "turn_off", {"entity_id": self.sleep_switch}, blocking=True)

    def _is_on(self, entity_id: str) -> bool:
        return (s := self.hass.states.get(entity_id)) is not None and s.state == STATE_ON



async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    from . import websocket  # noqa: PLC0415

    websocket.async_setup(hass)
    await websocket.async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data = curve = Curve(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    await curve.async_start()
    entry.async_on_unload(curve.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await entry.runtime_data.async_update(force=True)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR])
