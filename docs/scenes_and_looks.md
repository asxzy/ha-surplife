# Scenes, per-LED looks, and the calendar

This page covers the two features that are *not* a straight mapping onto a
device command: per-LED ("scribble") painting as a usable control surface, and
user-defined scenes that can follow the date.

## 1. Why these are composed, not downloaded

The Surplife app has its own "custom scene" (`symp_scene`, `custom_data_hc3`,
`symphony_scene_hc3`) and its own doodle templates. Neither is usable here, for
evidence-backed reasons.

### 1.1 The app's custom scene is a cloud command

`symp_scene_v2` does have a shipped template, in
`.../magichome2_home_data_provide/assets/wifi_dp_cmd.json`:

```
"symp_scene_v2": {"cmdForm": "42{model}{speed}{bright}", "needChecksum": true}
```

**verified** — the template exists and the field is checksummed.

But that is a **DP command** — the cloud/MQTT data-point channel — not the local
`0xB6` TCP protocol this integration speaks. The local transport has no `0x42`
command family; the families that exist are `0x71` (power), `0xE0 0x01`
(colour/warm white), `0xE1 0x21` (effects), `0xE1 0x22` (segments), `0xE1 0x23`
+ `0xE1 0x26` (per-LED), `0xE0 0x06`/`0xE0 0x05` (timers) and `0x11`/`0x10`
(clock). See `docs/protocol_0xB6.md`.

The device-side custom-effect payload is built by a family of functions the AOT
still names — `buildEffectPlayListHeaderHEXCommand`,
`buildCompressEffectFrameBodyHEXCommand`, `buildEffectEndFrameHEXCommand`,
`buildDynamicEffectRecurHexCommand` — which together describe a framed,
compressed, chunked blob. Its layout is **not** characterised. **unknown**

Product 182 does *declare* `symphony_scene_v2`, `custom_data_hc3`
(`幻彩自定义模式`), `symphony_scene_hc3`, `smear_hc3` and `smear_hc3_v2` among
its 41 functions, so the hardware may well support them over MQTT. Nothing in
the local protocol exposes them. **verified** (function list) / **unknown**
(local availability)

### 1.2 The doodle templates are preview art

The eleven `doodle/*.json` templates are Bodymovin/Lottie animations for the
picker UI, not frame data — see `docs/app_evidence.md` §4.2. They cannot be
turned into per-LED output.

### 1.3 What that leaves

Everything the local protocol *does* support is enough to express a scene:
colour, warm white, any of the 24 native effects, 20 fixed segments, and full
per-LED painting. A scene here is a composition of those verified commands. It
is strictly more expressive than the app's fixed scene slots, needs no cloud,
and cannot do anything the services cannot.

## 2. Per-LED looks

A look is a named, parameterised pattern generator that expands to exactly
`led_count` entries. They live in
`custom_components/surplife/scribble_looks.py`.

| Look | What it does |
|---|---|
| `solid` | every LED one colour |
| `stripes` | three-LED blocks, cycling the palette |
| `candy_cane` | two-colour diagonal banding |
| `gradient` | interpolation between palette stops |
| `rainbow` | full hue sweep (ignores the palette) |
| `firefly` | deterministic scatter of blinking dots on dark |
| `twinkle` | colour blocks, every third LED blinking fast |
| `chase` | one lit block on a dark strip |
| `wreath` | alternating lit/dark garland |

**Every look is deterministic.** The scatter in `firefly` comes from an integer
hash of the LED index, not from an RNG. That is a deliberate design choice: a
scene built on a look must produce the same LEDs every time, or a daily
automation would drift and no test could assert output.

### 2.1 The entities

| Entity | Purpose |
|---|---|
| `select.<name>_per_led_look` | pick one of the nine looks — **start here** |
| `text.<name>_per_led_pattern` | type a pattern, e.g. `12xred 23xblue/fast` |

Pick a look and the strip is painted. The look takes a palette of ordinary
colour pickers in **Configure → Configure a per-LED look**, so the everyday case
needs no syntax at all.

The text entity is the escape hatch for anything the nine looks do not cover. It
is **disabled by default**, because a pattern string is a command and learning
one should be a choice: enable it in the entity's settings if you want it. Its
state *is* the current pattern, so it survives a restart via `RestoreEntity`,
and it validates the whole string before sending, so a typo cannot half-paint
the strip.

Grammar, unchanged from `surplife.set_scribble` and the CLI. This is what the
opt-in text entity accepts; you do not need it to use the nine looks:

```
space-separated tokens
  <colour>                 one LED
  <n>x<colour>             a run of n LEDs
  <colour>/slow|fast       with a blink mode
  <colour>/<blink>/<speed> with an explicit blink speed
  w<n>                     white LED at level n (0-100), colour channels dark
  r,g,b,w                  white LED at level w added to the colour
```

Colours are a name, `#rrggbb`, or `r,g,b`. A fourth value is the W channel, so
`0,0,0,100` is the white LED alone while `255,0,0,60` mixes red with white.
Prefer `w<n>` or a fourth value over `255,255,255` when you want white: the
strip's white LED is brighter and a cleaner white than three colour LEDs
simulating one. Short patterns are padded with dark LEDs — deliberately,
because the device keeps a stateful per-LED framebuffer and an unmentioned LED
would otherwise keep its previous value.

## 3. Scenes

A scene is a named recipe. It lives in Home Assistant's `.storage`, not on the
controller, so it works identically on all three strips.

```yaml
service: surplife.save_scene
data:
  name: christmas
  title: Christmas
  mode: effect
  effect: Flowing Water
  colors: ["#ff0000", "#00c83c"]
  speed: 25
  density: 75
  start: "11-15"      # first eligible day
  end: "12-31"        # last eligible day, inclusive
  tags: holidays
  priority: 10
```

### 3.1 Modes

| Mode | Payload | Maps onto |
|---|---|---|
| `rgb` | `colors[0]`, `brightness` | `E0 01` colour, or RGBW if `colors[0]` has a fourth value |
| `white` | `white` | `E0 01` white channel |
| `effect` | `effect`, `colors`, `speed`, `density`, `direction` | `E1 21` |
| `segments` | `segments` (up to 20) | `E1 22` |
| `scribble` | `pattern` | `E1 23` + `E1 26` |
| `look` | `look` | `E1 23` + `E1 26` |

### 3.1.1 Colour is RGBW

The strip has one white LED. A colour entry may therefore be a triple
(`#ff0000`) or a quadruple whose fourth value is a W level 0-100:

```yaml
colors: ["0,0,0,100"]     # the white LED, no colour — a real white
colors: ["255,0,0,60"]    # red mixed with white
colors: ["#ff0000"]       # colour only
```

`0,0,0,100` is the recommended way to ask for white. `255,255,255` also looks
white, but it lights three colour LEDs to imitate the white LED, which is dimmer
and a different shade. The same applies to `segments` entries and to the
per-LED pattern's fourth value.

Applying a scene also turns the strip on if it is off, so "apply the scene" is
one operation rather than two.

### 3.2 Seasons

`start` and `end` are `MM-DD`, inclusive. **Windows wrap the year end**, so
`12-01` to `01-06` behaves as you would expect. Omitting both means "all year";
omitting one leaves the other end open.

Scenes are selected by `select_for_date`, which filters by season and optional
tag, then:

* without `randomize`: highest `priority` wins, name breaks ties;
* with `randomize`: picks pseudo-randomly, **seeded from the date, topic and
  candidate names**.

The seeding is the important part. An automation that runs at sunset, and again
on a restart, must not flip the lights to a different scene. Seeding from the
date makes the choice stable for the day and independent of the order the
scenes happen to be stored in.

### 3.3 Services

| Service | Purpose |
|---|---|
| `surplife.save_scene` | create or replace a scene |
| `surplife.delete_scene` | remove one |
| `surplife.apply_scene` | apply a named scene to one or more lights |
| `surplife.apply_scene_for_date` | apply whatever belongs to a date |
| `surplife.import_scenes` | bulk load (optionally replacing everything) |
| `surplife.apply_look` | render a look without saving a scene |

### 3.4 The daily automation

```yaml
automation:
  - alias: Surplife seasonal scene
    triggers:
      - trigger: sun
        event: sunset
    actions:
      - action: surplife.apply_scene_for_date
        target:
          entity_id:
            - light.soffit_east
            - light.soffit_west
            - light.soffit_south
        data:
          randomize: true
```

That is the whole feature the user asked for: every day, the strips pick a scene
that is in season, varying with the date, and they stay consistent if the
automation runs more than once.

Add `topic: hoa_safe` to restrict the pick to scenes carrying that tag — useful
when some scenes are too bright or too colourful for a given context.

## 4. Storage

One store for the whole integration, at `.storage/surplife.scenes`:

```json
{"scenes": [{"name": "christmas", "mode": "effect", "effect": "Flowing Water",
             "colors": [[255, 0, 0], [0, 200, 60]], "start": "11-15",
             "end": "12-31", "tags": ["holidays"], "priority": 10, ...}]}
```

Sharing one store rather than one per config entry is deliberate: the scenes are
shared vocabulary across the three controllers, and duplicating them per entry
would let them drift apart. It also means an entry reload does not lose them.

The loader **skips invalid records instead of raising**, so a hand-edited file
with one bad entry still yields the rest. Unknown keys are ignored on read, so a
renamed field does not brick an existing store.
