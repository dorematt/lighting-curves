import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.helpers.selector import EntitySelector, EntitySelectorConfig

from . import DOMAIN

# A starting curve: night, fade up 05:00-06:00, day, fade down around sunset,
# evening, fade to night 22:30-23:00.
DEFAULT_POINTS = [
    {"time": "05:00", "brightness": 3, "color_temp_kelvin": 2700},
    {"time": "06:00", "brightness": 100, "color_temp_kelvin": 6500},
    {"time": "sunset-45", "brightness": 100, "color_temp_kelvin": 6500},
    {"time": "sunset+45", "brightness": 35, "color_temp_kelvin": 2700},
    {"time": "22:30", "brightness": 35, "color_temp_kelvin": 2700},
    {"time": "23:00", "brightness": 3, "color_temp_kelvin": 2700},
]


class LightingCurvesConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per Adaptive Lighting switch."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            switch = user_input["switch"]
            await self.async_set_unique_id(switch)
            self._abort_if_unique_id_configured()
            state = self.hass.states.get(switch)
            # Only the main switch of an Adaptive Lighting entry carries manual_control.
            if state is None or "manual_control" not in state.attributes:
                errors["switch"] = "not_main_switch"
            else:
                return self.async_create_entry(
                    title=state.name.removeprefix("Adaptive Lighting: "),
                    data={"switch": switch},
                    options={"points": DEFAULT_POINTS},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("switch"): EntitySelector(
                        EntitySelectorConfig(integration="adaptive_lighting", domain="switch")
                    )
                }
            ),
            errors=errors,
        )
