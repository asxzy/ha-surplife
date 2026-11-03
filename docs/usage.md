# How to use

A practical guide, ordered from "turn it on" to "let the calendar decide".
Everything here was run against the real controllers before being written down.

## 1. The five things you will actually touch

Each controller (`Soffit East`, `Soffit West`, `Soffit South`) gets the same set
of entities. Using West as the example:

| Entity | What it is for |
|---|---|
| `light.soffit_west` | on/off, brightness, colour, warm white, 24 effects |
| `select.soffit_west_scene` | apply a saved scene, or today's scene |
| `select.soffit_west_per_led_look` | 9 one-tap per-LED patterns |
| `text.soffit_west_per_led_pattern` | type an exact per-LED pattern |
| `select.soffit_west_effect` | effect picker without a full `light.turn_on` |

Plus `number.*_effect_speed`, `number.*_effect_density`,
`select.*_direction`, `sensor.*_mode` (tells you if it is `static`,
`segments` or `scribble`), `sensor.*_timers`, and two buttons.

## 2. Everyday basics

From the dashboard, the light card does all of this. From YAML:

```yaml
# On
action: light.turn_on
target:
  entity_id: light.soffit_west

# Colour at half brightness
action: light.turn_on
target:
  entity_id: light.soffit_west
data:
  rgb_color: [255, 0, 0]
  brightness: 128

# Warm white — this device has a warm-white channel, not colour temperature
action: light.turn_on
target:
  entity_id: light.soffit_west
data:
  white: 200

# A native effect (24 available, case-insensitive)
action: light.turn_on
target:
  entity_id: light.soffit_west
data:
  effect: Chase
```

The 24 effects are: Wave, Meteor, Streamer, Building Blocks, Flowing Water,
Chase, Horse Racing, Cycle, Breathe, Jump, Strobe, Twinkling Stars, Stars wink,
Warning, Collision, Fireworks, Comet, Gradient Meteor, Volcano, Superluminal,
Rainbow Bridge, Gradient Overlay, Static Gradient, Static Fill.

> There is **no** `color_mode` parameter in `light.turn_on`. Warm white is
> `white: <0-255>`; that is a Home Assistant constraint, not this integration's.

## 3. Segments (20 fixed colour blocks)

```yaml
action: surplife.set_segments
target:
  entity_id: light.soffit_west
data:
  colors: ["#ff0000", "#ffffff", "#ff0000"]
```

Fewer than 20 colours is fine; the rest keep their previous colour.

## 4. Per-LED ("scribble")

Three levels of precision, all driving the same command:

```yaml
# 1. One tap — a built-in look
action: surplife.apply_look
target:
  entity_id: light.soffit_west
data:
  look: candy_cane
  palette: ["#ff0000", "#ffffff"]

# 2. Type it — the text entity
#    text.soffit_west_per_led_pattern  <-  "12xred 23xblue/fast"

# 3. Full control
action: surplife.set_scribble
target:
  entity_id: light.soffit_west
data:
  pattern: "18xw80 17xred/fast"
  effect: flowing
  direction: r2l
  density: 80
  speed: 60
```

The nine looks are `solid`, `stripes`, `candy_cane`, `gradient`, `rainbow`,
`firefly`, `twinkle`, `chase`, `wreath`.

Pattern grammar, space separated:

| Token | Meaning |
|---|---|
| `red` | one LED in `red` |
| `12xred` | a run of 12 LEDs |
| `red/fast` | one LED, blinking fast (`slow` also works) |
| `red/fast/50` | explicit blink speed |
| `#ff0000`, `255,0,0`, `w80` | hex, RGB triple, warm white at level 80 |

Colour names: `red`, `green`, `blue`, `yellow`, `cyan`, `magenta`, `purple`,
`orange`, `pink`, `white`, `warm_white`.

Short patterns are padded with **dark** LEDs on purpose — the controller keeps
a framebuffer, so an unmentioned LED would otherwise keep its old colour.

Per-LED effects: `static`, `flowing`, `twinkling`, `stars_wink`, `accumulate`.

## 5. Scenes

A scene is a saved recipe that can be applied to any controller. Save once,
use everywhere.

```yaml
action: surplife.save_scene
data:
  name: christmas
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

Modes: `rgb`, `white`, `effect`, `segments`, `scribble`, `look`.

Apply it:

```yaml
action: surplife.apply_scene
target:
  entity_id:
    - light.soffit_east
    - light.soffit_west
    - light.soffit_south
data:
  scene: christmas
```

Or from the dashboard: `select.soffit_west_scene` lists every saved scene with
its season and summary, so you can see what you are picking.

Other scene services: `surplife.delete_scene`, `surplife.import_scenes` (bulk
load from YAML, `replace_all: true` to wipe first).

## 6. The calendar (what this was built for)

Give scenes season windows and one call handles the rest:

```yaml
action: surplife.apply_scene_for_date
target:
  entity_id:
    - light.soffit_east
    - light.soffit_west
    - light.soffit_south
data:
  randomize: true
```

* No `date` → today, in Home Assistant's time zone.
* `randomize: true` → picks among the scenes in season.
* `topic: hoa_safe` → only consider scenes carrying that tag.

**The pick is seeded from the date**, so running it twice, or restarting HA,
does not flip the lights to a different scene. It also does not depend on the
order scenes are stored in.

A complete, tested automation:

```yaml
alias: Surplife daily scene
triggers:
  - trigger: sun
    event: sunset
    offset: "-00:30:00"
actions:
  - action: surplife.apply_scene_for_date
    target:
      entity_id:
        - light.soffit_east
        - light.soffit_west
        - light.soffit_south
    data:
      randomize: true
mode: single
```

Outside every season an all-year scene (one with no `start`/`end`) is used, so
keep one around — `warm_white` is a sensible default. If nothing is eligible
the service fails with a clear message rather than guessing.

`randomize: false` (the default) picks the highest `priority` instead, which is
what you want if you would rather control the order deliberately.

## 7. Hardware timers

The controller has six timer slots. Two were populated at the factory
(`20:00 on`, `00:30 off`, both everyday) — **check these before assuming a
failure is a bug**, and note they keep working even if HA is down.

```yaml
action: surplife.set_timer
target:
  entity_id: light.soffit_west
data:
  slot: 3
  time: "19:30"
  action: on
  repeat: everyday       # or weekdays, weekend, once, mon,wed,fri
  color: "#ff8800"       # optional

action: surplife.clear_timer
target:
  entity_id: light.soffit_west
data:
  slot: 3
```

`sensor.soffit_west_timers` shows what is currently set:

```
slot_1: 20:00 on Everyday
slot_2: 00:30 off Everyday
slot_3: unset
```

Timers run off the **device's own clock**, so after a power cut run
`surplife.sync_clock` (or press `button.soffit_west_sync_clock`) — otherwise
they fire at the wrong time.

## 8. Presets (quick seasonal looks)

Seven ready-made recipes, no setup needed:

```yaml
action: surplife.apply_preset
target:
  entity_id: light.soffit_west
data:
  preset: christmas
```

`halloween`, `christmas`, `thanksgiving`, `canada_day`, `valentines`,
`new_years`, `warm_white`.

Presets are shipped in code and are a different mechanism from scenes: presets
cannot be edited or given seasons, scenes can. Use presets for a quick look,
scenes for anything scheduled.

## 9. Adding a controller

Settings → Devices & Services → **Add Integration** → *Surplife Permanent
Lighting*. Discovered controllers are listed; if discovery is blocked by your
network, choose "Enter an IP address".

Set a **DHCP reservation** for each controller. The protocol is plain TCP on
port 5577 with no encryption *and no authentication* — the only thing standing
between your neighbours and your lights is that they are on a different
network, so keep them on a trusted VLAN and never port-forward 5577.

## 10. Things that look like bugs but are not

| Symptom | Why |
|---|---|
| Brightness/colour unknown after a restart | The firmware never echoes colour or brightness; the entity restores it locally. |
| Commands take a second | The controller stops answering for up to ~2.6 s after a per-LED write. |
| A light shows `unavailable` briefly | Three consecutive poll failures are tolerated before giving up; it recovers on its own. |
| Timers fire at odd times | Device clock drifted after a power cut — run `sync_clock`. |
| Setting the same effect twice does nothing visible | Some effects restart from the same phase. Change speed to force a visible change. |

## 11. Coexisting with the stock integration

This component is designed to run **alongside** your existing `flux_led`
entries. Verify the new entities behave, then disable the old entries. They
control the same hardware, so leaving both enabled means two sets of entities
fighting over the same strip.

## 12. Where to go next

* [`docs/scenes_and_looks.md`](scenes_and_looks.md) — how scenes, looks and the
  calendar are designed, and the evidence behind the choices.
* [`docs/protocol_0xB6.md`](protocol_0xB6.md) — the verified wire protocol.
* [`README.md`](../README.md) — install, entity list, full service list.
