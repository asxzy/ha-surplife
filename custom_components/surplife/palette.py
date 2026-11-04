"""The effect palette editor, as a plain entity on the device page.

The light entity can carry exactly **one** colour. Home Assistant's light model
has no concept of a colour list, and its dialog has no palette control, so a
multi-colour effect like ``Chase`` or ``Static Gradient`` could only be built
through the Configure dialog -- two menus deep, behind a gear icon, which is
exactly where nobody looks.

This platform puts the palette where the other controls already are: a text
entity on the device page that shows the colours the running effect is using
and accepts a new list.

**Why text rather than one colour entity per slot.** A ``light`` per slot would
put five colour wheels on the device page, each of which looks like a light you
can turn on, and none of which says which slot it is. A single entity that
shows the whole ordered palette in its state -- ``#ff0000, #00ff00`` -- makes
the order visible at a glance and is one control, not eight.

The grammar is the same colour vocabulary the services and the CLI accept, so
there is one thing to learn rather than two. Curated, tap-only palettes stay
available as scenes; this is the direct editor next to them.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATIC_EFFECT
from .coordinator import SurplifeCoordinator
from .effect_profile import max_colours, profile_for
from .protocol.spec import format_palette, parse_palette

_LOGGER = logging.getLogger(__name__)

#: Longest palette string accepted. Eight colours of ``#rrggbb w100`` is about
#: 120 characters, so this is generous while still bounded.
MAX_PALETTE_LENGTH: Final = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the palette entity."""
    coordinator: SurplifeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SurplifeEffectPaletteText(coordinator)])


class SurplifeEffectPaletteText(
    CoordinatorEntity[SurplifeCoordinator], TextEntity, RestoreEntity
):
    """The ordered colour list an effect is using, editable in place.

    Setting it recolours the running effect instead of cancelling it, which is
    the whole point: the light card's single colour wheel stops the pattern,
    so there was no way to say "these three colours, in this order" from
    anything a user actually reaches for.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "effect_palette"
    _attr_icon = "mdi:palette"
    # This is the everyday control for a multi-colour effect, so unlike the
    # per-LED pattern text it is enabled by default. Someone with a
    # single-colour strip can hide it; someone with `Chase` should not have to
    # find it first.
    _attr_entity_registry_enabled_default = True
    _attr_native_min = 0
    _attr_native_max = MAX_PALETTE_LENGTH
    _attr_mode = TextMode.TEXT

    def __init__(self, coordinator: SurplifeCoordinator) -> None:
        """Initialise the palette entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_effect_palette"
        # Start from whatever the strip is showing so the field is never blank
        # for a device that already has a colour.
        self._attr_native_value = ""

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry."""
        return self.coordinator.device_info

    @property
    def native_value(self) -> str | None:
        """The running effect's palette, as a comma-separated list.

        Read from the runtime rather than from the last value typed, so the
        entity also reflects palettes set by the Configure panel, a scene, or
        an automation. ``Static`` has no palette, so it reports the flat colour
        instead -- that is the colour the strip is actually showing.
        """
        runtime = self.coordinator.runtime
        if runtime.effect_name:
            colours = runtime.palette
        elif (mix := runtime.rgbw_mix) is not None:
            colours = [mix]
        else:
            return None
        text = format_palette(colours)
        return text or None

    async def async_added_to_hass(self) -> None:
        """Nothing to restore: the palette is read from the coordinator.

        ``RestoreEntity`` is inherited only so the entity keeps its place in the
        registry across restarts; the value itself always comes from the
        runtime, because a stale restored string would show colours the strip
        is not using.
        """
        await super().async_added_to_hass()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say what the current effect accepts, so the field is self-explaining."""
        runtime = self.coordinator.runtime
        name = runtime.effect_name or STATIC_EFFECT
        profile = profile_for(name)
        if name == STATIC_EFFECT:
            cap = 1
            hint = "one colour; pick a pattern to use a palette"
        elif profile.palette.value == "generated":
            cap = 0
            hint = "this effect paints its own colours; a palette is ignored"
        else:
            cap = max_colours(name)
            hint = f"up to {cap} colour(s) for this effect"
        return {
            "effect": name,
            "max_colours": cap,
            "hint": hint,
            "grammar": (
                "comma-separated; colour names, #rrggbb, or r,g,b; "
                "append w<n> for the white level"
            ),
            "example": "#ff0000, #00ff00 w30, #0000ff",
        }

    async def async_set_value(self, value: str) -> None:
        """Apply a new palette to the running effect.

        Raises:
            HomeAssistantError: if the text does not parse, the effect takes no
                palette, or the list is longer than the effect's cap.
        """
        text = value.strip()
        if not text:
            raise HomeAssistantError("the palette is empty")

        runtime = self.coordinator.runtime
        name = runtime.effect_name
        if not name:
            raise HomeAssistantError(
                "no effect is running; pick one from the effect selector first, "
                "or use the light card for a single colour"
            )

        profile = profile_for(name)
        if profile.palette.value == "generated":
            raise HomeAssistantError(
                f"{name} paints its own colours, so a palette would be ignored"
            )

        cap = max_colours(name)
        try:
            colours = parse_palette(text, max_colours=cap)
        except ValueError as err:
            raise HomeAssistantError(f"invalid palette: {err}") from err

        # Go through the same path the light card's wheel uses, so a palette set
        # here behaves exactly like one set there: the effect keeps running and
        # its colours are persisted for the next time it is picked.
        if not await self.coordinator.async_recolour_active_effect(colours):
            raise HomeAssistantError(
                f"{name} is not currently showing; pick the effect again first"
            )
        self.async_write_ha_state()
