"""The light platform: the primary control surface.

Maps Home Assistant's light model onto the controller's three modes:

* **white** — ``E0 01 ... B1`` warm-white, no cold-white LED on this hardware
* **rgb** — ``E0 01 ... A1`` whole-strip colour
* **effect** — ``E1 21`` native animated effect

Colour and brightness are tracked optimistically because the firmware does not
echo them; see :mod:`.coordinator`.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SurplifeCoordinator, SurplifeRuntime
from .protocol.constants import EFFECT_NAMES

_LOGGER = logging.getLogger(__name__)

#: The device has a warm-white channel only; there is no cold-white LED, so
#: ``ColorMode.COLOR_TEMP`` would be a lie.
SUPPORTED_COLOR_MODES = {ColorMode.RGB, ColorMode.WHITE}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light entity for a controller."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SurplifeLight(coordinator)])


class SurplifeLight(CoordinatorEntity[SurplifeCoordinator], LightEntity):
    """One 0xB6 controller as a Home Assistant light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = SUPPORTED_COLOR_MODES
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(EFFECT_NAMES.values())

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        """Initialise the light."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_light"

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    # -- state -------------------------------------------------------------

    @property
    def _runtime(self) -> SurplifeRuntime:
        return self.coordinator.runtime

    @property
    def is_on(self) -> bool:
        """Whether the strip is on."""
        return self._runtime.is_on

    @property
    def color_mode(self) -> ColorMode:
        """Report white mode when the white channel is driving the strip."""
        return ColorMode.WHITE if self._runtime.color_mode == "white" else ColorMode.RGB

    @property
    def brightness(self) -> int | None:
        """Brightness in Home Assistant's 0-255 range."""
        if not self.is_on:
            return None
        return self._runtime.brightness

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """The commanded RGB colour, or ``None`` in white mode."""
        return self._runtime.rgb

    @property
    def effect(self) -> str | None:
        """The active native effect name, if any."""
        return self._runtime.effect_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the config-entry id.

        The ``surplife.*`` services accept light entity ids and need to resolve
        each one back to its coordinator. Putting the entry id in the state
        attributes keeps that lookup to a plain state read, so the services do
        not have to walk the entity registry.
        """
        return {
            "surplife_entry_id": self.coordinator.entry.entry_id,
            "pattern": self._runtime.state.pattern if self._runtime.state else None,
        }

    # -- commands ----------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, applying any requested colour, brightness or effect."""
        runtime = self._runtime

        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            await self.coordinator.async_set_effect(
                effect,
                colors=[runtime.rgb or (255, 255, 255)],
            )
            if not runtime.is_on:
                await self.coordinator.async_set_power(True)
            return

        brightness = kwargs.get(ATTR_BRIGHTNESS, runtime.brightness)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        was_on = runtime.is_on

        if rgb is not None:
            await self.coordinator.async_set_rgb(tuple(rgb), brightness)
        elif brightness != runtime.brightness and was_on:
            await self.coordinator.async_set_brightness(brightness)
        elif not was_on:
            # Re-apply the last known colour so turning on is deterministic
            # rather than restoring whatever the strip last showed.
            if runtime.white_level and runtime.rgb is None:
                await self.coordinator.async_set_white(runtime.white_level)
            else:
                await self.coordinator.async_set_rgb(
                    runtime.rgb or (255, 255, 255), brightness
                )

        if not was_on:
            await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the strip off."""
        await self.coordinator.async_set_power(False)
