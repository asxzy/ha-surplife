"""User-defined scenes: named, reusable, optionally date-bound recipes.

A scene is deliberately the same shape as a bundled preset — a small recipe
applied through the coordinator's normal command methods — so a scene can never
do anything the services cannot. The difference is that presets are shipped
with the integration and scenes are created by the user at runtime and stored in
Home Assistant.

**Why this is not the app's `symp_scene`.** The app's "custom scene" is a
Symphony cloud/MQTT DP command (``42{model}{speed}{bright}``) whose device-side
payload is a framed, compressed, chunked blob that has not been characterised.
Nothing in the local ``0xB6`` protocol carries it. What *is* available locally,
and verified on hardware, is the full command surface — colour, effects,
segments and per-LED paints — so a scene here is a composition of those. This
is strictly more expressive than the app's fixed scene slots and needs no cloud.

Date binding exists so an automation can apply "whatever scene belongs to
today" with a single call, including a stable pseudo-random choice: the pick is
seeded from the date, so it does not shuffle on every Home Assistant restart.
"""

from __future__ import annotations

import datetime as dt
import random
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from ..protocol.constants import (
    DIRECTIONS,
    EFFECT_NAME_TO_ID,
    SCRIBBLE_EFFECT_NAME_TO_ID,
)
from ..protocol.spec import parse_color, parse_led_spec
from ..scribble_looks import LOOKS

if TYPE_CHECKING:
    from ..coordinator import SurplifeCoordinator

Color = tuple[int, int, int]

#: How a scene drives the strip. Each mode reads exactly one payload field,
#: which keeps validation and error messages unambiguous.
SCENE_MODES: Final = ("rgb", "white", "effect", "segments", "scribble", "look")

#: Most scenes a single Home Assistant instance may store.
MAX_SCENES: Final = 200

#: Scene names double as service values and select options, so they are
#: restricted to a conservative slug.
_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_]{0,47}$")
_MMDD_RE: Final = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

WHITE: Final[Color] = (255, 255, 255)


def normalize_name(text: str) -> str:
    """Reduce arbitrary text to a valid scene name.

    Lowercases, turns whitespace and dashes into underscores and drops
    everything outside ``[a-z0-9_]``.

    Raises:
        ValueError: if nothing usable is left.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    slug = re.sub(r"_{2,}", "_", slug)[:48].strip("_")
    if not slug:
        raise ValueError(f"{text!r} does not contain a usable scene name")
    return slug


def _as_color(value: Any) -> Color:
    """Validate one colour into a 3-tuple of 0-255 integers.

    Accepts the same tokens as everywhere else — ``#rrggbb``, ``r,g,b`` or a
    three-item sequence — because scene definitions are written by hand in
    YAML and in the service UI, where hex is the natural form. A warm-white
    token (``w80``) is rejected: colours here are RGB, and warm white is its
    own scene mode.
    """
    if isinstance(value, (str, bytes)):
        rgb, white = parse_color(value)
        if white is not None:
            raise ValueError(
                f"invalid colour {value!r}: use the 'white' field for warm white"
            )
        if rgb is None:
            raise ValueError(f"invalid colour {value!r}: 'off' is not a colour")
        return rgb
    try:
        parts = tuple(int(part) for part in value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"invalid colour {value!r}") from err
    if len(parts) != 3:
        raise ValueError(f"invalid colour {value!r}: expected three values")
    if any(not 0 <= part <= 255 for part in parts):
        raise ValueError(f"invalid colour {value!r}: values must be 0-255")
    return parts  # type: ignore[return-value]


def _as_segment(value: Any) -> Color | None:
    """Validate one segment colour, treating "off"/"black" as dark.

    A dark segment is meaningful — it is a gap in the run — so it becomes
    ``None`` rather than being rejected.
    """
    if value is None:
        return None
    if isinstance(value, (str, bytes)) and str(value).strip().lower() in (
        "off",
        "black",
        "none",
    ):
        return None
    return _as_color(value)


def _as_int(value: Any, low: int, high: int, field: str) -> int:
    """Validate an integer field, reporting the field name on failure."""
    try:
        number = int(value)
    except (TypeError, ValueError) as err:
        raise ValueError(f"{field} must be a whole number") from err
    if not low <= number <= high:
        raise ValueError(f"{field} must be {low}..{high}, got {number}")
    return number


def _as_mmdd(value: Any, field: str) -> str | None:
    """Validate an ``MM-DD`` date fragment, or return ``None`` for empty."""
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not _MMDD_RE.match(text):
        raise ValueError(f"{field} must look like MM-DD, got {text!r}")
    return text


def _mmdd(value: str) -> tuple[int, int]:
    """Split a validated ``MM-DD`` string into a comparable pair."""
    month, day = value.split("-")
    return int(month), int(day)


def parse_tags(value: Any) -> tuple[str, ...]:
    """Normalise tags from a list or a comma-separated string."""
    if value is None:
        return ()
    if isinstance(value, str):
        items: Iterable[Any] = value.split(",")
    else:
        items = value
    result: list[str] = []
    for item in items:
        tag = str(item).strip().lower()
        if tag and tag not in result:
            result.append(tag)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class Scene:
    """A named lighting recipe, applied to one controller at a time.

    Attributes:
        name: Machine name; a slug, used as the service and select value.
        title: Human-readable label shown in the UI.
        mode: One of :data:`SCENE_MODES`; selects the payload used.
        colors: Palette for ``rgb`` (first colour) and ``effect``.
        brightness: Home Assistant brightness 0-255.
        white: Warm-white level 0-255 for ``white`` mode.
        effect: Native effect name for ``effect`` mode.
        speed: Effect speed 0-100.
        density: Effect density 0-100.
        direction: ``l2r`` or ``r2l``.
        segments: 20-entry palette for ``segments`` mode; ``None`` is dark.
        pattern: Per-LED spec for ``scribble`` mode.
        look: Built-in per-LED look name for ``look`` mode.
        scribble_effect: Per-LED effect id 0-8 for the two per-LED modes.
        start: First day of the scene's season as ``MM-DD``; ``None`` means
            always eligible.
        end: Last day of the season as ``MM-DD``, inclusive.
        tags: Free-form labels used to filter daily selection.
        priority: Tie-breaker for deterministic (non-random) selection; the
            highest priority wins.
    """

    name: str
    title: str = ""
    mode: str = "rgb"
    colors: tuple[Color, ...] = (WHITE,)
    brightness: int = 255
    white: int = 0
    effect: str | None = None
    speed: int = 50
    density: int = 50
    direction: str = "l2r"
    segments: tuple[Color | None, ...] = ()
    pattern: str | None = None
    look: str | None = None
    scribble_effect: int = 0
    start: str | None = None
    end: str | None = None
    tags: tuple[str, ...] = ()
    priority: int = 0

    def __post_init__(self) -> None:
        """Normalise containers and validate every field.

        Raises:
            ValueError: if any field is out of range or the payload for the
                selected mode is missing.
        """
        set_field = object.__setattr__

        set_field(self, "name", normalize_name(self.name))
        if not _NAME_RE.match(self.name):
            raise ValueError(f"invalid scene name {self.name!r}")
        if not self.title:
            set_field(self, "title", self.name.replace("_", " ").title())
        if self.mode not in SCENE_MODES:
            raise ValueError(
                f"mode must be one of {', '.join(SCENE_MODES)}, got {self.mode!r}"
            )

        set_field(self, "colors", tuple(_as_color(color) for color in self.colors))
        set_field(
            self,
            "segments",
            tuple(_as_segment(part) for part in self.segments),
        )
        set_field(self, "tags", parse_tags(self.tags))
        set_field(self, "brightness", _as_int(self.brightness, 0, 255, "brightness"))
        set_field(self, "white", _as_int(self.white, 0, 255, "white"))
        set_field(self, "speed", _as_int(self.speed, 0, 100, "speed"))
        set_field(self, "density", _as_int(self.density, 0, 100, "density"))
        set_field(
            self, "scribble_effect", _as_int(self.scribble_effect, 0, 8, "scribble_effect")
        )
        set_field(self, "priority", _as_int(self.priority, -100, 100, "priority"))
        set_field(self, "start", _as_mmdd(self.start, "start"))
        set_field(self, "end", _as_mmdd(self.end, "end"))

        if self.direction not in DIRECTIONS:
            raise ValueError(f"direction must be l2r or r2l, got {self.direction!r}")

        if self.mode == "rgb" and not self.colors:
            raise ValueError("rgb scenes need at least one colour")
        if self.mode == "effect":
            if not self.effect:
                raise ValueError("effect scenes need an effect name")
            if self.effect.lower() not in EFFECT_NAME_TO_ID:
                raise ValueError(f"unknown effect {self.effect!r}")
            set_field(self, "effect", self.effect)
        if self.mode == "segments":
            if not self.segments:
                raise ValueError("segments scenes need a segment palette")
            if len(self.segments) > 20:
                raise ValueError(
                    f"segments scenes take at most 20 colours, got {len(self.segments)}"
                )
        if self.mode == "scribble" and not self.pattern:
            raise ValueError("scribble scenes need a per-LED pattern")
        if self.mode == "look":
            if not self.look:
                raise ValueError("look scenes need a look name")
            if self.look not in LOOKS:
                raise ValueError(
                    f"unknown look {self.look!r}; available: {', '.join(sorted(LOOKS))}"
                )

    # -- description -------------------------------------------------------

    @property
    def season(self) -> str:
        """A compact human description of the date window."""
        if self.start is None and self.end is None:
            return "all year"
        return f"{self.start or '01-01'} to {self.end or '12-31'}"

    def describe(self) -> str:
        """One-line summary, used in entity attributes."""
        detail = {
            "rgb": lambda: ", ".join(f"#{red:02x}{green:02x}{blue:02x}" for red, green, blue in self.colors),
            "white": lambda: f"warm white {self.white}",
            "effect": lambda: f"{self.effect} {self.speed}%/{self.density}%",
            "segments": lambda: f"{len(self.segments)} segments",
            "scribble": lambda: str(self.pattern),
            "look": lambda: str(self.look),
        }
        return f"{self.mode}: {detail[self.mode]()}"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict, omitting empty fields."""
        data: dict[str, Any] = {
            "name": self.name,
            "title": self.title,
            "mode": self.mode,
            "brightness": self.brightness,
            "speed": self.speed,
            "density": self.density,
            "direction": self.direction,
            "scribble_effect": self.scribble_effect,
            "priority": self.priority,
        }
        if self.colors:
            data["colors"] = [list(color) for color in self.colors]
        if self.white:
            data["white"] = self.white
        if self.effect:
            data["effect"] = self.effect
        if self.segments:
            data["segments"] = [
                None if part is None else list(part) for part in self.segments
            ]
        if self.pattern:
            data["pattern"] = self.pattern
        if self.look:
            data["look"] = self.look
        if self.start:
            data["start"] = self.start
        if self.end:
            data["end"] = self.end
        if self.tags:
            data["tags"] = list(self.tags)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Scene:
        """Rebuild a scene from :meth:`to_dict` output.

        Unknown keys are ignored so an older store stays readable after a
        field is renamed.
        """
        known = {
            "name",
            "title",
            "mode",
            "colors",
            "brightness",
            "white",
            "effect",
            "speed",
            "density",
            "direction",
            "segments",
            "pattern",
            "look",
            "scribble_effect",
            "start",
            "end",
            "tags",
            "priority",
        }
        payload = {key: value for key, value in data.items() if key in known}
        return cls(**payload)

    # -- scheduling --------------------------------------------------------

    def in_window(self, day: dt.date) -> bool:
        """Whether ``day`` falls inside this scene's season.

        Windows wrap year-end when ``start`` is later than ``end``, so
        ``12-01`` to ``01-06`` works as expected.
        """
        if self.start is None and self.end is None:
            return True
        start = _mmdd(self.start or "01-01")
        end = _mmdd(self.end or "12-31")
        today = (day.month, day.day)
        if start <= end:
            return start <= today <= end
        return today >= start or today <= end

    def matches(self, day: dt.date, topic: str | None = None) -> bool:
        """Whether the scene is eligible on ``day``, optionally for a tag."""
        if not self.in_window(day):
            return False
        return topic is None or topic in self.tags

    # -- application -------------------------------------------------------

    async def async_apply(self, coordinator: SurplifeCoordinator) -> None:
        """Apply this scene to one controller."""
        if self.mode == "segments":
            await coordinator.async_set_segments(list(self.segments))
        elif self.mode == "effect":
            await coordinator.async_set_effect(
                self.effect or "",
                speed=self.speed,
                density=self.density,
                direction=DIRECTIONS[self.direction],
                colors=list(self.colors),
            )
        elif self.mode == "white":
            await coordinator.async_set_white(self.white)
        elif self.mode == "scribble":
            leds = parse_led_spec(
                self.pattern or "", coordinator.runtime.led_count
            )
            await coordinator.async_set_scribble(
                leds,
                effect=self.scribble_effect,
                direction=DIRECTIONS[self.direction],
                density=self.density,
                speed=self.speed,
            )
        elif self.mode == "look":
            leds = LOOKS[self.look or ""].build(coordinator.runtime.led_count)
            await coordinator.async_set_scribble(
                leds,
                effect=self.scribble_effect,
                direction=DIRECTIONS[self.direction],
                density=self.density,
                speed=self.speed,
            )
        else:
            await coordinator.async_set_rgb(self.colors[0], self.brightness)

        if not coordinator.runtime.is_on:
            await coordinator.async_set_power(True)
        await coordinator.async_mark_scene(self.name)


# ---------------------------------------------------------------------------
# Date-driven selection
# ---------------------------------------------------------------------------


def select_for_date(
    scenes: Iterable[Scene],
    day: dt.date,
    *,
    topic: str | None = None,
    randomize: bool = True,
) -> Scene | None:
    """Pick the scene that belongs to ``day``.

    With ``randomize`` the choice is seeded from the date, the topic and the
    candidate names, so the same date always yields the same scene even across
    restarts — an automation that runs twice in one day will not flip the
    lights to something else. Without it the highest-priority scene wins, with
    the name as a stable tie-breaker.

    Args:
        scenes: Every stored scene.
        day: The date to choose for, usually today in Home Assistant's zone.
        topic: Optional tag the scene must carry.
        randomize: Whether to choose pseudo-randomly instead of by priority.

    Returns:
        The chosen scene, or ``None`` when nothing is eligible.
    """
    candidates = sorted(
        (scene for scene in scenes if scene.matches(day, topic)),
        key=lambda scene: (-scene.priority, scene.name),
    )
    if not candidates:
        return None
    if not randomize:
        return candidates[0]
    seed = "|".join(
        [day.isoformat(), topic or "", ",".join(scene.name for scene in candidates)]
    )
    return random.Random(seed).choice(candidates)


def known_effects() -> tuple[str, ...]:
    """Effect names accepted by ``effect`` scenes, for schema generation."""
    return tuple(sorted(EFFECT_NAME_TO_ID))


def known_scribble_effects() -> tuple[str, ...]:
    """Per-LED effect names accepted in scene payloads."""
    return tuple(sorted(SCRIBBLE_EFFECT_NAME_TO_ID))


ColorList = Sequence[Color]
