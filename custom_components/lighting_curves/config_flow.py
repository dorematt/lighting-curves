import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
)

from . import DOMAIN
from .light_control import DEFAULTS

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
    """One independent entry per schedule."""

    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return LightingCurvesOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            name = user_input["name"].strip()
            if not name:
                errors["name"] = "empty_name"
            else:
                await self.async_set_unique_id(name.lower(), raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name, data={}, options={**DEFAULTS, "lights": [], "points": DEFAULT_POINTS},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("name"): TextSelector()}),
            errors=errors,
        )


class LightingCurvesOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})
        schema = {}
        for key in ("interval", "transition", "initial_transition", "manual_control_timeout"):
            schema[vol.Required(key, default=self.config_entry.options.get(key, DEFAULTS[key]))] = NumberSelector(
                NumberSelectorConfig(min=1 if key == "interval" else 0, step=1, mode="box", unit_of_measurement="s")
            )
        schema[vol.Required("manual_control_mode", default=self.config_entry.options.get(
            "manual_control_mode", DEFAULTS["manual_control_mode"]
        ))] = SelectSelector(SelectSelectorConfig(
            options=["pause_all", "pause_changed"], translation_key="manual_control_mode",
        ))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
