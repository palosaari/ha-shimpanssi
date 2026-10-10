"""Config flow for Shimpanssi integration."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_MAC

from .const import DOMAIN


class ShimpanssiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Shimpanssi."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            mac = user_input[CONF_MAC]

            # Simple validation for MAC address format
            if len(mac.split(":")) == 6:
                # Use the generalized name for the integration instance
                return self.async_create_entry(
                    title=f"Shimano E-Bike ({mac})", data=user_input
                )

            # Error key matches the translation file (fi.json / en.json)
            errors["base"] = "invalid_mac"

        data_schema = vol.Schema({vol.Required(CONF_MAC): str})

        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )
