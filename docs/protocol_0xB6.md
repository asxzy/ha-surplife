# Verified wire protocol — Surplife / Zengge 0xB6

Every claim here is tagged:

- **verified** — reproduced against the three controllers in this installation
  (`AK001-ZJ21413`, firmware 9.26, 35 LEDs) on 2026-11-02
- **inferred** — consistent with all captures but not directly proven
- **unknown** — no evidence

The captures behind this document are in [`evidence/`](evidence/). The
implementation is [`custom_components/surplife/protocol/`](../custom_components/surplife/protocol/).

---

## 1. Transport

TCP on port **5577**. UDP discovery on **48899**. **verified**

The controllers tolerate several simultaneous TCP connections, which is what
lets this integration run alongside the stock `flux_led` entry during
migration. **verified** (two concurrent sockets held open, both functional)

They are, however, fragile in specific ways worth knowing:

- A fresh connection occasionally ignores the first command. The client
  retries once on a new socket. **verified** (observed when opening sockets in
  quick succession)
- Bursts of writes get dropped. The client paces writes to one per 50 ms.
  **verified**
- After a multi-paint scribble render (`E1 23` + several `E1 26`), the
  controller **stops answering for up to ~2.6 s**. Polls issued in that window
  time out. Measured over repeated trials; the worst case observed was 2.63 s.
  **verified**
- **The controller acknowledges writes, including fire-and-forget ones, and the
  acknowledgement can arrive seconds later.** A paint reply is state-shaped
  (27 bytes, `EA 81 ...`), so it is indistinguishable from a state reply by
  content alone — only by the fact that it was not the answer to the current
  query. Unread acknowledgements therefore poison the *next* read: the client
  consumed a stale ack as the state frame and failed with
  `unparseable state reply: not a state frame (missing EA 81)`.
  **verified** (captured the raw stream: a wrapped frame plus a bare duplicate
  of the same 27 bytes; reproduced as a `set_scribble` 500 in Home Assistant)
- UDP discovery replies are lossy: one device in three failed roughly one
  probe in three, so discovery sends several broadcast rounds and merges.
  **verified** (three rounds; a single round found a random subset of the
  three controllers)

The first three are transient, so the client retries with a growing delay
(0.5 s, then 1.0 s) instead of a fixed one — a fixed short delay lands while
the device is still busy. Unread acknowledgements are handled structurally
rather than by waiting: every fire-and-forget write drains the socket
afterwards, and the state reader skips frames that are not state frames instead
of trusting the first reply it sees. The coordinator additionally tolerates up
to three consecutive failed polls before marking entities unavailable, because
the post-scribble pause can span a whole poll at the default interval.

---

## 2. Outer frame

Every command is wrapped:

```
b0 b1 b2 b3 | 00 01 | version | counter | len_hi | len_lo | inner | cksum
```

| Field | Meaning |
|---|---|
| `b0 b1 b2 b3` | magic |
| `00 01` | constant |
| `version` | `0x02` for `E1`/`E0 01`; `0x01` for clock and timers |
| `counter` | arbitrary byte, echoed by the device |
| `len` | inner length, big-endian |
| `cksum` | sum of every preceding byte, mod 256 |

There is **no inner checksum**: the length is followed directly by the
payload. **verified** for both wrapper versions.

The device echoes `version` and `counter` in its replies, which is a useful
sanity check when several commands are in flight.

---

## 3. State frame

A state query is the inner payload `ea 81 8a 8b` in a version-2 wrapper. The
reply is a 27-byte inner payload:

```
ea 81 01 00 b6 09 [PWR] [PRESET] [PATTERN] [SPEED] f0 b4 [H2] [SAT] [VAL] ff 00 05 00 [COUNT] 00 00 00 20 0c 01 02 03
 0  1  2  3  4  5    6      7        8        9    10 11  12   13    14 15 16 17   18
```

| Offset | Field | Notes |
|---|---|---|
| 4 | model | `0xb6` |
| 5 | firmware | `0x09` (reported as 9.26 by `AT+LVER`) |
| 6 | power | `0x23` on, `0x24` off |
| 7 | preset | `0x25` static, `0x24` segments, `0x66` scribble |
| 8 | pattern | see the caveat below |
| 9 | speed | 0-100 |
| 11 | hue | **halved**: 0-180 covers 0-360° |
| 12 | saturation | 0-100 |
| 13 | value | 0-100 |
| 18 | LED count | 35 on all three controllers here |

All offsets **verified** by differential probing: change one input, read the
frame back.

### 3.1 The pattern byte is not "the effect"

This is the single most misleading field on the device.

The byte at offset 8 is **always populated**, including in modes that are not
`E1 21` effects, and it is reused for other purposes:

| Situation | Offset 8 |
|---|---|
| Factory state, never commanded | `0x65` (= 101, "Static Gradient") |
| After a plain `E0 01` colour command | `0x6e` (not a documented effect) |
| After `E1 21` effect id *N* | *N* |
| After `E1 23`/`E1 26` scribble | retains the previous value |

Note the two shapes of the same problem. `0x65` is a *documented* effect id that
the device reports before it has ever been told to play that effect, so a fresh
device looks like it is running "Static Gradient" when it is showing nothing of
the sort. And after an effect has played, a later plain colour command leaves the
previous id in this byte rather than resetting it, so the byte reports a pattern
that is no longer playing.

The implementation therefore does not treat the byte as a mode signal on its
own. It remembers which non-effect mode *it* last commanded and distrusts the
byte until it changes, because a change is the only evidence that something
outside Home Assistant altered the mode. **verified** on hardware: commanding a
red fill after "Wave" left the byte at `1` while the strip showed a static red.

**verified** across the whole command matrix.

So a byte of `101` does **not** prove the user selected "Static Gradient", and
`0x6e` is not an effect at all. The implementation therefore treats only the
documented effect IDs (1-22, 101, 102) as effects and exposes the raw value
separately as `pattern`.

### 3.2 Colour and brightness are not echoed

Sending a plain `E0 01` colour command does **not** update bytes 11-13. Six
different colours in a row produced byte-identical frames. Only `E1 21` effect
colours reach those bytes. **verified**

This is a firmware behaviour, not a parsing gap. The Home Assistant entity keeps
its own optimistic colour and brightness and trusts the device only for power,
mode, effect id and speed.

One consequence worth stating plainly: **you cannot read back the current
colour over the local protocol.** If something else changes the colour (the
app, a hardware timer, another controller), Home Assistant will not know until
it is commanded again.

---

## 4. Power

```
71 23 0f    on
71 24 0f    off
```

Version 2. **verified** — the state frame's power byte flips immediately.

---

## 5. Colour, white and RGBW — `E0 01`

```
e0 01 00 A1 H/2 S V 00 00 00 00 14 00 00     colour only
e0 01 00 B1 00 00 00 00 LVL 00 00 14 00 00   white only
e0 01 00 00 H/2 S V 00 LVL 00 00 14 00 00    colour + white (RGBW)
```

| Byte | Meaning |
|---|---|
| 3 | write mode: `0xa1` colour, `0xb1` white, `0x00` both |
| 4-6 | hue (halved), saturation, value |
| 7 | white temperature — always 0, the strip has no cold-white LED |
| 8 | white level 0-100 |
| 11 | constant `0x14` |

Version 2. Both single-channel forms are **verified** on hardware. Byte 7
staying at zero is **verified** by the absence of any cool-white behaviour on
the strip.

**Write mode `0x00` is how white is selected without an RGB imitation.** The
strip has one white LED and three colour LEDs. Writing the colour channels to
zero and byte 8 to a level lights only the white LED; writing three equal bright
RGB values instead lights three colour LEDs and is dimmer and a different white.
The `0x00` frame is accepted by the firmware (it does not error or drop the
connection), and the field layout is the same one that the **verified** `E1 26`
paint below uses for its per-LED colour record.

Like `0xa1` and `0xb1`, the combined frame is **not echoed** in the state frame,
so the integration tracks colour and white optimistically.

---

## 6. Animated effects — `E1 21`

```
e1 21 01 64 PP OO DD NN SS 00 00 00 00 00 00 CC [H/2 S V 00 00] × CC
```

| Byte | Meaning |
|---|---|
| 2-3 | constants `01 64` |
| 4 | effect id |
| 5 | option / variant `0x00`-`0x02` |
| 6 | direction `01` left→right, `02` right→left |
| 7 | density 0-100 |
| 8 | speed 0-100 |
| 15 | colour count 1-8 |
| 16+ | 5 bytes per colour |

Version 2. **verified**: every effect ID round-trips through the state frame's
pattern and speed bytes.

### 6.1 Effect IDs

| ID | Name | ID | Name |
|---|---|---|---|
| 1 | Wave | 12 | Twinkling Stars |
| 2 | Meteor | 13 | Stars wink |
| 3 | Streamer | 14 | Warning |
| 4 | Building Blocks | 15 | Collision |
| 5 | Flowing Water | 16 | Fireworks |
| 6 | Chase | 17 | Comet |
| 7 | Horse Racing | 18 | Gradient Meteor |
| 8 | Cycle | 19 | Volcano |
| 9 | Breathe | 20 | Superluminal |
| 10 | Jump | 21 | Rainbow Bridge |
| 11 | Strobe | 22 | Gradient Overlay |
| | | 101 | Static Gradient |
| | | 102 | Static Fill |

The names are the app's own strings, harvested from the shipped bundle. Effect
**13 is `Stars wink`** with a lowercase *w* — that is the app's casing, kept
verbatim so the effect list matches what users see. **verified**
(`docs/evidence/effect_id_name_table.tsv`)

IDs 101 and 102 being in the same table as 1-22 is odd but is what the
reference captures show; 101 is also what the device reports in its factory
state, which matters for the caveat in §3.1.

---

## 7. Fixed segments — `E1 22`

```
e1 22 00 00 00 00 14 [H/2 S V 00 00] × 20
```

Exactly 20 slots, always. Unspecified trailing segments must be sent as zero
(a dark segment), not omitted. Version 2. **verified** — 20 zones render, and
the count of 20 is corroborated by the app shipping `color_segment_0` through
`color_segment_19` with no gaps.

Segments are *fixed* zones, not user-defined boundaries: the strip is divided
into 20 equal parts by firmware. **verified**

---

## 8. Per-LED ("scribble") — `E1 23` + `E1 26`

The app calls this **doodle / Graffiti / smear**. The wire protocol needs two
commands, and only one of them renders.

### 8.1 `E1 23` — mode entry, non-rendering

```
e1 23 01 00 01 50 64 00 N [00 00 00 00 00 00 64] × N
```

**verified**: on its own this does **not** light the strip. Its only observable
effect is flipping the state frame's preset byte to `0x66`. It is a buffer /
mode-init command.

### 8.2 `E1 26` — the render path

```
e1 26 EFFECT DIR DENSITY SPEED BLINK H/2 S V 00 WLVL BLINKSPD <bitmap>
```

| Byte | Meaning |
|---|---|
| 2 | effect 0-8 |
| 3 | direction |
| 4 | density |
| 5 | speed |
| 6 | blink mode: `00` none, `08` slow, `10` fast |
| 7-9 | hue (halved), saturation, value |
| 11 | white level |
| 12 | blink speed |
| 13+ | bitmap, `ceil(N/8)` bytes |

**verified**: `E1 26` both sets *and* renders. The device keeps a stateful
per-LED framebuffer, so each paint affects only its bitmap-selected LEDs and
leaves the rest untouched.

The 5-byte colour record `H/2 S V 00 WLVL` is the same one used by `E1 21`
effects and `E1 22` segments, so a per-LED LED can be colour, white, or both.
This is the field layout that establishes byte 8 of `E0 01` as the W level:
the paint is verified, and it puts white in the same position.

That last property is the reason the implementation **covers every LED in every
render**, padding short patterns with dark LEDs. Relying on the framebuffer to
retain prior values would make the result depend on what the strip was showing
before, which is not reproducible.

### 8.3 Bitmap encoding

MSB-first: LED *i* is byte `i // 8`, bit `7 - (i % 8)`. Length is
`ceil(N / 8)`; for 35 LEDs that is 5 bytes, and the final byte's low 5 bits are
zero. **verified** (`ff ff ff ff e0` selects all 35)

### 8.4 Scribble effects

Byte 2 accepts 0-8. The app names five of them; 3, 4, 6 and 7 are functional but
unnamed in the shipped strings.

| ID | Name |
|---|---|
| 0 | Static |
| 1 | Flowing |
| 2 | Twinkling |
| 3 | *(unnamed)* |
| 4 | *(unnamed)* |
| 5 | Stars Wink |
| 6 | *(unnamed)* |
| 7 | *(unnamed)* |
| 8 | Accumulate |

The named set is **verified** from the app bundle; that 3, 4, 6 and 7 are
accepted by the device is **verified**, but what they look like is **unknown** —
they were not visually characterised.

### 8.5 Grouping paints

LEDs sharing an identical `(rgb, white, blink_mode, blink_speed)` can share one
`E1 26`. The implementation groups on exactly that key, which yields the
minimum number of paints and lets each group blink independently. **verified**

---

## 9. Clock

Wrapper version **1**.

```
11 1a 14 00 00 00 00 00 00                  query
00 11 14 YY MM DD HH MM SS DOW 00           reply
10 14 YY MM DD HH MM SS DOW 00 0f           set
```

`DOW` is `isoweekday()` (Monday = 1). **verified** — set the clock, read it back.

Note the reply does **not** start with `11 14`; it starts with a `00` byte. A
parser that assumes a fixed offset gets this wrong, so the implementation
searches for the `11 14` signature instead.

---

## 10. Timers — `E0 06` / `E0 05`

Wrapper version **1**. Six slots.

```
e0 06                                   read all six
e0 05 SLOT <20-byte record>             write one
```

The reply concatenates all six slots. **verified**:

- an **active** slot is **21 bytes**: the slot byte plus a 20-byte record
- an **empty** slot is **7 bytes**: the slot byte plus six zeros

Getting the empty-slot stride wrong silently misparses every later slot — it
happened during development and is why this is called out explicitly.

Record layout for a write:

```
F0 HH MM 00 MASK 0E E0 01 00 ACTION [H/2 S V ...]
 0  1  2  3   4    5  6  7  8    9
```

For a read, the same bytes come back with the flag at offset 0 and the hours at
offset 1.

| Field | Meaning |
|---|---|
| `F0` | active flag (`00` = unset) |
| `MASK` | day mask, see below |
| `ACTION` | `23` on, `24` off, `a1` colour, `29` gradient scene, `6b` segments scene |

**Day mask**: bit 0 is reserved, Monday is bit 1. So `0xfe` is every day,
`0x3e` weekdays, `0xc0` weekend, `0x00` once. **verified**

The factory timers on the controller under test were slot 1 = on at 20:00 daily
and slot 2 = off at 00:30 daily, both with mask `0xfe`. **verified**

---

## 11. Discovery

UDP to 48899, broadcast address.

```
HF-A11ASSISTHREAD
→ 192.168.1.50,AABBCCDDEEFF,AK001-ZJ21413,00112233445566778899aabbccddeeff
```

`AT+LVER` returns `+ok=B6_26_20240514_ZG-BL-UFO`. **verified** on all three
controllers.

---

## 12. What is still unknown

- **Visual appearance of scribble effects 3, 4, 6 and 7.** They are accepted;
  nobody has described what they look like.
- **Meaning of the last three bytes** of the state frame (`20 0c 01`, then
  `02 03`). They are stable across every capture, so they are probably an
  ASCII-ish firmware build tag, but that is **inferred** at best.
- **Whether `E1 21` option `0x00`-`0x02` changes anything visible.** The device
  accepts all three; the captures do not show a difference.
- **The scene timer formats** (`action` `0x29` / `0x6b`). The implementation
  writes plain on/off/colour timers only. The scene variants take a
  variable-length payload that was not characterised. **unknown**
- **Behaviour above 35 LEDs.** Bitmap sizing is derived from the reported count
  and was verified at 35; upstream reference work used 80 and 100 on other
  units, but not here. **inferred**
