"""Surplife / Zengge 0xB6 (AK001-ZJ21413) local-network protocol.

A small, dependency-free implementation of the wire protocol used by Surplife
outdoor permanent lighting controllers. Everything here is derived from live
captures and verified against hardware; see ``docs/protocol_0xB6.md``.

The public entry point is :class:`~.client.SurplifeClient`.
"""

from __future__ import annotations

from .client import SurplifeClient, SurplifeConnectionError, SurplifeError
from .constants import (
    BLINK_FAST,
    BLINK_NONE,
    BLINK_SLOW,
    EFFECT_NAMES,
    SCRIBBLE_EFFECT_NAMES,
    TimerAction,
)
from .discovery import DiscoveredDevice, async_discover, discover
from .scribble import ScribbleLED
from .state import SurplifeState
from .timers import Timer

__all__ = [
    "BLINK_FAST",
    "BLINK_NONE",
    "BLINK_SLOW",
    "EFFECT_NAMES",
    "SCRIBBLE_EFFECT_NAMES",
    "DiscoveredDevice",
    "ScribbleLED",
    "SurplifeClient",
    "SurplifeConnectionError",
    "SurplifeError",
    "SurplifeState",
    "Timer",
    "TimerAction",
    "async_discover",
    "discover",
]
