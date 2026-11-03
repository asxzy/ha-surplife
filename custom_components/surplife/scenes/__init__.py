"""User-defined scenes and date-driven selection.

A *scene* is a named recipe — colour, effect, segments, per-LED look or scribble
pattern — that can be applied to any number of controllers, bound to a date
window, and chosen automatically for "today". See :mod:`.model` for the data
model and :mod:`.store` for persistence.
"""

from __future__ import annotations

from .model import (
    MAX_SCENES,
    SCENE_MODES,
    Scene,
    known_effects,
    known_scribble_effects,
    normalize_name,
    parse_tags,
    select_for_date,
)
from .store import STORAGE_KEY, STORAGE_VERSION, SceneStore

__all__ = [
    "MAX_SCENES",
    "SCENE_MODES",
    "STORAGE_KEY",
    "STORAGE_VERSION",
    "Scene",
    "SceneStore",
    "known_effects",
    "known_scribble_effects",
    "normalize_name",
    "parse_tags",
    "select_for_date",
]
