"""Plain-assert entry migration checks; run in the HA Docker image."""
import asyncio
from copy import deepcopy
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, ".")
from custom_components.lighting_curves import async_migrate_entry


async def main():
    points = [{"time": "06:00", "brightness": 50, "color_temp_kelvin": 3000}]
    al = SimpleNamespace(domain="adaptive_lighting", options={"lights": ["light.one", "light.two"], "interval": 10})
    original = deepcopy(al.options)
    updates = []

    def update(entry, **changes):
        assert entry is not al, "AL must never be updated"
        updates.append(entry)
        for key, value in changes.items():
            setattr(entry, key, value)

    hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_get_entry=lambda entry_id: al if entry_id == "al" else None,
        async_update_entry=update,
    ))
    registry = SimpleNamespace(async_get=lambda entity_id: SimpleNamespace(config_entry_id="al")
                               if entity_id == "switch.adaptive_lighting_main" else None)
    with patch("custom_components.lighting_curves.er.async_get", return_value=registry):
        for lights in (None, []):
            entry = SimpleNamespace(version=1, title="Room", data={"switch": "switch.adaptive_lighting_main"},
                                    options={"points": deepcopy(points), **({"lights": lights} if lights is not None else {})})
            assert await async_migrate_entry(hass, entry)
            assert entry.version == 2 and entry.data == {}
            assert entry.options["lights"] == ["light.one", "light.two"]
            assert entry.options["points"] == points and entry.options["interval"] == 90
            assert al.options == original
        missing = SimpleNamespace(version=1, title="Missing AL", data={"switch": "switch.missing"}, options={"points": points})
        assert await async_migrate_entry(hass, missing)
        assert missing.options["lights"] == [] and missing.options["points"] == points and missing.data == {}
        direct = SimpleNamespace(version=1, title="Direct", data={}, options={"points": points, "lights": ["light.direct"]})
        assert await async_migrate_entry(hass, direct)
        assert direct.options["lights"] == ["light.direct"] and direct.options["points"] == points
        count = len(updates)
        assert await async_migrate_entry(hass, direct) and len(updates) == count
    assert al.options == original
    print("ok: migration copies lights, preserves points/direct mode, leaves AL untouched")


asyncio.run(main())
