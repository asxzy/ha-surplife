"""Tests for frame wrapping and stream parsing."""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol import framing

from . import golden


def test_wrap_matches_captured_state_query() -> None:
    """The wrapper reproduces the exact bytes we captured."""
    assert framing.wrap(golden.STATE_QUERY_INNER, 0x02, 0x00) == golden.STATE_QUERY_FRAME


def test_wrap_matches_captured_power_on() -> None:
    assert framing.wrap(golden.POWER_ON_INNER, 0x02, 0x0B) == golden.POWER_ON_FRAME


def test_wrap_matches_captured_clock_query() -> None:
    assert framing.wrap(golden.GET_TIME_INNER, 0x01, 0x06) == golden.GET_TIME_FRAME


def test_checksum_is_sum_mod_256() -> None:
    assert framing.checksum(b"\x01\x02\x03") == 6
    assert framing.checksum(b"\xff\x02") == 1
    assert framing.checksum(b"") == 0


def test_unwrap_roundtrip() -> None:
    inner = bytes.fromhex("e006")
    frame = framing.wrap(inner, 0x01, 0x09)
    assert framing.unwrap(frame) == inner


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", bytes(range(1, 200))],
    ids=["empty", "one-byte", "long"],
)
def test_unwrap_rejects_empty_inner(payload: bytes) -> None:
    if not payload:
        with pytest.raises(ValueError):
            framing.wrap(payload)
        return
    assert framing.unwrap(framing.wrap(payload)) == payload


def test_unwrap_rejects_bad_magic() -> None:
    frame = bytearray(framing.wrap(b"\x71\x23\x0f"))
    frame[0] = 0x00
    with pytest.raises(ValueError, match="B0 B1 B2 B3"):
        framing.unwrap(bytes(frame))


def test_unwrap_rejects_bad_checksum() -> None:
    frame = bytearray(framing.wrap(b"\x71\x23\x0f"))
    frame[-1] ^= 0xFF
    with pytest.raises(ValueError, match="checksum"):
        framing.unwrap(bytes(frame))


def test_unwrap_rejects_truncated_frame() -> None:
    frame = framing.wrap(b"\x71\x23\x0f")
    with pytest.raises(ValueError):
        framing.unwrap(frame[:-1])


def test_wrap_rejects_bad_counter() -> None:
    with pytest.raises(ValueError, match="counter"):
        framing.wrap(b"\x71\x23\x0f", counter=256)


def test_frame_parse_exposes_metadata() -> None:
    frame = framing.Frame.parse(golden.POWER_ON_FRAME)
    assert frame.version == 0x02
    assert frame.counter == 0x0B
    assert frame.inner == golden.POWER_ON_INNER


# ---------------------------------------------------------------------------
# FrameReader
# ---------------------------------------------------------------------------


def test_reader_parses_single_frame() -> None:
    reader = framing.FrameReader()
    reader.feed(golden.STATE_QUERY_FRAME)
    assert reader.pop() == golden.STATE_QUERY_FRAME
    assert reader.pop() is None


def test_reader_reassembles_split_frame() -> None:
    """TCP can split a frame arbitrarily; the reader must cope."""
    reader = framing.FrameReader()
    for byte in golden.STATE_QUERY_FRAME:
        assert reader.pop() is None
        reader.feed(bytes([byte]))
    assert reader.pop() == golden.STATE_QUERY_FRAME


def test_reader_parses_two_frames_in_one_read() -> None:
    """The device answers a pipelined query and command in a single read."""
    reader = framing.FrameReader()
    reader.feed(golden.STATE_QUERY_FRAME + golden.POWER_ON_FRAME)
    frames = reader.pop_all()
    assert frames == [golden.STATE_QUERY_FRAME, golden.POWER_ON_FRAME]


def test_reader_skips_leading_garbage() -> None:
    reader = framing.FrameReader()
    reader.feed(b"\xde\xad\xbe\xef" + golden.STATE_QUERY_FRAME)
    assert reader.pop() == golden.STATE_QUERY_FRAME


def test_reader_resyncs_after_corrupt_frame() -> None:
    """A corrupt frame is dropped and the next good one is still returned."""
    bad = bytearray(golden.STATE_QUERY_FRAME)
    bad[-1] ^= 0xFF
    reader = framing.FrameReader()
    reader.feed(bytes(bad) + golden.POWER_ON_FRAME)
    assert reader.pop() == golden.POWER_ON_FRAME


def test_reader_reports_buffered_bytes() -> None:
    reader = framing.FrameReader()
    reader.feed(golden.STATE_QUERY_FRAME[:5])
    assert reader.buffered == 5
    reader.feed(golden.STATE_QUERY_FRAME[5:])
    reader.pop()
    assert reader.buffered == 0
