"""The light platform: the primary control surface.

Maps Home Assistant's light model onto the controller's three modes:

* **white** — ``E0 01 ... B1`` white channel only
* **rgbw** — ``E0 01 ... 00`` colour channels and W channel together
* **effect** — ``E1 21`` native animated effect

The strip has a single white LED (warm white) and no cold-white LED, so the
colour model is RGBW rather than RGBWW/CCT: a colour picker plus one white
level. Requesting white lights that LED instead of mixing the three colour LEDs
into an approximate white, which is brighter and cleaner.

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
    ATTR_RGBW_COLOR,
    ATTR_WHITE,
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
from .effect_profile import profile_for
from .protocol.constants import EFFECT_NAMES

_LOGGER = logging.getLogger(__name__)


def _hex(colour: tuple[int, ...]) -> str:
    """Render an RGB triple as ``#rrggbb``."""
    return "#{:02x}{:02x}{:02x}".format(*colour[:3])


def _hexw(colour: tuple[int, ...]) -> str:
    """Render an RGBW colour as ``#rrggbb`` plus its W level.

    The W level is a separate channel, not a shade of the colour, so appending
    it as a percentage is more honest than pretending it is part of the hex. A
    zero level is omitted, because that is just a colour with the white LED
    off.
    """
    if len(colour) == 4 and colour[3]:
        return f"{_hex(colour)} w{colour[3]}"
    return _hex(colour)


#: The controller drives four channels: R, G, B and a single warm-white LED.
#: It has no cold-white LED, so ``ColorMode.COLOR_TEMP`` would be a lie and a
#: temperature slider would do nothing. ``RGBW`` is the honest model: a colour
#: picker *and* a W channel that can be lit on its own, which is how a real
#: white is requested instead of three colour LEDs imitating one.
#:
#: ``WHITE`` is kept alongside it so ``light.turn_on`` with ``white:`` still
#: works, and so the entity can report a pure-white state.
SUPPORTED_COLOR_MODES = {ColorMode.RGBW, ColorMode.WHITE}


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
    #: Immutable, because Home Assistant compares effect lists by identity in
    #: some paths and commandeers the list if it is a mutable class default.
    _attr_effect_list: tuple[str, ...] = tuple(EFFECT_NAMES.values())

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
        """Report the channel mix actually driving the strip."""
        mode = self._runtime.color_mode
        if mode == "rgbw":
            return ColorMode.RGBW
        if mode == "white":
            return ColorMode.WHITE
        return ColorMode.RGBW

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
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """The commanded RGBW mix, or ``None`` outside RGBW mode.

        A request for white arrives as ``(0, 0, 0, w)``: no colour at all, only
        the white LED. That is the point of this property — ``rgb_color`` alone
        cannot express it without falling back to simulated white.
        """
        if self._runtime.color_mode != "rgbw":
            return None
        return self._runtime.rgbw

    @property
    def effect(self) -> str | None:
        """The active native effect name, if any."""
        return self._runtime.effect_name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the config-entry id and the active effect's capabilities.

        The ``surplife.*`` services accept light entity ids and need to resolve
        each one back to its coordinator. Putting the entry id in the state
        attributes keeps that lookup to a plain state read, so the services do
        not have to walk the entity registry.

        The effect details let a dashboard show the controls that match what is
        running — the palette the effect was given, and which of the speed,
        density and direction sliders it responds to.
        """
        effect = self._runtime.effect_name
        profile = profile_for(effect) if effect else None
        return {
            "surplife_entry_id": self.coordinator.entry.entry_id,
            "pattern": self._runtime.state.pattern if self._runtime.state else None,
            "palette": [
                _hex(colour) if len(colour) == 3 else _hexw(colour)
                for colour in self._runtime.palette
            ],
            "effect_speed": self._runtime.effect_speed,
            "effect_density": self._runtime.effect_density,
            "effect_direction": (
                "left_to_right"
                if self._runtime.effect_direction == 1
                else "right_to_left"
            ),
            "palette_mode": profile.palette.value if profile else None,
            "effect_uses_density": profile.density if profile else None,
            "effect_uses_direction": profile.direction if profile else None,
        }

    # -- commands ----------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, applying any requested colour, brightness or effect."""
        runtime = self._runtime

        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            # No palette is passed: the coordinator restores the colours saved
            # for this effect in the Configure panel, falling back to whatever
            # is showing. Passing one colour here is what made multi-colour
            # effects impossible to configure from the light card.
            await self.coordinator.async_set_effect(effect)
            if not runtime.is_on:
                await self.coordinator.async_set_power(True)
            return

        brightness = kwargs.get(ATTR_BRIGHTNESS, runtime.brightness)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        rgbw = kwargs.get(ATTR_RGBW_COLOR)
        was_on = runtime.is_on

        # The controller drives white and colour through separate channels, so
        # ``white`` selects the warm-white channel rather than an RGB value.
        # Without this, a ``white`` request would fall through to the brightness
        # branch and be sent as grey RGB.
        if (white := kwargs.get(ATTR_WHITE)) is not None:
            await self.coordinator.async_set_white(white)
        elif rgbw is not None:
            red, green, blue, white_level = rgbw
            await self.coordinator.async_set_rgbw(
                (red, green, blue), white_level, brightness
            )
        elif rgb is not None:
            await self.coordinator.async_set_rgb(tuple(rgb), brightness)
        elif brightness != runtime.brightness and was_on:
            await self.coordinator.async_set_brightness(brightness)
        elif not was_on:
            # Re-apply the last known colour so turning on is deterministic
            # rather than restoring whatever the strip last showed.
            if runtime.rgbw is not None:
                red, green, blue, white_level = runtime.rgbw
                await self.coordinator.async_set_rgbw(
                    (red, green, blue), white_level, brightness
                )
            elif runtime.white_level and runtime.rgb is None:
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
