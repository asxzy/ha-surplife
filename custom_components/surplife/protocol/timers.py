"""Timer slot parsing.

The ``E0 06`` reply carries all six slots back to back. Each slot begins with
a slot number and a flag byte::

    slot  flags  payload
     01    0F    14 00 00 FE 0E E0 01 00 23 00 00 00 00 00 00 00 00 00 00
     02    F0    00 1E 00 FE 0E E0 01 00 24 00 00 00 00 00 00 00 00 00 00
     03    00    (empty)
     ...

A zero flag means the slot is unset. Otherwise the payload is a 20-byte record
whose meaning depends on the action byte at offset 9:

* ``0x23`` / ``0x24`` — switch on / off, no extra data
* ``0xA1`` — colour, with ``(H/2, S, V)`` at offsets 10-12
* ``0x29`` / ``0x6B`` — scene timer, with a variable-length colour list

**Verified** against the two factory timers on the test controller: slot 1
switches on at 20:00, slot 2 switches off at 00:30, both daily.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final

from .constants import (
    REPEAT_DAY_LABELS,
    TIMER_ACTION_NAMES,
    TIMER_SLOTS,
)

EMPTY_FLAG: Final = 0x00
RECORD_LEN: Final = 20


@dataclass(frozen=True, slots=True)
class Timer:
    """One hardware timer slot.

    Attributes:
        slot: Slot number, 1-6.
        active: Whether the slot is configured.
        hour: Start hour 0-23.
        minute: Start minute 0-59.
        repeat_mask: Day mask; ``0`` means run once.
        action: Raw action byte. See :data:`~.constants.TIMER_ACTION_NAMES`.
        color: ``(H/2, S, V)`` for colour timers, else ``None``.
        raw: The raw 20-byte record, for diagnostics.
    """

    slot: int
    active: bool = False
    hour: int = 0
    minute: int = 0
    repeat_mask: int = 0
    action: int = 0x24
    color: tuple[int, int, int] | None = None
    raw: bytes = b""

    @property
    def action_name(self) -> str:
        """Human-readable action name."""
        return TIMER_ACTION_NAMES.get(self.action, f"0x{self.action:02x}")

    @property
    def repeat_days(self) -> list[str]:
        """Enabled day labels, e.g. ``["Mon", "Tue"]``."""
        return [label for bit, label in REPEAT_DAY_LABELS if self.repeat_mask & bit]

    @property
    def repeat_label(self) -> str:
        """Compact repeat description: ``Once``, ``Everyday``, or day list."""
        if self.repeat_mask == 0:
            return "Once"
        if self.repeat_mask == 0xFE:
            return "Everyday"
        if self.repeat_mask == 0x3E:
            return "Weekdays"
        if self.repeat_mask == 0xC0:
            return "Weekend"
        return ", ".join(self.repeat_days) or "Never"

    @property
    def time_label(self) -> str:
        """``HH:MM`` start time."""
        return f"{self.hour:02d}:{self.minute:02d}"


def parse_timers(payload: bytes) -> list[Timer]:
    """Parse the inner payload of an ``E0 06`` reply into six slots.

    Args:
        payload: The unwrapped reply, beginning ``E0 06``.

    Returns:
        One :class:`Timer` per slot, always length :data:`TIMER_SLOTS`.

    Raises:
        ValueError: if the payload is not a timer reply.
    """
    if len(payload) < 2 or payload[0] != 0xE0 or payload[1] != 0x06:
        raise ValueError("not a timer reply (expected E0 06)")

    body = payload[2:]
    timers: list[Timer] = []
    offset = 0

    while offset < len(body) and len(timers) < TIMER_SLOTS:
        slot = body[offset]
        if slot < 1 or slot > TIMER_SLOTS:
            # Not a slot header; stop rather than misparse.
            break
        if offset + 1 >= len(body):
            break
        flags = body[offset + 1]

        if flags == EMPTY_FLAG:
            # An unset slot occupies 7 bytes: the slot byte plus six zeros.
            timers.append(Timer(slot=slot, active=False))
            offset += 7
            continue

        record = body[offset + 1 : offset + 1 + RECORD_LEN]
        if len(record) < 10:
            timers.append(Timer(slot=slot, active=False))
            offset += 7
            continue

        # Record layout differs from the write layout: the flag byte comes
        # first, then hour/minute/seconds, then the repeat mask, then the
        # 0x0E marker and the action byte.
        timer = Timer(
            slot=slot,
            active=True,
            hour=record[1],
            minute=record[2],
            repeat_mask=record[4],
            action=record[9] if len(record) > 9 else 0x24,
            raw=bytes(record),
        )
        if timer.action == 0xA1 and len(record) >= 13:
            timer = replace(timer, color=(record[10], record[11], record[12]))
        timers.append(timer)
        offset += 1 + RECORD_LEN

    # Pad any missing slots so callers can index by slot number.
    seen = {t.slot for t in timers}
    for slot in range(1, TIMER_SLOTS + 1):
        if slot not in seen:
            timers.append(Timer(slot=slot, active=False))
    return sorted(timers, key=lambda t: t.slot)


def parse_time(payload: bytes) -> tuple[int, int, int, int, int, int] | None:
    """Parse a clock reply into ``(year, month, day, hour, minute, second)``.

    Args:
        payload: The unwrapped reply. **Verified** format:
            ``00 11 14 YY MM DD HH MM SS DOW 00``.

    Returns:
        The six-tuple, or ``None`` if the payload is not a clock reply.
    """
    if len(payload) < 10:
        return None
    # The reply begins with a zero byte, so locate the 11 14 header rather than
    # assuming a fixed offset.
    index = payload.find(b"\x11\x14")
    if index < 1 or index + 8 > len(payload):
        return None
    year, month, day, hour, minute, second = payload[index + 2 : index + 8]
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour <= 23 and minute <= 59):
        return None
    return (year + 2000, month, day, hour, minute, second)
