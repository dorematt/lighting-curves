"""Lighting Curves: independent time-of-day brightness and colour schedules."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_STATE_CHANGED,
    SUN_EVENT_SUNRISE,
    SUN_EVENT_SUNSET,
    STATE_ON,
    Platform,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .curve import target
from .hass_utils import target_entities
from .light_control import (
    BRIGHTNESS_KEYS,
    COLOR_KEYS,
    DEFAULTS,
    is_ours,
    light_data,
    matches,
    own_context,
    register_curve,
    unregister_curve,
    with_transition,
)

DOMAIN = "lighting_curves"
PLATFORMS = [Platform.SENSOR, Platform.SWITCH]
PREVIEW_TIMEOUT = 600
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
    """One schedule, including manual overrides and temporary preview ownership."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass, self.entry = hass, entry
        self.target = None
        self.preview_expires_at = None
        self.preview = None  # Original participants: entity_id -> was on.
        self.intercepted = {}
        self.passthrough = {}  # entity_id -> context of an effect/flash turn-on
        self.manual = {}
        self._held = set()
        self.async_sync_switches = None
        self._manual_timeouts = {}
        self._listeners = []
        self._interval = self._preview_timeout = None
        self._apply_lock = asyncio.Lock()
        self._preview_lock = asyncio.Lock()

    @property
    def points(self) -> list:
        return self.entry.options["points"]

    def option(self, key):
        return self.entry.options.get(key, DEFAULTS[key])

    def lights(self) -> list[str]:
        return list(self.entry.options.get("lights", []))

    def preview_state(self):
        return {"active": self.preview is not None, "expires_at": self.preview_expires_at}

    def in_preview(self, entity_id):
        curves = self.hass.data.get(DOMAIN, {}).get("curves", {self.entry.entry_id: self})
        return any(curve.preview is not None and entity_id in curve.preview for curve in curves.values())

    def calculate_target(self):
        self.target = target(self.points, day_minutes(dt_util.now()), *sun(self.hass))

    async def async_start(self) -> None:
        register_curve(self)
        self._listeners.extend([
            async_track_time_change(self.hass, self._async_minute, second=0),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._async_light_changed,
                                      event_filter=self._is_light),
            self.hass.bus.async_listen(EVENT_CALL_SERVICE, self._service_called),
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed),
        ])
        self.reset_interval()
        await self.async_update(force=True)

    @callback
    def _is_light(self, data):
        return data["entity_id"] in self.lights()

    @callback
    def reset_interval(self):
        if self._interval:
            self._interval()
        self._interval = async_track_time_interval(
            self.hass, self._async_tick, timedelta(seconds=self.option("interval"))
        )

    @callback
    def async_stop(self) -> None:
        unregister_curve(self)
        while self._listeners:
            self._listeners.pop()()
        if self._interval:
            self._interval()
            self._interval = None
        if self._preview_timeout:
            self._preview_timeout()
            self._preview_timeout = None
        for cancel, _deadline in self._manual_timeouts.values():
            cancel()
        self._manual_timeouts.clear()

    @callback
    def resume(self, entity_id):
        paused = self.manual.pop(entity_id, None)
        self._held.discard(entity_id)
        if timer := self._manual_timeouts.pop(entity_id, None):
            timer[0]()
        if paused:
            self._manual_changed(entity_id)

    @callback
    def hold(self, entity_id):
        """Pause both axes until explicitly resumed or the light cycles off/on."""
        self._held.add(entity_id)
        self.manual[entity_id] = {"brightness", "color"}
        self._restart_manual_timeout(entity_id)
        self._manual_changed(entity_id)

    @callback
    def _manual_changed(self, entity_id):
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.entry.entry_id}_{entity_id}_active")

    @callback
    def _restart_manual_timeout(self, entity_id, timeout=None):
        if timer := self._manual_timeouts.pop(entity_id, None):
            timer[0]()
        if entity_id in self._held:
            return
        if timeout is None:
            timeout = self.option("manual_control_timeout")
            if not timeout:
                return

        async def expire(_now):
            self.resume(entity_id)
            self.calculate_target()
            await self._async_apply([entity_id])

        self._manual_timeouts[entity_id] = (
            async_call_later(self.hass, timeout, expire), self.hass.loop.time() + timeout,
        )

    @callback
    def _service_called(self, event):
        data = event.data
        if data["domain"] != "light" or data["service"] not in ("turn_on", "toggle") or is_ours(event.context):
            return
        service_data = data["service_data"]
        params = service_data.get("params", service_data)
        axes = set()
        if BRIGHTNESS_KEYS.intersection(params) or "profile" in params:
            axes.add("brightness")
        if COLOR_KEYS.intersection(params) or "profile" in params:
            axes.add("color")
        for entity_id in target_entities(self.hass, service_data):
            state = self.hass.states.get(entity_id)
            if entity_id not in self.lights():
                continue
            if "effect" in params or "flash" in params:
                if state and state.state == "off":
                    self.passthrough[entity_id] = event.context.id
            else:
                self.passthrough.pop(entity_id, None)
            if not axes or not state or state.state != STATE_ON or self.in_preview(entity_id):
                continue
            self.manual.setdefault(entity_id, set()).update(
                {"brightness", "color"} if self.option("manual_control_mode") == "pause_all" else axes
            )
            self._restart_manual_timeout(entity_id)
            self._manual_changed(entity_id)

    async def _async_light_changed(self, event):
        old, new = event.data["old_state"], event.data["new_state"]
        if not new or new.state != STATE_ON or (old and old.state == STATE_ON):
            return
        if old and old.state == "off":
            self.resume(new.entity_id)
        passthrough = self.passthrough.pop(new.entity_id, None)
        if passthrough and passthrough in (event.context.id, event.context.parent_id):
            self.manual[new.entity_id] = {"brightness", "color"}  # an effect or flash owns the light
            self._restart_manual_timeout(new.entity_id)
            self._manual_changed(new.entity_id)
            return
        intercepted = self.intercepted.pop(new.entity_id, None)
        if self.in_preview(new.entity_id) or is_ours(event.context) or (
            intercepted and intercepted in (event.context.id, event.context.parent_id)
        ):
            return
        self.calculate_target()
        await self._async_apply([new.entity_id], transition=self.option("initial_transition"))

    async def _registry_changed(self, event):
        data = event.data
        old_id = data.get("changes", {}).get("entity_id", data["entity_id"])
        if old_id not in self.lights() and (self.preview is None or old_id not in self.preview):
            return
        new_id = data["entity_id"]
        if data["action"] == "remove":
            lights = [light for light in self.lights() if light != old_id]
            _LOGGER.warning("Removed deleted light %s from schedule %s", old_id, self.entry.title)
            if self.preview is not None:
                self.preview.pop(old_id, None)
        elif data["action"] == "update" and old_id != new_id:
            lights = list(dict.fromkeys(new_id if light == old_id else light for light in self.lights()))
            if self.preview is not None and old_id in self.preview:
                self.preview[new_id] = self.preview.pop(old_id)
            _LOGGER.info("Followed light rename %s -> %s for schedule %s", old_id, new_id, self.entry.title)
        else:
            return
        manual, timer = self.manual.get(old_id), self._manual_timeouts.get(old_id)
        held = old_id in self._held
        self.resume(old_id)
        if data["action"] == "update" and manual:
            self.manual[new_id] = manual
            if held:
                self._held.add(new_id)
            if timer:
                self._restart_manual_timeout(new_id, max(0, timer[1] - self.hass.loop.time()))
            self._manual_changed(new_id)
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options, "lights": lights})

    async def _async_apply(self, lights, *, setting=None, transition=None, restore=False):
        async with self._apply_lock:
            for entity_id in lights:
                state = self.hass.states.get(entity_id)
                if not state or state.state in ("unavailable", "unknown"):
                    if restore:
                        raise HomeAssistantError(f"Preview light {entity_id} is unavailable")
                    continue
                if not restore and setting is None and (
                    entity_id not in self.lights() or state.state != STATE_ON or self.in_preview(entity_id)
                ):
                    continue
                data = light_data(state, setting or self.target, () if restore or setting else self.manual.get(entity_id, ()))
                if setting is None and not restore and (not data or matches(state, data)):
                    continue
                try:
                    await self.hass.services.async_call(
                        "light", "turn_on", {"entity_id": entity_id, **with_transition(
                            data, self.option("transition") if transition is None else transition
                        )}, blocking=True, context=own_context(),
                    )
                except Exception:
                    if setting is not None or restore:
                        raise
                    _LOGGER.exception("Could not apply schedule %s to %s; will retry", self.entry.title, entity_id)

    @callback
    def async_restart_preview_timeout(self):
        if self._preview_timeout:
            self._preview_timeout()
        self.preview_expires_at = (dt_util.utcnow() + timedelta(seconds=PREVIEW_TIMEOUT)).isoformat()
        self._preview_timeout = async_call_later(self.hass, PREVIEW_TIMEOUT, self._async_preview_timed_out)

    async def _async_preview_timed_out(self, _now):
        self._preview_timeout = None
        try:
            await self.async_end_preview()
        except HomeAssistantError:
            _LOGGER.exception("Could not restore preview for schedule %s; will retry", self.entry.title)

    async def async_preview(self, setting):
        async with self._preview_lock:
            if self.preview is None:
                self.preview = {light: bool((state := self.hass.states.get(light)) and state.state == STATE_ON)
                                for light in self.lights()}
            self.async_restart_preview_timeout()  # Arm before any service can fail.
            await self._async_apply(list(self.preview), setting=setting, transition=0.3)

    async def async_end_preview(self):
        """Restore the original participants, retaining failed rollback work."""
        async with self._preview_lock:
            if self.preview is None:
                return
            if self._preview_timeout:
                self._preview_timeout()
                self._preview_timeout = None
            self.calculate_target()
            failed = False
            for entity_id, was_on in list(self.preview.items()):
                try:
                    if was_on:
                        await self._async_apply([entity_id], transition=self.option("initial_transition"), restore=True)
                    else:
                        state = self.hass.states.get(entity_id)
                        if not state or state.state in ("unknown", "unavailable"):
                            raise HomeAssistantError(f"Preview light {entity_id} is unavailable")
                        await self.hass.services.async_call(
                            "light", "turn_off", {"entity_id": entity_id}, blocking=True, context=own_context()
                        )
                except Exception:
                    failed = True
                    _LOGGER.exception("Could not restore preview light %s", entity_id)
                else:
                    self.preview.pop(entity_id, None)
            if failed:
                self.async_restart_preview_timeout()
                raise HomeAssistantError("Some preview lights could not be restored; rollback will retry")
            self.preview = None
            self.preview_expires_at = None

    async def _async_minute(self, _now):
        await self.async_update()

    async def _async_tick(self, _now):
        if not self._apply_lock.locked():
            await self.async_update(force=True)

    async def async_update(self, force=False):
        self.calculate_target()
        async_dispatcher_send(self.hass, f"{DOMAIN}_{self.entry.entry_id}")
        if force:
            await self._async_apply(self.lights())


async def async_migrate_entry(hass, entry):
    if entry.version > 2:
        return False
    if entry.version == 1:
        options = {**DEFAULTS, **entry.options}
        if not options.get("lights"):
            switch = entry.data.get("switch")
            entity = er.async_get(hass).async_get(switch) if switch else None
            linked = hass.config_entries.async_get_entry(entity.config_entry_id) if entity else None
            if linked and linked.domain == "adaptive_lighting":
                options["lights"] = list(dict.fromkeys(linked.options.get("lights", [])))
            else:
                options["lights"] = []
                if switch:
                    _LOGGER.warning("Schedule %s: linked Adaptive Lighting entry not found; choose its lights again", entry.title)
        owned = {light for other in hass.config_entries.async_entries(DOMAIN) if other.entry_id != entry.entry_id
                 for light in other.options.get("lights", [])}
        duplicates = sorted(owned.intersection(options["lights"]))
        if duplicates:
            _LOGGER.warning("Schedule %s: dropped lights already owned by another Lighting Curves schedule: %s",
                            entry.title, ", ".join(duplicates))
            options["lights"] = [light for light in options["lights"] if light not in owned]
        data = {k: v for k, v in entry.data.items() if k != "switch"}
        hass.config_entries.async_update_entry(entry, data=data, options=options, version=2)
        _LOGGER.info("Migrated schedule %s to standalone light control (%d lights)", entry.title, len(options["lights"]))
    return True


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    from . import services, websocket

    services.async_setup(hass)
    websocket.async_setup(hass)
    await websocket.async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data = curve = Curve(hass, entry)
    al_lights = {light for al in hass.config_entries.async_entries("adaptive_lighting")
                 for light in al.options.get("lights", [])}
    overlap = sorted(al_lights.intersection(curve.lights()))
    if overlap:
        _LOGGER.warning("Schedule %s: %s are still in Adaptive Lighting; remove them from Adaptive Lighting so the two do not fight",
                        entry.title, ", ".join(overlap))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(curve.async_stop)
    await curve.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    curve = entry.runtime_data
    for light in list(curve.manual):
        if light not in curve.lights():
            curve.resume(light)
    if curve.async_sync_switches:
        await curve.async_sync_switches()
    curve.reset_interval()
    await curve.async_update(force=True)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        await entry.runtime_data.async_end_preview()
    except HomeAssistantError:
        _LOGGER.exception("Could not restore preview for schedule %s during unload; continuing teardown", entry.title)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
