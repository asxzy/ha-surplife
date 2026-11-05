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
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATIC_EFFECT
from .coordinator import SurplifeCoordinator, SurplifeRuntime
from .effect_profile import profile_for
from .groups import SurplifeGroupCoordinator, is_group_entry
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


#: Both a controller and a group present this surface to the entities, so the
#: light below is written once and works for either. A group's coordinator
#: forwards every ``async_set_*`` to its members, which is what keeps this file
#: free of a second implementation of "recolour the effect" or of the
#: optimistic colour bookkeeping.
LightCoordinator = SurplifeCoordinator | SurplifeGroupCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the light entity for a controller, or for a group."""
    if is_group_entry(entry):
        async_add_entities([SurplifeGroupLight(hass.data[DOMAIN][entry.entry_id])])
        return
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SurplifeLight(coordinator)])


class SurplifeLight(CoordinatorEntity[LightCoordinator], LightEntity):
    """One 0xB6 controller as a Home Assistant light."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_color_modes = SUPPORTED_COLOR_MODES
    _attr_supported_features = LightEntityFeature.EFFECT
    #: Immutable, because Home Assistant compares effect lists by identity in
    #: some paths and commandeers the list if it is a mutable class default.
    #:
    #: ``Static`` comes first so the card can always leave pattern mode. Without
    #: it, a colour request while a pattern runs recolours the pattern (see
    #: :meth:`SurplifeCoordinator.async_recolour_active_effect`) and there would
    #: be no way back to a plain colour from the card.
    _attr_effect_list: tuple[str, ...] = (STATIC_EFFECT, *EFFECT_NAMES.values())

    def __init__(self, coordinator: LightCoordinator) -> None:
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
    def _fully_on(self) -> bool:
        """Whether everything this entity drives is powered.

        On a controller that is just its own power flag. A group reports on when
        *any* member is on, which is the right reading for the card's toggle but
        the wrong one for deciding whether ``turn_on`` still has work to do — a
        half-lit roof has to turn the dark strips back on.
        """
        return self._runtime.is_on

    @property
    def is_on(self) -> bool:
        """Whether the strip is on."""
        return self._runtime.is_on

    @property
    def color_mode(self) -> ColorMode:
        """Report the channel mix actually driving the strip."""
        mode = self._runtime.color_mode
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
        """The colour channels of the current mix.

        Derived from :attr:`rgbw_color` rather than tracked separately: Home
        Assistant fills ``rgb_color`` in from ``rgbw_color`` anyway when the
        light only advertises RGBW, and two independent sources could disagree.
        ``None`` in white-only mode, where the W channel is the light source.
        """
        mix = self._runtime.rgbw_mix
        return None if mix is None else (mix[0], mix[1], mix[2])

    @property
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """The full RGBW mix, or ``None`` in white-only mode.

        A request for white arrives as ``(0, 0, 0, w)``: no colour at all, only
        the white LED. That is the point of this property — ``rgb_color`` alone
        cannot express it without falling back to simulated white.

        This is never ``None`` while :attr:`color_mode` says ``rgbw``. The
        frontend reads the colour wheel position and the white slider value
        from this attribute, so ``None`` here removes the colour controls from
        the card entirely.

        While an effect is running the wheel shows the effect's **first**
        colour. It used to show the last colour commanded anywhere, which meant
        the wheel's position disagreed with what the strip was doing and the
        first touch of it replaced a multi-colour palette with a single colour.
        """
        runtime = self._runtime
        if runtime.effect_name and runtime.palette:
            first = runtime.palette[0]
            white = first[3] if len(first) > 3 else 0
            return (first[0], first[1], first[2], round(white * 255 / 100))
        return runtime.rgbw_mix

    @property
    def effect(self) -> str | None:
        """The active effect, or ``Static`` when the strip is a flat colour.

        Reporting ``Static`` rather than ``None`` keeps the dropdown showing
        which mode the strip is in, so leaving pattern mode is a visible choice
        instead of a blank.
        """
        effect = self._runtime.effect_name
        return effect if effect else STATIC_EFFECT

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
        attributes: dict[str, Any] = {
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
        return attributes

    # -- commands ----------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on, applying any requested colour, brightness or effect."""
        runtime = self._runtime

        if (effect := kwargs.get(ATTR_EFFECT)) is not None:
            if effect == STATIC_EFFECT:
                # Leave pattern mode: drive the remembered colour as a flat,
                # static colour. Clearing the effect is what the next colour
                # request needs in order to stop recolouring a pattern.
                #
                # ``rgbw_mix`` is ``None`` when the strip is white-only, where
                # the last colour is the right thing to restore rather than the
                # white level -- a white request would otherwise be re-sent as
                # an RGB zero, which is dark.
                mix = self._runtime.rgbw_mix
                rgb = (
                    mix[:3]
                    if mix is not None
                    else (self._runtime.rgb or (255, 255, 255))
                )
                await self.coordinator.async_set_rgb(rgb, self._runtime.brightness)
                if not self._fully_on:
                    await self.coordinator.async_set_power(True)
                return
            # No palette is passed: the coordinator restores the colours saved
            # for this effect in the Configure panel, falling back to whatever
            # is showing. Passing one colour here is what made multi-colour
            # effects impossible to configure from the light card.
            await self.coordinator.async_set_effect(effect)
            if not self._fully_on:
                await self.coordinator.async_set_power(True)
            return

        brightness = kwargs.get(ATTR_BRIGHTNESS, runtime.brightness)
        rgb = kwargs.get(ATTR_RGB_COLOR)
        rgbw = kwargs.get(ATTR_RGBW_COLOR)
        was_on = self._fully_on

        # The controller drives white and colour through separate channels, so
        # ``white`` selects the warm-white channel rather than an RGB value.
        # Without this, a ``white`` request would fall through to the brightness
        # branch and be sent as grey RGB.
        if (white := kwargs.get(ATTR_WHITE)) is not None:
            await self.coordinator.async_set_white(white)
        elif rgbw is not None:
            red, green, blue, white_level = rgbw
            # While a pattern is showing, a colour on the card means "recolour
            # this pattern", not "stop the pattern and show one flat colour".
            if not await self.coordinator.async_recolour_active_effect(
                [(red, green, blue, round(white_level * 100 / 255))], index=0
            ):
                await self.coordinator.async_set_rgbw(
                    (red, green, blue), white_level, brightness
                )
        elif rgb is not None:
            if not await self.coordinator.async_recolour_active_effect(
                [(rgb[0], rgb[1], rgb[2], 0)], index=0
            ):
                await self.coordinator.async_set_rgb(tuple(rgb), brightness)
        elif brightness != runtime.brightness and was_on:
            await self.coordinator.async_set_brightness(brightness)
        elif not was_on:
            # Re-apply what was showing so turning on is deterministic rather
            # than restoring whatever the strip last showed. A strip that was
            # mid-pattern comes back in that pattern; otherwise the mode decides
            # the channel: last used for white comes back white, last used for
            # colour comes back in colour, never driven comes back neutral white.
            if runtime.effect_name:
                await self.coordinator.async_set_effect(runtime.effect_name)
            elif runtime.color_mode == "white":
                await self.coordinator.async_set_white(runtime.white_level)
            elif (mix := runtime.rgbw_mix) is not None:
                red, green, blue, white_level = mix
                await self.coordinator.async_set_rgbw(
                    (red, green, blue), white_level, brightness
                )

        if not was_on:
            await self.coordinator.async_set_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the strip off."""
        await self.coordinator.async_set_power(False)


class SurplifeGroupLight(SurplifeLight):
    """The group's light: identical controls, one device for the whole roof.

    Everything that makes a controller's light behave — the ``Static``
    pseudo-effect, recolouring the running effect from the wheel, the optimistic
    colour tracking — comes from :class:`SurplifeLight` unchanged. The only
    difference is the device it belongs to and the membership attributes, which
    are the contract the ``surplife.*`` services read to expand a group.
    """

    def __init__(self, coordinator: SurplifeGroupCoordinator) -> None:
        """Initialise the group light from the group coordinator."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_group_light"

    @property
    def group_coordinator(self) -> SurplifeGroupCoordinator:
        """The group coordinator, typed."""
        return self.coordinator

    @property
    def _fully_on(self) -> bool:
        """Whether every member is powered, not merely one."""
        return self.group_coordinator.all_members_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the membership alongside the usual effect details.

        ``surplife_group_members`` is what the services read to expand the group
        into its controllers, so it is part of the contract rather than
        decoration. ``entity_id`` mirrors what Home Assistant's own light group
        publishes, for anyone debugging from the states page.
        """
        attributes = super().extra_state_attributes
        # A group entry id is not a controller, and the services would try to
        # resolve it. The member list is the honest answer, so it replaces the
        # single-controller key rather than sitting next to it.
        attributes.pop("surplife_entry_id", None)
        members = self.group_coordinator.member_entry_ids
        attributes["surplife_group_members"] = list(members)
        attributes[ATTR_ENTITY_ID] = list(self.group_coordinator.member_entity_ids)
        attributes["members"] = [
            entry.title
            for member in members
            if (entry := self.hass.config_entries.async_get_entry(member))
        ]
        return attributes
