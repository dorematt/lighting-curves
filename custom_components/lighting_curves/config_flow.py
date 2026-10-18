from homeassistant.config_entries import ConfigFlow

from . import DOMAIN


class LightingCurvesConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        # ponytail: single empty step; rooms and lights get added once the schedule model exists.
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Lighting Curves", data={})
