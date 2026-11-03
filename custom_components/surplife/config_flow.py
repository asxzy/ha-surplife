"""Config flow: discover controllers over UDP, or accept a manual IP."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_FIRMWARE_INFO,
    CONF_HW_VERSION,
    CONF_LED_COUNT,
    CONF_MAC,
    CONF_MODEL_INFO,
    DEFAULT_NAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .protocol import SurplifeClient, SurplifeError, async_discover, is_supported

_LOGGER = logging.getLogger(__name__)

MANUAL_OPTION = "__manual__"


def _default_name(host: str) -> str:
    """A usable fallback name when the user does not supply one.

    ``DEFAULT_NAME`` alone would make every controller in the house the same
    name, so the last octet is appended -- enough to tell three soffits apart
    without making the user type anything.
    """
    return f"{DEFAULT_NAME} {host.rsplit('.', 1)[-1]}"

MANUAL_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


class SurplifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow."""
        self._discovered: dict[str, Any] = {}
        self._pending: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer discovered controllers, or fall through to manual entry."""
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host == MANUAL_OPTION:
                return await self.async_step_manual()
            device = self._discovered.get(host)
            if device is None:
                return await self.async_step_manual()
            return await self._async_validate(
                host=device.host,
                name=user_input.get(CONF_NAME) or _default_name(device.host),
                mac=device.mac,
                hw_version=device.hw_version,
                model_info=device.model_info,
                firmware_info=device.firmware_info,
            )

        try:
            devices = await async_discover(timeout=3.0)
        except OSError as err:
            _LOGGER.debug("discovery failed: %s", err)
            devices = []

        supported = [device for device in devices if is_supported(device)]
        if not supported:
            return await self.async_step_manual()

        self._discovered = {device.host: device for device in supported}
        options = [
            SelectOptionDict(
                value=device.host,
                label=f"{device.host} ({device.mac})",
            )
            for device in supported
        ]
        options.append(SelectOptionDict(value=MANUAL_OPTION, label="Enter an IP address"))

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=supported[0].host): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    ),
                    # Asking for the name here rather than in a second step
                    # keeps adding a controller to one screen. Leaving it blank
                    # still gives a usable name, so it never blocks.
                    vol.Optional(CONF_NAME, default=""): str,
                }
            ),
            description_placeholders={"count": str(len(supported))},
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an IP address and optional name."""
        if user_input is not None:
            return await self._async_validate(
                host=user_input[CONF_HOST].strip(),
                name=user_input.get(CONF_NAME) or DEFAULT_NAME,
            )
        return self.async_show_form(step_id="manual", data_schema=MANUAL_SCHEMA)

    async def _async_validate(
        self,
        *,
        host: str,
        name: str,
        mac: str | None = None,
        hw_version: str | None = None,
        model_info: str | None = None,
        firmware_info: str | None = None,
    ) -> ConfigFlowResult:
        """Probe the controller and create the entry."""
        errors: dict[str, str] = {}
        state = None
        try:
            client = SurplifeClient(host)
            state = await asyncio.to_thread(client.get_state)
            await asyncio.to_thread(client.close)
        except (SurplifeError, OSError) as err:
            _LOGGER.debug("probe of %s failed: %s", host, err)
            errors["base"] = "cannot_connect"

        if state is not None and state.model != 0xB6:
            errors["base"] = "not_supported"

        if errors:
            schema = MANUAL_SCHEMA
            return self.async_show_form(
                step_id="manual", data_schema=schema, errors=errors
            )

        assert state is not None
        unique = mac or host
        await self.async_set_unique_id(unique)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        data = {
            CONF_HOST: host,
            CONF_NAME: name,
            CONF_MAC: mac or host,
            CONF_HW_VERSION: hw_version,
            CONF_MODEL_INFO: model_info,
            CONF_FIRMWARE_INFO: firmware_info,
            CONF_LED_COUNT: state.led_count,
        }
        return self.async_create_entry(title=name, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow."""
        return SurplifeOptionsFlow()


class SurplifeOptionsFlow(OptionsFlow):
    """Options: how often to poll."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "scan_interval",
                        default=self.config_entry.options.get(
                            "scan_interval", DEFAULT_SCAN_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=10, max=600, step=5, mode=NumberSelectorMode.BOX
                        )
                    )
                }
            ),
        )
