"""Client behaviour against a scripted socket.

These tests pin down how the client copes with replies that do not match the
request. Two real behaviours motivated them:

* The controller acknowledges some fire-and-forget writes, seconds later. Those
  bytes land in the receive buffer and can be read as the reply to the *next*
  query.
* The client speaks several frame dialects on one connection: state (``EA 81``),
  clock (``00 11 14``) and timer (``E0 06``) replies. A clock or timer reply
  arriving late is not a state frame, and the state reader must skip it rather
  than fail.

The failure this guards against is real: ``surplife.set_scribble`` returned
``unparseable state reply: not a state frame (missing EA 81)`` against the
hardware, because a reply belonging to an earlier exchange was consumed as the
state reply.

The fake socket models *when* bytes become readable, which is the whole point:
a reply to a query cannot be read before the query is written.
"""

from __future__ import annotations

import pytest

from custom_components.surplife.protocol import client as client_mod
from custom_components.surplife.protocol.client import (
    SurplifeClient,
    SurplifeConnectionError,
    SurplifeError,
)
from custom_components.surplife.protocol.framing import wrap

from . import golden


class ScriptedSocket:
    """A socket stand-in with separate "already arrived" and "reply" queues.

    ``pending`` is readable immediately -- stale data sitting in the buffer
    before the client writes anything. ``on_write`` is released the moment the
    client sends a command, which is how a real device behaves: it answers the
    command it just received.

    ``recv`` raises ``TimeoutError`` when nothing is readable, matching a real
    socket at its read deadline.
    """

    def __init__(self, pending: list[bytes], on_write: list[bytes]) -> None:
        self.pending = list(pending)
        self.on_write = list(on_write)
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, value: float | None) -> None:
        self._timeout = value

    def setblocking(self, flag: bool) -> None:
        self._blocking = flag

    def sendall(self, data: bytes) -> None:
        self.sent.extend(data)
        self.pending.extend(self.on_write)
        self.on_write.clear()

    def recv(self, size: int) -> bytes:
        if self.closed:
            raise OSError("socket closed")
        if self.pending:
            return self.pending.pop(0)
        raise TimeoutError("no data")

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def make_client(monkeypatch):
    """Build a client wired to a scripted socket, bypassing real connect."""

    def _make(
        pending: list[bytes] | None = None,
        on_write: list[bytes] | None = None,
    ) -> tuple[SurplifeClient, ScriptedSocket]:
        sock = ScriptedSocket(pending or [], on_write or [])
        client = SurplifeClient("192.0.2.10")

        def fake_connect() -> None:
            client._sock = sock  # noqa: SLF001 - test seam

        monkeypatch.setattr(client, "connect", fake_connect)
        # Retries would reconnect and replay the script; keep one attempt so
        # each test observes exactly one exchange.
        monkeypatch.setattr(client_mod, "MAX_ATTEMPTS", 1)
        return client, sock

    return _make


# A clock reply (``00 11 14 ...``). It is a perfectly valid frame on this
# connection, just not a state frame, so it must be skipped.
CLOCK_REPLY = wrap(golden.GET_TIME_REPLY_INNER, 0x01, 0x08)

# A timer reply (``E0 06 ...``). Same story: valid frame, wrong dialect.
TIMER_REPLY = wrap(golden.GET_TIMERS_REPLY_INNER, 0x01, 0x0A)


def test_state_query_skips_a_stale_clock_reply(make_client) -> None:
    """A late clock reply must not be read as the state frame."""
    client, sock = make_client(
        pending=[CLOCK_REPLY], on_write=[golden.STATE_OFF_FRAME]
    )

    state = client.get_state()

    assert state.power is False
    assert state.led_count == 35
    assert sock.pending == []


def test_state_query_skips_a_timer_reply_that_arrives_after_it(make_client) -> None:
    """The wrong-dialect reply may land after the query; keep reading."""
    client, _ = make_client(on_write=[TIMER_REPLY, golden.STATE_OFF_FRAME])

    state = client.get_state()

    assert state.power is False


def test_state_query_skips_several_stale_frames(make_client) -> None:
    """A backlog of unrelated replies must not hide the real one."""
    client, _ = make_client(
        pending=[CLOCK_REPLY, TIMER_REPLY],
        on_write=[golden.STATE_OFF_FRAME],
    )

    state = client.get_state()

    assert state.power is False


def test_state_query_raises_when_only_non_state_frames_arrive(make_client) -> None:
    """If every reply is the wrong dialect, that is a protocol error.

    The distinction matters: the coordinator retries a connection error but
    logs a protocol error, and the two need different diagnoses.
    """
    client, _ = make_client(on_write=[CLOCK_REPLY, CLOCK_REPLY])

    with pytest.raises(SurplifeError, match="unparseable state reply"):
        client.get_state()


def test_state_query_raises_when_nothing_arrives(make_client) -> None:
    """No reply at all is a connection error, so the coordinator can retry."""
    client, _ = make_client()

    with pytest.raises(SurplifeConnectionError):
        client.get_state()


def test_fire_and_forget_write_drains_stale_bytes(make_client) -> None:
    """A write must not leave earlier replies sitting in the buffer.

    Writes are sent without waiting, but the controller answers some of them
    anyway. Draining means the next query starts from a clean socket.
    """
    client, sock = make_client(pending=[CLOCK_REPLY])

    client.set_warm_white(80)

    assert sock.pending == []
    assert bytes(sock.sent)


def test_write_does_not_fail_when_drain_finds_nothing(make_client) -> None:
    """Draining an idle socket is a no-op, not an error."""
    client, sock = make_client()

    client.set_warm_white(80)  # must not raise

    assert bytes(sock.sent)
