# Architecture

## Why a standalone component

Home Assistant pins `flux-led` in the stock `flux_led` manifest. A custom
component that depends on unmerged `flux-led` branches therefore cannot
coexist with the built-in integration, and breaks on every HA upgrade when the
pin moves.

Instead this component vendors a **stdlib-only protocol layer** and depends on
nothing outside Home Assistant core. `manifest.json` has an empty `requirements`
list, which is what makes it safe to install alongside the stock integration.

## Layering

```
custom_components/surplife/
├── protocol/          stdlib only — no Home Assistant imports at all
│   ├── framing.py     B0B1B2B3 wrapper, checksum, incremental FrameReader
│   ├── constants.py   effect/scribble/timer tables
│   ├── state.py       27-byte EA81 state frame
│   ├── commands.py    inner-payload builders
│   ├── scribble.py    per-LED model + minimum-paint grouping
│   ├── timers.py      timer and clock parsing
│   ├── discovery.py   UDP probe with retry-and-merge
│   ├── spec.py        shared colour/pattern/repeat parsers
│   └── client.py      persistent socket, serialised writes, backoff retry
├── coordinator.py     async wrapper + optimistic state
├── light.py           the main control surface
├── select.py          effect, direction, per-LED look, scene
├── number.py          speed, density
├── text.py            per-LED pattern editor
├── sensor.py          LED count, mode, timers
├── button.py          sync clock, refresh
├── switch.py          deliberately empty
├── effect_profile.py  what each effect actually uses (colours, speed, density)
├── settings.py        per-effect and per-look settings in the entry options
├── services.py        scribble, segments, timers, presets
├── scene_services.py  scene CRUD, looks, date-driven apply
├── presets/           seven holiday recipes
├── scribble_looks.py  nine named per-LED pattern generators
├── scenes/
│   ├── model.py       Scene dataclass, validation, date selection
│   └── store.py       HA Store-backed persistence
├── config_flow.py     discovery, manual entry, and the per-effect control panel
└── manifest.json
```

The boundary is strict: **`protocol/` never imports Home Assistant.** That is
what lets the same code back the standalone CLI in `tools/`, and what makes the
134 protocol tests runnable without the HA test harness.

## The synchronous/async boundary

`protocol/client.py` is synchronous and blocking. `coordinator.py` marshals
every call through `asyncio.to_thread` behind a single `asyncio.Lock`:

```python
async def _run(self, func, *args, **kwargs):
    async with self._write_lock:
        return await asyncio.to_thread(func, *args, **kwargs)
```

Three consequences:

1. The event loop never blocks on a 6-second socket timeout.
2. Commands cannot interleave, which matters because the device has a stateful
   per-LED framebuffer and an in-order command stream.
3. In tests, the fake client must be built from plain `Mock`, **not**
   `AsyncMock` — an `AsyncMock` returns a coroutine object that `to_thread`
   hands straight back. This cost an afternoon once; it is why
   `tests/test_integration.py` says so in the fixture docstring.

## Failure tolerance

The controllers are not perfectly reliable, and the integration is built around
that rather than pretending otherwise:

| Behaviour | Handling |
|---|---|
| fresh socket drops its first command | retry on a new socket |
| post-scribble pause of ~2.6 s | retry with growing delay (0.5 s, 1.0 s) |
| late write acknowledgement in the buffer | drain after every fire-and-forget write |
| reply is the wrong frame dialect | skip it and keep reading |
| one poll fails in isolation | keep the last known state, stay available |
| three polls fail in a row | report the entity unavailable |

The third and fourth rows are one bug seen twice. The controller acknowledges
writes — paints especially — seconds after they are sent. A paint reply is
state-shaped, so a stale one is indistinguishable from the real answer by
content alone; the only defence is to never leave one unread. Every write now
drains the socket, and the state reader skips frames that are not state frames
(`EA 81`) rather than trusting the first reply. Before this, `set_scribble`
failed intermittently with `not a state frame (missing EA 81)`.

The last row matters. A single missed poll used to mark every entity
unavailable, which is both alarming and wrong when the device is merely busy
rendering the pattern it was just sent. Only a sustained run of failures is
treated as an outage.

`protocol/spec.py` holds the shared text parsers (colour tokens, the per-LED
pattern grammar, repeat phrases). Both the services and the CLI use it, so the
two cannot drift; it lives in the stdlib layer so it can be tested without Home
Assistant.

## Optimistic state

The firmware never echoes colour or brightness (§3.2 of the protocol doc). The
coordinator therefore keeps:

| Field | Source of truth |
|---|---|
| power | device |
| preset mode | device |
| effect id | device |
| speed | device |
| RGB | **commanded, optimistic** |
| brightness | **commanded, optimistic** |
| white level | **commanded, optimistic** |
| LED count | device |
| timers | device, on demand |

This is a deliberate, documented compromise rather than a bug. The alternative —
reporting the stale bytes the device does return — would show users a colour
they did not pick.

## Grouping

The app's device grouping is **cloud-side only**: a `GroupControl` record pushed
over MQTT, with no local-LAN group command. (`buildMeshGroupCommand` in the app
is BLE-mesh, which these Wi-Fi controllers do not speak.)

Local replacements, in increasing order of capability:

1. **HA light group** — one entity controlling all three, using HA's own
   `light.group`. Good for on/off and colour.
2. **`surplife.apply_preset` broadcast** — one service call, several targets,
   one deterministic command per controller. Better for holiday scenes, because
   the preset is identical on every strip by construction.

Presets are deterministic by design: applying `christmas` to three controllers
in any order produces the same result, which is what makes them a real
replacement for the cloud group.

## Discovery

Single UDP broadcasts are unreliable: one controller in three failed roughly one
probe in three. `discover()` sends several rounds and merges by MAC:

```python
for _ in range(rounds):
    sock.sendto(PROBE, ("255.255.255.255", 48899))
    # collect replies until timeout
```

Verified to find all three controllers reliably across repeated runs. **Verified**

## Entities

| Entity | Purpose |
|---|---|
| `light.<name>` | on/off, RGB, warm white, 24 effects |
| `select.<name>_effect` | effect picker (also on the light) |
| `select.<name>_direction` | animation direction |
| `select.<name>_per_led_look` | nine curated per-LED patterns |
| `text.<name>_per_led_pattern` | raw pattern editor (disabled by default) |
| `select.<name>_scene` | stored scenes, or today's scene |
| `number.<name>_effect_speed` | 0-100, read from the device |
| `number.<name>_effect_density` | 0-100, local only |
| `sensor.<name>_led_count` | sizes every bitmap |
| `sensor.<name>_mode` | static / segments / scribble |
| `sensor.<name>_timers` | configured slot count + detail |
| `button.<name>_sync_clock` | push HA time to the controller |
| `button.<name>_refresh` | force a state read |

The `select` and `number` entities exist alongside the light's own effect
support so effects can be driven from automations without constructing a full
`light.turn_on` payload.

`switch.py` registers nothing. The stock integration's switches are "remote
access" (a cloud feature) and "music mode" (no local protocol on 0xB6);
creating entities that do nothing would be worse than not creating them.

## Why scenes live outside the config entry

Scenes are stored once for the whole integration at
`.storage/surplife.scenes`, not per config entry. The same "Christmas" scene is
meant to drive all three strips at once, so one copy is the correct model;
per-entry copies could drift, and applying "the same scene everywhere" would
become ambiguous.

The store is created by the first entry and kept under a dedicated
`hass.data[DOMAIN]["__scenes__"]` key, so reloading any single entry does not
lose or duplicate it. It is dropped only when the last entry unloads, along with
the services.

Two robustness rules in the loader:

1. **A bad record is skipped, not fatal.** One hand-edited scene with an
   unknown effect must not take the whole collection down.
2. **Unknown keys are ignored on read.** A renamed field stays readable rather
   than bricking the store on upgrade.

## Why the daily pick is seeded

`select_for_date` chooses pseudo-randomly by default, but the RNG is seeded from
`date | topic | candidate names`. Two consequences, both required:

* the same day always resolves to the same scene, so an automation that runs at
  sunset and again after a restart does not flip the lights;
* the result does not depend on the order scenes happen to be stored in, so it
  is stable across a restore.

A plain `random.choice` would violate both.

## Why services carry `surplife_entry_id`

The `surplife.*` services accept light entity IDs. Resolving an entity ID to a
coordinator normally means walking the entity registry, which is heavier than it
needs to be. Instead the light exposes its config-entry id in its state
attributes:

```python
{"surplife_entry_id": self.coordinator.entry.entry_id, "pattern": ...}
```

A state read is then enough to find the coordinator. The raw `pattern` byte is
exposed for the same reason: it is the one device field that is easy to
misinterpret, so surfacing it makes diagnosis possible without a packet capture.
