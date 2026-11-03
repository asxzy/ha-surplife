"""What each native effect actually needs from the user.

The wire protocol accepts up to eight palette colours on every ``E1 21``
effect, but no two effects use them the same way. ``Static Fill`` fills the
whole strip with one colour and ignores the rest. ``Rainbow Bridge`` generates
its own colours and ignores the palette entirely. Sending eight colours to
Static Fill is not an error; it just silently uses the first one.

This module records which is which, so the UI can ask for exactly the
parameters an effect uses and stay quiet about the rest. That is the difference
between "a colour picker that may or may not do anything" and a control panel
that matches the effect you picked.

**Where the classification comes from.** The wire protocol has no per-effect
parameter declaration — verified on hardware by sending one and then eight
colours to ``Static Gradient`` and reading back an identical state frame. The
grouping below therefore comes from the app's own shipped vocabulary
(``docs/app_evidence.md`` §3.3, ``docs/evidence/strings_colorful_modes.txt``),
which pairs a plain effect name with a ``Colorful`` variant:

===========================  =====================================
plain name (our effect id)   ``Colorful`` sibling in the app
===========================  =====================================
``Wave``                     ``Colorful waves``
``Meteor``                   ``Colorful Meteor``
``Horse Racing``             ``Colorful horse racing``
``Breathe``                  ``Colorful Breath``, ``Colorful breath``
``Jump``                     ``Colorful jump``, ``Colorful Jump``
``Strobe``                   ``Colorful Strobe``, ``Colorful strobe``
``Twinkling Stars``          ``ColorfulTwinklingStars``
===========================  =====================================

The existence of a distinct ``Colorful`` variant is the evidence that the plain
version is the **single-colour** one; otherwise the two names would be
redundant. Effects with no such sibling are treated as palette effects, which
is the safe default: the firmware accepts a palette and uses what it needs.

**Honesty note.** Only the ``Colorful`` split is directly evidenced. The exact
number of colours each effect renders cannot be measured on this hardware,
because the state frame never echoes colour and the firmware exposes no
read-back for it. So :attr:`EffectProfile.panel_colours` is what the *panel
offers*, not a wire limit; :func:`palette_for` only enforces the protocol's own
hard limits and never discards colours a caller passed for a palette effect.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .protocol.constants import EFFECT_NAMES

#: The protocol's hard ceiling, from ``E1 21`` byte 15.
MAX_PROTOCOL_COLOURS: Final = 8


class PaletteMode(StrEnum):
    """How an effect consumes the palette.

    Values are declared in the order the UI should offer them, and are used
    directly in the ``palette_mode`` attribute so they are readable in YAML.
    """

    #: One colour drives the whole strip. Extra colours are ignored.
    SINGLE = "single"
    #: The palette is painted across the strip in order.
    COLOURS = "colours"
    #: The effect generates its own colours; the palette is ignored.
    GENERATED = "generated"


@dataclass(frozen=True, slots=True)
class EffectProfile:
    """The user-facing parameters one effect accepts.

    Attributes:
        palette: How the palette is consumed.
        panel_colours: How many colour pickers the panel should show. Zero for
            an effect that paints its own colours.
        direction: Whether the direction control does anything.
        density: Whether the density control does anything.
        speed: Whether the speed control does anything.
        description: One line for the panel, phrased as what the strip does.
    """

    palette: PaletteMode = PaletteMode.COLOURS
    panel_colours: int = 2
    direction: bool = True
    density: bool = True
    speed: bool = True
    description: str = ""


#: Effects that paint their own colours and ignore the palette.
_GENERATED: Final = {
    "Rainbow Bridge": "Cycles a full rainbow across the run; the palette is ignored.",
}

#: Effects that fill with a single colour.
_SINGLE: Final = {
    "Static Fill": "Fills the whole run with one colour.",
    "Wave": "A band of one colour travelling along the run.",
    "Meteor": "A single-colour streak with a fading tail.",
    "Horse Racing": "Single-colour dashes racing along the run.",
    "Breathe": "The whole run fades up and down in one colour.",
    "Jump": "Single-colour pulses hopping along the run.",
    "Strobe": "The whole run flashes one colour.",
    "Twinkling Stars": "Single-colour dots twinkling on and off.",
}

#: How many pickers to offer for the palette effects. Two is enough for the
#: common two-tone holiday looks; gradient effects get the full protocol width
#: because their whole point is a multi-stop blend.
_PANEL_COLOURS: Final = {
    "Static Gradient": MAX_PROTOCOL_COLOURS,
    "Gradient Meteor": MAX_PROTOCOL_COLOURS,
    "Gradient Overlay": MAX_PROTOCOL_COLOURS,
    "Fireworks": 3,
    "Collision": 3,
    "Volcano": 3,
}

#: Per-effect one-liners. Effects not listed get a generic line, because
#: inventing a description would be worse than saying less.
_DESCRIPTIONS: Final = {
    "Streamer": "Bands of colour streaming along the run.",
    "Building Blocks": "Colour blocks stacking up along the run.",
    "Flowing Water": "A soft multi-colour current along the run.",
    "Chase": "Blocks chasing each other along the run.",
    "Cycle": "The palette cycling through the run.",
    "Warning": "Alternating colours, like hazard lights.",
    "Collision": "Colour blocks colliding and bouncing back.",
    "Fireworks": "Bursts of colour along the run.",
    "Comet": "Travelling heads with trailing tails.",
    "Gradient Meteor": "Comets whose tails blend across the palette.",
    "Volcano": "Colour erupting from a moving hot spot.",
    "Superluminal": "Fast streaks of colour along the run.",
    "Gradient Overlay": "A gradient laid over the moving pattern.",
    "Static Gradient": "A still blend from the first colour to the last.",
    "Static Fill": "Fills the whole run with one colour.",
    "Rainbow Bridge": "Cycles a full rainbow across the run.",
}

#: Effects whose direction byte is ignored by the firmware. The two static
#: effects have no movement to reverse; verified against the state frame, which
#: does not change when the direction byte flips for them.
_NO_DIRECTION: Final = {"Static Gradient", "Static Fill"}

#: Effects whose density byte is ignored by the firmware.
_NO_DENSITY: Final = {"Static Gradient", "Static Fill", "Strobe"}


def _canonical(name: str) -> str | None:
    """Resolve an effect name to the exact casing used by the effect list.

    The list ships ``Stars wink`` with a lowercase second word, so lookups are
    case-insensitive to stay tolerant of a hand-typed value.
    """
    return {key.lower(): key for key in EFFECT_NAMES.values()}.get(name.strip().lower())


def profile_for(name: str) -> EffectProfile:
    """Return the profile for an effect name.

    Args:
        name: Effect name as shown in the effect list.

    Returns:
        The matching profile, or a permissive default for an unknown name so
        an unrecognised effect still gets controls rather than none.
    """
    canonical = _canonical(name)
    if canonical is None:
        return EffectProfile()

    direction = canonical not in _NO_DIRECTION
    density = canonical not in _NO_DENSITY
    description = _DESCRIPTIONS.get(canonical, "An animated colour pattern.")

    if canonical in _GENERATED:
        return EffectProfile(
            palette=PaletteMode.GENERATED,
            panel_colours=0,
            direction=direction,
            density=density,
            description=description,
        )
    if canonical in _SINGLE:
        return EffectProfile(
            palette=PaletteMode.SINGLE,
            panel_colours=1,
            direction=direction,
            density=density,
            description=description,
        )
    return EffectProfile(
        palette=PaletteMode.COLOURS,
        panel_colours=_PANEL_COLOURS.get(canonical, 2),
        direction=direction,
        density=density,
        description=description,
    )


def palette_for(
    name: str, colours: list[tuple[int, ...]] | Sequence[tuple[int, ...]]
) -> list[tuple[int, ...]]:
    """Clamp a palette to what the protocol can carry.

    This deliberately does **not** trim to the panel's suggestion. A palette is
    only cut when the wire format cannot express it, or when the effect ignores
    colours entirely — otherwise an automation that passes three colours to
    ``Wave`` would be silently reduced to one on the strength of an inference.

    Args:
        name: The effect name.
        colours: The commanded palette.

    Returns:
        At least one colour, because the protocol rejects an empty palette.
    """
    ordered = list(colours)
    if profile_for(name).palette is PaletteMode.GENERATED:
        # A generated effect still needs a valid count on the wire; one colour
        # is the cheapest valid frame and the firmware ignores it.
        return ordered[:1] or [(255, 255, 255)]
    return ordered[:MAX_PROTOCOL_COLOURS] or [(255, 255, 255)]


def panel_colours(name: str) -> int:
    """How many colour pickers to show for an effect."""
    return profile_for(name).panel_colours
