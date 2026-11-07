"""Downloadable runtime snapshot for a Lighting Curves config entry."""
from . import DOMAIN
from .api import schedules


async def async_get_config_entry_diagnostics(hass, entry):
    if not (curve := getattr(entry, "runtime_data", None)):
        return {"entry_id": entry.entry_id, "loaded": False, "options": dict(entry.options)}
    snapshot = schedules(hass, entry.entry_id)[0]
    snapshot.pop("samples")
    shared = hass.data.get(DOMAIN, {})
    return {
        **snapshot,
        "loaded": True,
        "light_states": {
            entity_id: {"state": state.state if (state := hass.states.get(entity_id)) else None,
                        "available": bool(state and state.state not in ("unavailable", "unknown"))}
            for entity_id in curve.lights()
        },
        "manual_control": {
            entity_id: {"paused": sorted(axes), "remaining_seconds": max(0, timer[1] - hass.loop.time())
                        if (timer := curve._manual_timeouts.get(entity_id)) else None}
            for entity_id, axes in curve.manual.items()
        },
        "interceptor": "installed" if shared.get("hooks") else "fallback",
    }
