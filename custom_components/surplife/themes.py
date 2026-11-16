"""Date-driven themes: what a given day should look like.

The companion to :mod:`.scenes`. A *scene* is a recipe the user wrote; a
*theme* is one the integration generates. Both end up as a :class:`.Scene`
applied through the coordinator, so a theme can never do anything a scene
cannot, and every existing apply path — groups included — works unchanged.

Three rules, from the request:

1. **The theme belongs to the date.** A holiday or a season is resolved from
   the calendar, so "Christmas" only appears around Christmas. Chinese
   festivals are included and are resolved with the same library Home
   Assistant core's own ``holiday`` integration uses (:mod:`holidays`), which
   derives them from the lunar calendar rather than a hand-maintained table.
2. **A theme is one coherent style.** A theme is not a bag of random colours
   and effects. It picks a small palette and a single motion, and every part
   of the display — palette, effect, pattern, speed, density — is derived
   from that one decision. The test suite asserts the coherence properties
   (a limited palette, an effect that actually consumes it, one mode per
   display).
3. **Every run is fresh.** Unlike :func:`.scenes.select_for_date`, whose pick
   is seeded so a day is stable across restarts, a theme is drawn from the
   process RNG with no seed. Two calls in a row are expected to differ; the
   *date* constrains what can be drawn, not which drawing is chosen.

**Why the generation is not a stored set.** The candidate palettes and
motions below are ingredients, not scenes. Nothing is persisted, nothing is
looked up by name, and the number of possible outputs is the product of the
palette, pattern, speed, density and direction draws — far larger than the
ingredient lists. A user who calls the service every morning gets a different
rendering each time; a user who calls it twice on the same morning is meant to
see two.
"""

from __future__ import annotations

import colorsys
import datetime as dt
import random
from dataclasses import dataclass
from typing import Final

from .protocol.constants import EFFECT_NAME_TO_ID
from .scenes.model import Scene
from .scribble_looks import LOOKS, Paint

Color = tuple[int, int, int]

# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

#: The country whose public holidays carry the Chinese festivals. ``holidays``
#: resolves Spring Festival, Dragon Boat, Mid-Autumn and the rest from the
#: lunar calendar, so no date table has to be maintained here. It is the same
#: library and the same version pin as HA core's ``holiday`` integration.
COUNTRY: Final = "CN"

#: English names, so the resolved festival is stable and readable in logs and
#: attributes. ``holidays`` defaults to the country's own language (Chinese).
_LANGUAGE: Final = "en_US"

#: What a festival name has to contain to be recognised, checked case-folded
#: before ``(observed)`` is stripped. Order matters only for readability.
_CHINESE_FESTIVALS: Final = (
    ("chinese new year's eve", "chinese_new_year"),
    ("chinese new year", "chinese_new_year"),
    ("dragon boat festival", "dragon_boat"),
    ("mid-autumn festival", "mid_autumn"),
    ("tomb-sweeping day", "tomb_sweeping"),
    ("national day", "national_day"),
    ("new year's day", "new_year"),
    ("labor day", "labor_day"),
)

#: Fixed (Gregorian) holidays. These are the ones that are not lunar and not
#: worth pulling a country's worth of ``holidays`` data for; they are the
#: occasions the request is really about.
_FIXED_HOLIDAYS: Final = (
    ((1, 1), "new_year", "New Year's Day"),
    ((2, 14), "valentines", "Valentine's Day"),
    ((3, 17), "st_patricks", "St Patrick's Day"),
    ((4, 1), "april_fools", "April Fools' Day"),
    ((5, 1), "labor_day", "Labor Day"),
    ((6, 1), "pride", "Pride Month"),
    ((7, 4), "independence", "Independence Day"),
    ((10, 31), "halloween", "Halloween"),
    ((11, 1), "all_saints", "All Saints' Day"),
    ((11, 11), "remembrance", "Remembrance Day"),
    ((12, 24), "christmas_eve", "Christmas Eve"),
    ((12, 25), "christmas", "Christmas"),
    ((12, 26), "boxing_day", "Boxing Day"),
    ((12, 31), "new_years_eve", "New Year's Eve"),
)


@dataclass(frozen=True, slots=True)
class Theme:
    """One day's resolved theme.

    Attributes:
        key: Machine name of the theme, e.g. ``christmas``.
        title: Human label.
        holiday: The holiday or festival this is based on, if any.
        season: The season name otherwise.
        kind: ``holiday``, ``season`` or ``everyday``.
    """

    key: str
    title: str
    holiday: str | None
    season: str
    kind: str


@dataclass(frozen=True, slots=True)
class Festival:
    """A festival resolved for one date.

    Attributes:
        key: Machine name, e.g. ``chinese_new_year``.
        title: Human label.
        name: The name the calendar library returned.
    """

    key: str
    title: str
    name: str


# ---------------------------------------------------------------------------
# Resolving the date
# ---------------------------------------------------------------------------

#: Seasons by month. A month belongs to exactly one season; the boundaries
#: are the solstices/equinoxes in the northern hemisphere, which is where
#: permanent outdoor lighting is installed in this installation.
_SEASONS: Final = (
    (12, "winter"),
    (1, "winter"),
    (2, "winter"),
    (3, "spring"),
    (4, "spring"),
    (5, "spring"),
    (6, "summer"),
    (7, "summer"),
    (8, "summer"),
    (9, "autumn"),
    (10, "autumn"),
    (11, "autumn"),
)

_SEASON_TITLES: Final = {
    "spring": "Spring",
    "summer": "Summer",
    "autumn": "Autumn",
    "winter": "Winter",
}

#: What to call a day with nothing special about it. A weekday and a weekend
#: are deliberately different: the point is that the display still varies.
_EVERYDAY: Final = {
    "spring": ("fresh_spring", "Fresh spring"),
    "summer": ("summer_day", "Summer day"),
    "autumn": ("autumn_day", "Autumn day"),
    "winter": ("winter_day", "Winter day"),
}


def _season(month: int) -> str:
    for season_month, name in _SEASONS:
        if month == season_month:
            return name
    return "spring"  # pragma: no cover - every month is listed


def _festival(day: dt.date) -> Festival | None:
    """Resolve ``day``'s festival from the calendar library.

    The lookup is by *name*, not by a date table: the library is the authority
    on when a lunar festival falls, including the observed days. The name is
    matched case-folded with an ``(observed)`` suffix stripped, because the
    library appends it to a festival that spills onto a substituted day and it
    would otherwise hide the festival behind a string that changes every year.

    A failure of the library — a missing dependency, a bad country — is *not*
    raised. The theme falls back to the season, so a broken calendar degrades
    the display to "winter" rather than taking the lights down.

    Returns:
        The festival, or ``None`` when the day is not one.
    """
    try:
        import holidays

        observed = holidays.country_holidays(
            COUNTRY, years={day.year}, language=_LANGUAGE
        )
    except Exception:  # pragma: no cover - dependency or country failure
        return None

    name = observed.get(day)
    if not name:
        return None
    key_name = str(name).split("(")[0].strip().lower()
    for needle, key in _CHINESE_FESTIVALS:
        if needle in key_name:
            return Festival(
                key=key, title=key.replace("_", " ").title(), name=str(name)
            )
    return None


def theme_for(day: dt.date) -> Theme:
    """Resolve the theme for one date.

    A fixed holiday wins, then a festival from the calendar library, then the
    season, then an everyday name that depends on the weekday so the display
    is not identical all season.
    """
    for (month, dom), key, title in _FIXED_HOLIDAYS:
        if (day.month, day.day) == (month, dom):
            return Theme(
                key=key,
                title=title,
                holiday=key,
                season=_season(day.month),
                kind="holiday",
            )
    festival = _festival(day)
    if festival is not None:
        return Theme(
            key=festival.key,
            title=festival.title,
            holiday=festival.key,
            season=_season(day.month),
            kind="holiday",
        )
    season = _season(day.month)
    if day.weekday() < 5:
        key, title = _EVERYDAY[season]
        kind = "everyday"
    else:
        key, title = f"weekend_{season}", f"Weekend {_SEASON_TITLES[season]}"
        kind = "weekend"
    return Theme(key=key, title=title, holiday=None, season=season, kind=kind)


def festival_name(day: dt.date) -> str | None:
    """The raw calendar-library name for ``day``, for logging.

    This is separate from :func:`_festival` because the display does not need
    it; it exists so a test or a log line can show what the library resolved
    without reaching into a private helper.
    """
    festival = _festival(day)
    return festival.name if festival is not None else None


# ---------------------------------------------------------------------------
# The display
# ---------------------------------------------------------------------------


#: One coherent style: a small palette and the motion that suits it. A theme
#: draws *one* of these, never a mix — that is what keeps the result a style
#: rather than a random stack.
@dataclass(frozen=True, slots=True)
class Style:
    """A palette plus the motion that consumes it.

    Attributes:
        palette: Two to four colours, in the order the pattern uses them.
        effect: Native effect name; ``None`` for a per-LED look.
        look: Per-LED look name; used when ``effect`` is ``None``.
        pattern: The per-LED look builder's palette length target.
        motion: ``calm``, ``flow`` or ``sparkle`` — which speed/density band
            the draw lands in, so a "calm" style never comes out frantic.
    """

    palette: tuple[Paint, ...]
    effect: str | None
    look: str | None
    motion: str


#: Speeds and densities per motion band. Kept narrow on purpose: a theme that
#: is drawn "calm" should not be able to come out as a strobe.
@dataclass(frozen=True, slots=True)
class _MotionBand:
    """One motion band's speed range, density range and per-LED effect."""

    speed: tuple[int, int]
    density: tuple[int, int]
    scribble: int


_MOTION_BANDS: Final = {
    "calm": _MotionBand(speed=(15, 35), density=(50, 75), scribble=0),
    "flow": _MotionBand(speed=(35, 60), density=(60, 85), scribble=1),
    "sparkle": _MotionBand(speed=(60, 85), density=(40, 70), scribble=5),
}


#: How much of the colour circle each season owns. The anchors are the hues a
#: person names when the season is mentioned — spring green, summer gold,
#: autumn rust, winter blue — and the width is wide enough that a season never
#: repeats itself, narrow enough that it never becomes another season. The arc
#: constrains the palette's *base* hue; the harmonic offsets then reach outside
#: it by design, so a spring palette may touch teal and a winter one violet
#: without either stopping being itself.
_SEASON_HUES: Final = {
    "spring": (55.0, 185.0),
    "summer": (28.0, 64.0),
    "autumn": (4.0, 42.0),
    "winter": (175.0, 268.0),
}

#: Saturation floor for a generated stop. Anything lower washes out towards
#: white, so the floor is high enough that the darkest stop in the widest value
#: range still reads as a colour with a name rather than a tint.
_MIN_SATURATION: Final = 0.78
#: Value range for a generated stop. The floor is low enough to allow a real
#: dark stop — the contrast against a bright one is what makes a palette read as
#: designed — while the ceiling stays below pure white.
_MIN_VALUE: Final = 0.30
_MAX_VALUE: Final = 1.0
#: How far a stop may stray from the palette's mean saturation and value. Wide
#: enough that the stops are visibly different in depth, not three near-copies.
_SATURATION_JITTER: Final = 0.10
_VALUE_JITTER: Final = 0.26

#: Hue offsets for the harmonic schemes. Each entry is a way of building a
#: three-stop palette that reads as one style rather than three unrelated
#: colours. The offsets are the *shape*; the base hue is what the season
#: supplies. Every season draws from every scheme, so the choice of shape is
#: itself part of the freshness. A wide scheme places its stops far apart on
#: purpose — that contrast is what makes a palette read as designed rather than
#: as one colour in three tints — and the season's identity comes from the base
#: hue, which is always drawn inside the season's arc. This is how "one
#: coherent style" and "a summer palette is not a winter palette" are both true.
#: Only the wide shapes are offered: an analogous step is the single-hue
#: "three tints" scheme that made the output read as plain, so it is dropped.
_HARMONIES: Final = {
    "split": (0.0, 150.0, -150.0),  # base plus both shoulders
    "triad": (0.0, 120.0, -120.0),  # three evenly spaced hues
}


def _hsv_to_rgb(hue: float, saturation: float, value: float) -> Color:
    """One saturated RGB stop from HSV, with hue wrapped into ``[0, 360)``."""
    red, green, blue = colorsys.hsv_to_rgb((hue % 360.0) / 360.0, saturation, value)
    return (round(red * 255), round(green * 255), round(blue * 255))


def _season_palette(season: str, rng: random.Random) -> tuple[Paint, ...]:
    """A vivid, harmonised palette that belongs to the season.

    The season supplies a hue arc; a base hue is drawn inside it and a harmonic
    scheme places the other two stops relative to it. Saturation is held high
    and value spans a wide band, so the palette has both bright and dark stops
    and every stop keeps real chroma. The scheme, the base hue and the per-stop
    saturation/value jitter are all drawn, so two runs in the same season differ
    while staying in season.
    """
    low, high = _SEASON_HUES[season]
    scheme = rng.choice(tuple(_HARMONIES))
    offsets = _HARMONIES[scheme]
    # The season constrains the *base* hue only. The harmonic offsets then place
    # the other stops where the scheme says, wrapping around the colour circle;
    # the base hue is what keeps the palette recognisably in season. Drawing the
    # base from the whole arc (rather than the narrow sub-arc a wide scheme's
    # reach would allow) is what makes two runs in one season actually differ
    # in hue instead of differing only in saturation and value jitter.
    base_hue = rng.uniform(low, high)
    # The palette's depth is drawn first, then jittered per stop. The jitter is
    # bounded by the floors, so a washed-out or clipped stop is impossible by
    # construction rather than by a post-hoc clamp.
    saturation = rng.uniform(_MIN_SATURATION, 1.0)
    value = rng.uniform(_MIN_VALUE, _MAX_VALUE)
    stops: list[Color] = []
    for offset in offsets:
        stop_saturation = min(
            1.0,
            max(
                _MIN_SATURATION,
                saturation + rng.uniform(-_SATURATION_JITTER, _SATURATION_JITTER),
            ),
        )
        stop_value = max(
            _MIN_VALUE,
            min(_MAX_VALUE, value + rng.uniform(-_VALUE_JITTER, _VALUE_JITTER)),
        )
        stops.append(_hsv_to_rgb(base_hue + offset, stop_saturation, stop_value))
    return (stops[0], stops[1], stops[2])


#: Holiday palettes. These are the occasions the request is about, so they are
#: explicit and recognisable rather than drawn. Every effect here is one whose
#: profile is :attr:`~effect_profile.PaletteMode.COLOURS`, so the whole palette
#: reaches the strip instead of being collapsed to the first colour.
_HOLIDAY_STYLES: Final = {
    # Every stop keeps its occasion's hue but is held at the same vivid floor as
    # the seasonal generator, so no holiday palette can fall back to white or a
    # tint. The identities are unchanged: Christmas is red and green, New Year
    # is gold, Valentine's is magenta, and so on.
    "christmas": (
        ((255, 0, 0), (0, 180, 60), (255, 210, 0)),
        "Flowing Water",
        None,
        "calm",
    ),
    "christmas_eve": (
        ((255, 40, 40), (0, 140, 60)),
        "Flowing Water",
        None,
        "calm",
    ),
    "new_years_eve": (
        ((255, 210, 0), (255, 90, 40)),
        "Fireworks",
        None,
        "sparkle",
    ),
    "new_year": (
        ((255, 200, 0), (255, 110, 30)),
        "Flowing Water",
        None,
        "sparkle",
    ),
    "valentines": (
        ((255, 40, 120), (255, 90, 170), (200, 20, 120)),
        "Flowing Water",
        None,
        "calm",
    ),
    "halloween": (
        ((255, 96, 0), (128, 0, 255)),
        "Chase",
        None,
        "flow",
    ),
    "st_patricks": (
        ((0, 180, 60), (90, 220, 140)),
        "Flowing Water",
        None,
        "calm",
    ),
    "independence": (
        ((255, 40, 40), (255, 200, 0), (40, 80, 255)),
        "Flowing Water",
        None,
        "flow",
    ),
    "pride": (
        ((255, 0, 0), (255, 128, 0), (255, 255, 0), (0, 200, 60)),
        None,
        "stripes",
        "flow",
    ),
    "remembrance": (
        ((200, 40, 80), (255, 90, 130)),
        "Flowing Water",
        None,
        "calm",
    ),
    "boxing_day": (
        ((255, 210, 0), (90, 140, 220)),
        "Flowing Water",
        None,
        "calm",
    ),
    "labor_day": (
        ((255, 200, 0), (255, 90, 60)),
        "Flowing Water",
        None,
        "flow",
    ),
    "all_saints": (
        ((128, 0, 255), (40, 0, 80)),
        "Flowing Water",
        None,
        "calm",
    ),
    "april_fools": (
        ((255, 105, 180), (0, 255, 255), (255, 255, 0)),
        "Cycle",
        None,
        "flow",
    ),
    "chinese_new_year": (
        ((255, 30, 30), (255, 200, 0)),
        "Fireworks",
        None,
        "sparkle",
    ),
    "dragon_boat": (
        ((0, 180, 60), (255, 220, 80)),
        "Flowing Water",
        None,
        "flow",
    ),
    "mid_autumn": (
        ((255, 200, 40), (255, 170, 40)),
        "Flowing Water",
        None,
        "calm",
    ),
    "tomb_sweeping": (
        ((90, 190, 80), (150, 220, 90)),
        "Flowing Water",
        None,
        "calm",
    ),
    "national_day": (
        ((255, 30, 30), (255, 200, 0)),
        "Flowing Water",
        None,
        "flow",
    ),
}

#: Per-LED looks a seasonal theme may draw. Every entry is a look whose builder
#: renders *every* colour it is given: ``gradient`` interpolates across all
#: stops, ``stripes`` cycles the palette in blocks of three, and ``wreath``
#: lights every LED in alternating palette pairs. A single-colour look
#: (``firefly``, ``chase``) would drop the accent, and ``twinkle`` blinks fast,
#: so none of them is offered.
_SEASON_LOOKS: Final = {
    "spring": ("gradient", "stripes", "wreath"),
    "summer": ("gradient", "stripes", "wreath"),
    "autumn": ("gradient", "stripes", "wreath"),
    "winter": ("gradient", "stripes", "wreath"),
}


def _draw_style(theme: Theme, rng: random.Random) -> Style:
    """Draw one coherent style for a theme.

    A holiday uses its own palette and motion. A season draws a palette from
    the season's stops, picks a motion band, and then picks a pattern that
    suits that band — a calm theme gets a static or breathing pattern, a
    sparkling one gets a twinkle or firefly. The palette and the motion are
    chosen together, which is what stops the result reading as stacked
    randomness.
    """
    if theme.holiday and theme.holiday in _HOLIDAY_STYLES:
        holiday = _HOLIDAY_STYLES[theme.holiday]
        return Style(
            palette=holiday[0], effect=holiday[1], look=holiday[2], motion=holiday[3]
        )

    season = theme.season
    palette = _season_palette(season, rng)
    motion = rng.choice(("calm", "flow", "sparkle"))
    # A seasonal theme uses a per-LED look, because that is where the palette
    # is most visible; the effect is left to the holiday styles. Every look in
    # the pool renders the whole three-colour palette and blinks only slowly, so
    # the motion band is carried by the speed/density draw rather than by the
    # pattern — a "calm" band can never come out as a strobe.
    look = rng.choice(_SEASON_LOOKS[season])
    return Style(palette=palette, effect=None, look=look, motion=motion)


def _speed_density(motion: str, rng: random.Random) -> tuple[int, int, int]:
    """Speed, density and per-LED effect for one motion band."""
    band = _MOTION_BANDS[motion]
    speed = rng.randint(band.speed[0], band.speed[1])
    density = rng.randint(band.density[0], band.density[1])
    return speed, density, band.scribble


def _scene_name(theme: Theme, style: Style) -> str:
    """A stable, human-readable name for a generated display.

    This is *not* a stored scene name. It is a label only; the scene is applied
    and forgotten, and ``runtime.scene`` reads it exactly as a stored scene's
    name would.
    """
    label = theme.key
    if style.motion:
        label = f"{label}_{style.motion}"
    return label


def generate_scene(day: dt.date, rng: random.Random | None = None) -> Scene:
    """Generate one display for ``day``.

    Args:
        day: The date the display is for.
        rng: Source of randomness. ``None`` — the normal case — uses the
            module's own :class:`random.Random`, which is *not* seeded from the
            date, so every call is a fresh draw. A caller that wants a
            reproducible draw (a test, a preview) passes its own generator.

    Returns:
        A :class:`Scene` that applies one coherent style. The scene is never
        written to the store.
    """
    rng = rng or random.Random()
    theme = theme_for(day)
    style = _draw_style(theme, rng)
    speed, density, scribble_effect = _speed_density(style.motion, rng)
    direction = rng.choice(("l2r", "r2l"))
    name = _scene_name(theme, style)

    if style.effect is not None:
        effect = style.effect
        if effect.lower() not in EFFECT_NAME_TO_ID:
            raise ValueError(f"theme {theme.key!r} drew unknown effect {effect!r}")
        return Scene(
            name=name,
            title=theme.title,
            mode="effect",
            colors=style.palette,
            effect=effect,
            speed=speed,
            density=density,
            direction=direction,
        )

    look = style.look or "solid"
    if look not in LOOKS:
        raise ValueError(f"theme {theme.key!r} drew unknown look {look!r}")
    return Scene(
        name=name,
        title=theme.title,
        mode="look",
        colors=style.palette,
        look=look,
        scribble_effect=scribble_effect,
        speed=speed,
        density=density,
        direction=direction,
    )


__all__ = [
    "COUNTRY",
    "Festival",
    "Theme",
    "festival_name",
    "generate_scene",
    "theme_for",
]
