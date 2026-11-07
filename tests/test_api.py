"""Plain-assert checks through HA's action registry and the shared API."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, ".")
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from custom_components.lighting_curves import Curve
from custom_components.lighting_curves import services, websocket
from custom_components.lighting_curves.config_flow import LightingCurvesOptionsFlow, DEFAULT_POINTS
from custom_components.lighting_curves.diagnostics import async_get_config_entry_diagnostics
from custom_components.lighting_curves.light_control import DEFAULTS


async def main():
    hass = HomeAssistant("/tmp/lighting-curves-api-test")
    entries = [SimpleNamespace(entry_id=key, domain="lighting_curves", title=key, options={
        **DEFAULTS, "points": deepcopy(DEFAULT_POINTS), "lights": lights,
    }) for key, lights in (("one", []), ("two", ["light.demo"]))]
    unloaded = SimpleNamespace(entry_id="unloaded", domain="lighting_curves", options={})
    foreign = SimpleNamespace(entry_id="foreign", domain="other")

    def update(entry, **changes):
        for key, value in changes.items():
            setattr(entry, key, value)

    async def create(domain, context, data):
        assert domain == "lighting_curves" and context == {"source": "user"}
        return {"type": "abort"} if data["name"] == "Duplicate" else {"type": "create_entry", "result": entries[0]}

    hass.config_entries = SimpleNamespace(
        async_get_entry=lambda key: next((e for e in [*entries, unloaded, foreign] if e.entry_id == key), None),
        async_get_known_entry=lambda key: next(e for e in entries if e.entry_id == key),
        async_loaded_entries=lambda _: entries, async_entries=lambda _: [*entries, unloaded],
        async_update_entry=update, flow=SimpleNamespace(async_init=create),
    )
    for entry in entries:
        entry.runtime_data = Curve(hass, entry)
    hass.states.async_set("light.demo", "unavailable")
    hass.states.async_set("switch.demo", "on")
    services.async_setup(hass)
    assert hass.services.supports_response("lighting_curves", "get_schedules") == SupportsResponse.ONLY

    async def call(action, data, response=False, context=None):
        return await hass.services.async_call("lighting_curves", action, data, blocking=True,
                                              return_response=response, context=context)

    async def invalid(action, data, key, response=False):
        try:
            await call(action, data, response)
        except ServiceValidationError as err:
            assert err.translation_domain == "lighting_curves" and err.translation_key == key, (action, err)
        else:
            raise AssertionError((action, "should fail", data))

    assert await call("create_schedule", {"name": "  New  "}, True) == {"entry_id": "one"}
    await invalid("create_schedule", {"name": "   "}, "empty_name", True)
    await invalid("create_schedule", {"name": "Duplicate"}, "exists", True)
    await invalid("create_schedule", {"name": 3}, "invalid_input", True)
    points = [{"time": "6:00", "brightness": 80, "rgb_color": [255, 0, 0]}]
    await call("set_points", {"entry_id": "one", "points": points})
    assert entries[0].options["points"] == [{**points[0], "time": "06:00"}]
    assert entries[0].options["transition"] == 45
    await invalid("set_points", {"entry_id": "one", "points": []}, "invalid_points")
    await invalid("set_points", {"entry_id": "one", "points": [{**points[0], "color_temp_kelvin": 2700}]}, "invalid_points")
    await call("set_lights", {"entry_id": "one", "lights": ["light.demo", "light.demo"]})
    assert entries[0].options["lights"] == ["light.demo"] and entries[1].options["lights"] == []
    for lights in (["switch.demo"], ["light.missing"], "light.demo", [None]):
        await invalid("set_lights", {"entry_id": "one", "lights": lights}, "invalid_lights")
    assert entries[0].options["lights"] == ["light.demo"]
    for key in ("unloaded", "foreign", "missing"):
        await invalid("get_schedules", {"entry_id": key}, "not_found", True)
        await invalid("set_points", {"entry_id": key, "points": points}, "not_found")
        await invalid("set_lights", {"entry_id": key, "lights": []}, "not_found")

    # All mutations enforce HA's admin check (including the response action).
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    for action, data in (("create_schedule", {"name": "Forbidden"}),
                         ("set_points", {"entry_id": "one", "points": points}),
                         ("set_lights", {"entry_id": "one", "lights": []})):
        try:
            await call(action, data, action == "create_schedule", Context(user_id="ordinary"))
        except Unauthorized:
            pass
        else:
            raise AssertionError("non-admin mutation accepted")

    with patch("custom_components.lighting_curves.api.sun", return_value=(360, 1080)):
        result = await call("get_schedules", {}, True)
        assert len(result["schedules"]) == 2
        one = (await call("get_schedules", {"entry_id": "one"}, True))["schedules"][0]
        assert {"entry_id", "title", "points", "times", "sunrise", "sunset", "lights", "target", "options", "preview"} <= one.keys()
        assert one["preview"] == {"active": False, "expires_at": None} and one["options"] == DEFAULTS
        connection = SimpleNamespace(send_result=Mock())
        await websocket.ws_schedules.__wrapped__(hass, connection, {"id": 7})
        assert connection.send_result.call_args.args[1] == result["schedules"]
        curve = entries[0].runtime_data
        curve.preview = {"light.demo": False}
        curve.async_restart_preview_timeout()
        active = (await call("get_schedules", {"entry_id": "one"}, True))["schedules"][0]["preview"]
        assert active["active"] and active["expires_at"].endswith("+00:00")
        curve.manual["light.demo"] = {"color"}
        diagnostic = await async_get_config_entry_diagnostics(hass, entries[0])
        assert {"points", "times", "sunrise", "sunset", "target", "options", "lights", "light_states", "manual_control", "preview", "interceptor"} <= diagnostic.keys()
        assert diagnostic["light_states"]["light.demo"] == {"state": "unavailable", "available": False}
        assert diagnostic["manual_control"]["light.demo"]["paused"] == ["color"]
        assert diagnostic["preview"] == active and diagnostic["interceptor"] == "fallback"
        hass.data["lighting_curves"] = {"hooks": [lambda: None]}
        assert (await async_get_config_entry_diagnostics(hass, entries[0]))["interceptor"] == "installed"
        curve._preview_timeout()
        curve._preview_timeout = None
    assert (await async_get_config_entry_diagnostics(hass, unloaded))["loaded"] is False

    # Options use the same light validation/move; invalid submission makes no changes.
    flow = LightingCurvesOptionsFlow()
    flow.hass, flow.handler = hass, "two"
    form = await flow.async_step_init()
    assert len(form["data_schema"].schema) == 8
    result = await flow.async_step_init({**DEFAULTS, "lights": ["switch.demo"]})
    assert result["errors"] == {"lights": "invalid_lights"} and entries[0].options["lights"] == ["light.demo"]
    result = await flow.async_step_init({**DEFAULTS, "lights": ["light.demo"], "transition": 20})
    assert result["data"]["lights"] == ["light.demo"] and result["data"]["transition"] == 20
    assert entries[0].options["lights"] == [] and result["data"]["points"] == DEFAULT_POINTS

    translations = json.loads(Path("custom_components/lighting_curves/translations/en.json").read_text())
    assert set(translations["services"]) == set(hass.services.async_services()["lighting_curves"])
    assert "Lighting Curves card" in translations["config"]["create_entry"]["created"]
    print("ok: four actions, translated errors, admin writes, WS parity, preview expiry, diagnostics, options light validation/move")


asyncio.run(main())
