"""Tests for timer and clock parsing against a captured reply."""

from __future__ import annotations

import datetime as dt

import pytest

from custom_components.surplife.protocol import timers

from . import golden


def test_parse_timers_returns_six_slots() -> None:
    parsed = timers.parse_timers(golden.GET_TIMERS_REPLY_INNER)
    assert len(parsed) == 6
    assert [t.slot for t in parsed] == [1, 2, 3, 4, 5, 6]


def test_parse_timers_factory_slot_one() -> None:
    """The controller ships with a 20:00 ON timer every day."""
    timer = timers.parse_timers(golden.GET_TIMERS_REPLY_INNER)[0]
    assert timer.active is True
    assert timer.hour == 20
    assert timer.minute == 0
    assert timer.action == 0x23
    assert timer.action_name == "on"
    assert timer.repeat_mask == 0xFE
    assert timer.repeat_label == "Everyday"
    assert timer.time_label == "20:00"


def test_parse_timers_factory_slot_two() -> None:
    """The second factory timer switches off at 00:30."""
    timer = timers.parse_timers(golden.GET_TIMERS_REPLY_INNER)[1]
    assert timer.active is True
    assert timer.hour == 0
    assert timer.minute == 30
    assert timer.action == 0x24
    assert timer.action_name == "off"
    assert timer.time_label == "00:30"


def test_parse_timers_empty_slots() -> None:
    parsed = timers.parse_timers(golden.GET_TIMERS_REPLY_INNER)
    for timer in parsed[2:]:
        assert timer.active is False


def test_parse_timers_rejects_wrong_opcode() -> None:
    with pytest.raises(ValueError, match="E0 06"):
        timers.parse_timers(b"\xe0\x05\x01")


def test_repeat_day_labels() -> None:
    timer = timers.Timer(slot=1, active=True, repeat_mask=0x3E)
    assert timer.repeat_days == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert timer.repeat_label == "Weekdays"


def test_repeat_weekend_label() -> None:
    assert timers.Timer(slot=1, repeat_mask=0xC0).repeat_label == "Weekend"


def test_repeat_once_label() -> None:
    assert timers.Timer(slot=1, repeat_mask=0x00).repeat_label == "Once"


def test_repeat_custom_day_list() -> None:
    timer = timers.Timer(slot=1, repeat_mask=0x02 | 0x40)  # Mon and Sat
    assert timer.repeat_days == ["Mon", "Sat"]
    assert timer.repeat_label == "Mon, Sat"


def test_unknown_action_falls_back_to_hex() -> None:
    assert timers.Timer(slot=1, action=0x99).action_name == "0x99"


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


def test_parse_time() -> None:
    parsed = timers.parse_time(golden.GET_TIME_REPLY_INNER)
    assert parsed == (2026, 11, 2, 15, 47, 38)


def test_parse_time_accepts_full_reply() -> None:
    """The parser locates the fields inside a whole read buffer."""
    parsed = timers.parse_time(b"\x00" + golden.GET_TIME_REPLY_INNER)
    assert parsed == (2026, 11, 2, 15, 47, 38)


def test_parse_time_rejects_wrong_opcode() -> None:
    assert timers.parse_time(b"\x00\x12\x14\x1a\x0b\x02\x0f\x2f\x26\x01") is None


def test_parse_time_rejects_short_payload() -> None:
    assert timers.parse_time(b"\x00\x11\x14\x1a") is None


def test_parse_time_result_is_usable_as_datetime() -> None:
    parsed = timers.parse_time(golden.GET_TIME_REPLY_INNER)
    assert parsed is not None
    assert dt.datetime(*parsed) == dt.datetime(2026, 11, 2, 15, 47, 38)  # noqa: DTZ001
