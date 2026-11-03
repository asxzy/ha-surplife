"""Persistent storage for user-defined scenes.

Scenes live in Home Assistant's ``.storage`` directory via
:class:`homeassistant.helpers.storage.Store`, one file for the whole
integration rather than one per config entry. That matters: the scenes are
shared vocabulary — the same "Christmas" scene is meant to be applied to all
three controllers simultaneously — and duplicating them per entry would let
them drift apart.

The store is the single owner of scene state. Nothing else writes it, and every
mutation goes through :meth:`SceneStore.async_put` / :meth:`async_delete` so the
in-memory mapping and the file cannot disagree.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .model import MAX_SCENES, Scene

_LOGGER = logging.getLogger(__name__)

#: Bump when the on-disk shape changes incompatibly. The loader tolerates
#: unknown keys, so additive changes do not need a bump.
STORAGE_VERSION: int = 1

#: File name under ``.storage/``. Must start with the integration domain.
STORAGE_KEY: str = "surplife.scenes"


@dataclass
class SceneStore:
    """The set of stored scenes, with lazy loading and write-through.

    Attributes:
        scenes: Scenes by machine name.
    """

    hass: HomeAssistant
    scenes: dict[str, Scene] = field(default_factory=dict)
    _store: Store[dict[str, Any]] = field(init=False, repr=False)
    _loaded: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        """Create the underlying storage handle."""
        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            STORAGE_KEY,
            atomic_writes=True,
        )

    # -- loading -----------------------------------------------------------

    async def async_load(self) -> None:
        """Read the stored scenes, discarding any that no longer validate.

        A bad record is logged and skipped rather than raised: one corrupt
        scene should not take the whole integration down, and the rest are
        still usable.
        """
        if self._loaded:
            return
        stored = await self._store.async_load()
        self.scenes = {}
        for raw in (stored or {}).get("scenes", []):
            try:
                scene = Scene.from_dict(raw)
            except (ValueError, TypeError, KeyError) as err:
                _LOGGER.warning("ignoring invalid stored scene %r: %s", raw, err)
                continue
            self.scenes[scene.name] = scene
        self._loaded = True

    async def async_save(self) -> None:
        """Write every scene to disk."""
        await self._store.async_save(
            {
                "scenes": [
                    scene.to_dict()
                    for scene in sorted(self.scenes.values(), key=lambda s: s.name)
                ]
            }
        )

    # -- queries -----------------------------------------------------------

    def get(self, name: str) -> Scene | None:
        """Look a scene up by machine name, case-insensitively."""
        return self.scenes.get(str(name).strip().lower())

    def require(self, name: str) -> Scene:
        """Look a scene up, raising a helpful error when it is missing.

        Raises:
            ValueError: if there is no such scene.
        """
        scene = self.get(name)
        if scene is None:
            available = ", ".join(sorted(self.scenes)) or "none"
            raise ValueError(f"unknown scene {name!r}; available: {available}")
        return scene

    def all(self) -> list[Scene]:
        """Every scene, sorted by name."""
        return sorted(self.scenes.values(), key=lambda scene: scene.name)

    def names(self) -> list[str]:
        """Machine names, sorted — the option list for the select entity."""
        return sorted(self.scenes)

    def __len__(self) -> int:
        """Number of stored scenes."""
        return len(self.scenes)

    # -- mutations ---------------------------------------------------------

    async def async_put(self, scene: Scene) -> Scene:
        """Create or replace a scene and persist the change.

        Raises:
            ValueError: if the store is full and this would be a new scene.
        """
        if scene.name not in self.scenes and len(self.scenes) >= MAX_SCENES:
            raise ValueError(
                f"cannot store more than {MAX_SCENES} scenes; delete one first"
            )
        self.scenes[scene.name] = scene
        await self.async_save()
        return scene

    async def async_put_many(self, scenes: Iterable[Scene]) -> list[Scene]:
        """Create or replace several scenes with a single disk write."""
        added: list[Scene] = []
        for scene in scenes:
            if scene.name not in self.scenes and len(self.scenes) >= MAX_SCENES:
                raise ValueError(
                    f"cannot store more than {MAX_SCENES} scenes; delete one first"
                )
            self.scenes[scene.name] = scene
            added.append(scene)
        await self.async_save()
        return added

    async def async_delete(self, name: str) -> Scene:
        """Remove a scene and persist the change.

        Raises:
            ValueError: if there is no such scene.
        """
        scene = self.require(name)
        del self.scenes[scene.name]
        await self.async_save()
        return scene

    async def async_reload(self, payload: Iterable[Mapping[str, Any]]) -> int:
        """Replace the whole collection from a list of dicts.

        Used by the ``import_scenes`` service so a user can keep their scenes in
        a YAML file under version control and push them in one go.

        Returns:
            How many scenes were written.
        """
        scenes = [Scene.from_dict(item) for item in payload]
        if len(scenes) > MAX_SCENES:
            raise ValueError(f"cannot store more than {MAX_SCENES} scenes")
        self.scenes = {scene.name: scene for scene in scenes}
        await self.async_save()
        return len(scenes)

    # -- lifecycle ---------------------------------------------------------

    async def async_remove(self) -> None:
        """Delete the backing file entirely."""
        await self._store.async_remove()
        self.scenes = {}
