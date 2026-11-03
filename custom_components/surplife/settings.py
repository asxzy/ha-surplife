"""Per-effect and per-look settings, remembered per controller.

The wire protocol has no notion of "remembered settings": every ``E1 21`` and
``E1 26`` command carries the full payload, so whatever the app showed last has
to be stored somewhere if it is to be shown again. The app keeps it in its own
device record. Here it lives in the config entry's options.

That choice matters. Options are the only writable, validated, reload-aware
place a config entry has, and they are what the Configure dialog edits. Keeping
the palette and the effect parameters there means:

* they survive a Home Assistant restart without a separate store file;
* the Configure dialog can edit exactly what the entities read;
* a scene or an automation can override them for one call without losing them.

**Units.** Colours are ``(r, g, b, w)`` with the white level as a **0-100**
percentage, which is the protocol layer's unit for the W channel everywhere
else. Home Assistant's 0-255 ``rgbw_color`` is converted at the HTTP boundary,
not here.

**Forward compatibility.** Everything read out of the options is validated and
anything invalid is dropped rather than raised. Options can be edited by hand
in ``.storage/core.config_entries``, and one bad colour should not stop the
integration from loading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any, Final

_LOGGER = logging.getLogger(__name__)

#: ``(r, g, b, w)``; ``w`` is the W-channel level 0-100.
Colour = tuple[int, int, int, int]

#: Option keys. Kept short because they end up in every config entry's YAML
#: representation and in the export produced by ``ha config entry``.
EFFECTS_KEY: Final = "effects"
LOOKS_KEY: Final = "looks"


@dataclass(frozen=True, slots=True)
class EffectSettings:
    """What one native effect should be driven with.

    Every field is optional so that "not configured yet" is distinguishable
    from "configured to the default value". A missing value falls back to the
    runtime's current setting, which is what makes the light card's effect
    dropdown behave sensibly before the panel has ever been opened.

    Attributes:
        colors: Palette, most-used first. Empty means "use whatever colour the
            strip is already showing".
        speed: Animation speed 0-100.
        density: Animation density 0-100.
        direction: ``1`` left-to-right, ``2`` right-to-left.
    """

    colors: tuple[Colour, ...] = ()
    speed: int | None = None
    density: int | None = None
    direction: int | None = None


@dataclass(frozen=True, slots=True)
class LookSettings:
    """What one per-LED look should be rendered with.

    Attributes:
        palette: Colours to hand the look's builder. Empty means the look's own
            built-in palette.
        speed: Per-LED animation speed 0-100.
        density: Per-LED animation density 0-100.
        direction: ``1`` left-to-right, ``2`` right-to-left.
        effect: Per-LED effect id 0-8.
    """

    palette: tuple[Colour, ...] = ()
    speed: int = 100
    density: int = 80
    direction: int = 1
    effect: int = 0


# -- validation -------------------------------------------------------------


def _bounded(value: Any, low: int, high: int) -> int | None:
    """Return ``value`` as an int clamped to ``[low, high]``, or ``None``."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return max(low, min(high, number))


def _colour(raw: Any) -> Colour | None:
    """Validate one stored colour.

    Accepts a 3- or 4-element sequence. A 3-element sequence is RGB with the
    white channel off, which is how the colour picker reports a colour.
    """
    if not isinstance(raw, (list, tuple)) or not 3 <= len(raw) <= 4:
        return None
    channels = [_bounded(value, 0, 255) for value in raw[:3]]
    if any(value is None for value in channels):
        return None
    white = _bounded(raw[3], 0, 100) if len(raw) == 4 else 0
    assert white is not None  # _bounded only returns None on a bad type
    return (channels[0], channels[1], channels[2], white)  # type: ignore[return-value]


def _palette(raw: Any) -> tuple[Colour, ...]:
    """Validate a stored palette, dropping entries that do not parse."""
    if not isinstance(raw, (list, tuple)):
        return ()
    colours = []
    for item in raw:
        colour = _colour(item)
        if colour is None:
            _LOGGER.debug("ignoring invalid stored colour %r", item)
            continue
        colours.append(colour)
    return tuple(colours)


def effect_settings(options: dict[str, Any] | None, name: str) -> EffectSettings | None:
    """The stored settings for one effect, or ``None`` if never configured."""
    stored = (options or {}).get(EFFECTS_KEY)
    if not isinstance(stored, dict):
        return None
    raw = stored.get(name)
    if not isinstance(raw, dict):
        return None
    return EffectSettings(
        colors=_palette(raw.get("colors")),
        speed=_bounded(raw.get("speed"), 0, 100),
        density=_bounded(raw.get("density"), 0, 100),
        direction=_bounded(raw.get("direction"), 1, 2),
    )


def look_settings(options: dict[str, Any] | None, name: str) -> LookSettings | None:
    """The stored settings for one per-LED look, or ``None`` if never set."""
    stored = (options or {}).get(LOOKS_KEY)
    if not isinstance(stored, dict):
        return None
    raw = stored.get(name)
    if not isinstance(raw, dict):
        return None
    defaults = LookSettings()
    return LookSettings(
        palette=_palette(raw.get("palette")),
        speed=_bounded(raw.get("speed"), 0, 100) or defaults.speed,
        density=_bounded(raw.get("density"), 0, 100) or defaults.density,
        direction=_bounded(raw.get("direction"), 1, 2) or defaults.direction,
        effect=_bounded(raw.get("effect"), 0, 8) or 0,
    )


# -- writing ----------------------------------------------------------------


def _to_raw(settings: EffectSettings | LookSettings) -> dict[str, Any]:
    """Serialise settings, omitting unset values so the file stays readable."""
    raw: dict[str, Any] = {}
    if isinstance(settings, EffectSettings):
        if settings.colors:
            raw["colors"] = [list(colour) for colour in settings.colors]
        for key in ("speed", "density", "direction"):
            value = getattr(settings, key)
            if value is not None:
                raw[key] = value
        return raw

    if settings.palette:
        raw["palette"] = [list(colour) for colour in settings.palette]
    raw["speed"] = settings.speed
    raw["density"] = settings.density
    raw["direction"] = settings.direction
    raw["effect"] = settings.effect
    return raw


def with_effect(
    options: dict[str, Any] | None, name: str, settings: EffectSettings
) -> dict[str, Any]:
    """Return a copy of ``options`` with one effect's settings replaced.

    Everything else in the options dict -- including keys this integration does
    not own -- is preserved, so saving the panel can never drop the poll
    interval or a future option field.
    """
    updated = dict(options or {})
    effects = dict(updated.get(EFFECTS_KEY) or {})
    effects[name] = _to_raw(settings)
    updated[EFFECTS_KEY] = effects
    return updated


def with_look(
    options: dict[str, Any] | None, name: str, settings: LookSettings
) -> dict[str, Any]:
    """Return a copy of ``options`` with one look's settings replaced."""
    updated = dict(options or {})
    looks = dict(updated.get(LOOKS_KEY) or {})
    looks[name] = _to_raw(settings)
    updated[LOOKS_KEY] = looks
    return updated


def merge_colours(
    palette: tuple[Colour, ...], overrides: dict[str, Any] | None
) -> tuple[Colour, ...]:
    """Overlay stored colours on a look's default palette, position by position.

    A look with two default colours and one stored override should not lose its
    second colour, so missing entries keep the default rather than being cut
    off. This is the per-LED equivalent of "inherit what you did not set".
    """
    stored = _palette((overrides or {}).get("palette"))
    if not stored:
        return palette
    merged = list(palette)
    for index, colour in enumerate(stored):
        if index < len(merged):
            merged[index] = colour
        else:
            merged.append(colour)
    return tuple(merged)


def replace_settings(settings: EffectSettings, **changes: Any) -> EffectSettings:
    """Return ``settings`` with some fields replaced."""
    return replace(settings, **changes)
