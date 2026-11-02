"""Outer frame construction and stream parsing.

Every command to a 0xB6 controller is wrapped in the LEDENET ``B0 B1 B2 B3``
envelope::

    b0 b1 b2 b3 | 00 01 | version | counter | len_hi | len_lo | inner | cksum

``version`` is ``0x02`` for the ``0xE1``/``0xE0 0x01`` command families and
``0x01`` for clock and timer commands. ``counter`` is an arbitrary byte the
device echoes back. ``cksum`` is the sum of every preceding byte, mod 256.

Neither wrapper version carries an *inner* checksum: the two-byte length is
followed directly by the payload.

**Verified** against hardware for both wrapper versions (2026-11-02).
"""

from __future__ import annotations

from dataclasses import dataclass

MAGIC = b"\xb0\xb1\xb2\xb3"
HEADER_LEN = 10  # magic(4) + 00 01(2) + version(1) + counter(1) + len(2)
CHECKSUM_LEN = 1

VERSION_EXTENDED = 0x02
VERSION_LEGACY = 0x01


def checksum(data: bytes | bytearray) -> int:
    """Return the LEDENET checksum: the sum of all bytes, mod 256."""
    return sum(data) & 0xFF


def wrap(inner: bytes, version: int = VERSION_EXTENDED, counter: int = 0) -> bytes:
    """Build a complete frame around an already-constructed inner payload.

    Args:
        inner: The payload, without any checksum of its own.
        version: Wrapper version, ``0x02`` for most commands, ``0x01`` for
            clock and timer commands.
        counter: Sequence byte echoed by the device; callers own incrementing.

    Returns:
        The full frame, ready to write to the socket.
    """
    if not 0 <= counter <= 0xFF:
        raise ValueError(f"counter must be 0..255, got {counter}")
    if not inner:
        raise ValueError("inner payload must not be empty")
    header = bytes(
        [
            *MAGIC,
            0x00,
            0x01,
            version,
            counter,
            len(inner) >> 8,
            len(inner) & 0xFF,
        ]
    )
    body = header + inner
    return body + bytes([checksum(body)])


def unwrap(frame: bytes) -> bytes:
    """Return the inner payload of a single complete frame.

    Raises:
        ValueError: if the frame is malformed or its checksum does not match.
    """
    if len(frame) < HEADER_LEN + CHECKSUM_LEN:
        raise ValueError(f"frame too short ({len(frame)} bytes)")
    if not frame.startswith(MAGIC):
        raise ValueError("frame does not start with B0 B1 B2 B3")
    inner_len = int.from_bytes(frame[8:10], "big")
    expected = HEADER_LEN + inner_len + CHECKSUM_LEN
    if len(frame) != expected:
        raise ValueError(f"frame length {len(frame)} != expected {expected}")
    body, received = frame[:-1], frame[-1]
    if checksum(body) != received:
        raise ValueError(
            f"bad checksum: computed {checksum(body):#04x}, got {received:#04x}"
        )
    return frame[HEADER_LEN : HEADER_LEN + inner_len]


@dataclass(frozen=True, slots=True)
class Frame:
    """A parsed frame, retaining the wrapper metadata."""

    version: int
    counter: int
    inner: bytes
    raw: bytes

    @classmethod
    def parse(cls, frame: bytes) -> Frame:
        """Parse a complete frame, validating magic, length and checksum."""
        inner = unwrap(frame)
        return cls(
            version=frame[6],
            counter=frame[7],
            inner=inner,
            raw=bytes(frame),
        )


class FrameReader:
    """Incremental parser for a byte stream of frames.

    TCP delivers arbitrary chunks, and a single read may contain several
    frames (the device answers a timer query with one frame, but a pipelined
    request can produce more). Feed bytes in with :meth:`feed` and pull
    complete frames out with :meth:`pop`.

    **Verified**: the device answers a state query and a following command on
    the same connection with two separate frames in one read.
    """

    __slots__ = ("_buf",)

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        """Append received bytes to the internal buffer."""
        self._buf.extend(data)

    @property
    def buffered(self) -> int:
        """Number of bytes held that do not yet form a complete frame."""
        return len(self._buf)

    def pop(self) -> bytes | None:
        """Return the next complete frame, or ``None`` if more bytes are needed.

        Bytes preceding the magic are discarded, which makes the reader
        resilient to stray leading data on a reused connection.

        Raises:
            ValueError: if a frame with the magic is present but its length or
                checksum is invalid. The offending frame is dropped so that
                subsequent frames can still be recovered.
        """
        while True:
            idx = self._buf.find(MAGIC)
            if idx < 0:
                # No magic anywhere; keep a 3-byte tail in case magic straddles.
                if len(self._buf) > len(MAGIC) - 1:
                    del self._buf[: len(self._buf) - (len(MAGIC) - 1)]
                return None
            if idx:
                del self._buf[:idx]
            if len(self._buf) < HEADER_LEN:
                return None
            inner_len = int.from_bytes(self._buf[8:10], "big")
            total = HEADER_LEN + inner_len + CHECKSUM_LEN
            if total > 8192:
                # Implausible length: drop the magic and resynchronise.
                del self._buf[: len(MAGIC)]
                continue
            if len(self._buf) < total:
                return None
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            try:
                Frame.parse(frame)
            except ValueError:
                continue
            return frame

    def pop_all(self) -> list[bytes]:
        """Drain every complete frame currently buffered."""
        frames: list[bytes] = []
        while (frame := self.pop()) is not None:
            frames.append(frame)
        return frames
