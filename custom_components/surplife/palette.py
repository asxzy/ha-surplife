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

from typing import Any, Final

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, STATIC_EFFECT
from .coordinator import SurplifeCoordinator
from .effect_profile import PaletteMode, max_colours, profile_for
from .protocol.spec import format_palette, parse_palette

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


class SurplifeEffectPaletteText(CoordinatorEntity[SurplifeCoordinator], TextEntity):
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Say what the current effect accepts, so the field is self-explaining."""
        runtime = self.coordinator.runtime
        name = runtime.effect_name or STATIC_EFFECT
        profile = profile_for(name)
        if name == STATIC_EFFECT:
            cap = 1
            hint = "one colour; pick a pattern to use a palette"
        elif profile.palette is PaletteMode.GENERATED:
            cap = 0
            hint = "this effect paints its own colours; a palette is ignored"
        else:
            cap = max_colours(name)
            hint = f"up to {cap} colour{'s' if cap > 1 else ''} for this effect"
        return {
            "effect": name,
            "max_colours": cap,
            "hint": hint,
            "grammar": (
                "comma-separated; colour names, #rrggbb, or r,g,b; "
                "a w<n> token after a colour sets its white level"
            ),
            "example": "#ff0000, #00ff00, w30, #0000ff",
        }

    async def async_set_value(self, value: str) -> None:
        """Apply a new palette to the running effect.

        ``Static`` is the one case that is not a palette at all: it shows a flat
        colour, so a single colour here is applied as a flat colour rather than
        refused. Anything longer is still an error, because the strip would only
        ever show one of them.

        Raises:
            HomeAssistantError: if the text does not parse, the effect takes no
                palette, or the list is longer than the effect's cap.
        """
        text = value.strip()
        if not text:
            raise HomeAssistantError("the palette is empty")

        runtime = self.coordinator.runtime
        name = runtime.effect_name

        # No effect running: the strip is on a flat colour. One colour is a
        # sensible thing to type here; a list is not, and would silently use
        # only its first entry.
        if not name:
            try:
                colours = parse_palette(text, max_colours=1)
            except ValueError as err:
                raise HomeAssistantError(
                    f"invalid colour: {err}. With no effect running this field "
                    "takes a single colour; pick an effect to use a palette"
                ) from err
            red, green, blue, white = colours[0]
            await self.coordinator.async_set_rgbw(
                (red, green, blue), round(white * 255 / 100), runtime.brightness
            )
            self.async_write_ha_state()
            return

        profile = profile_for(name)
        if profile.palette is PaletteMode.GENERATED:
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
