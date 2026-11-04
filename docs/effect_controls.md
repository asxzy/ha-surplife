# Controlling effects without raw commands

The light card gives you a colour wheel, a brightness slider and an effect
dropdown. That is the right shape for a plain colour, and the wrong shape for
this firmware's 24 effects. `Static Gradient` blends several colours.
`Rainbow Bridge` takes no colour at all. `Static Fill` has nothing to reverse and
nothing to repeat. No single RGB picker expresses any of that, so the only way
to find out what an effect does would be to send a command and look at the
strip.

This integration therefore puts the per-effect controls in the device's
**Configure** dialog, and makes the light card the everyday control.

## Finding the colour wheel

**The colour control is hidden behind an icon, and the dialog does not open on
it.** This is easy to miss, and it is not something the integration can change.

Home Assistant's light dialog opens on **brightness** — its frontend hard-codes
that default and gives integrations no way to override it. The colour wheel is
the third icon in the row beneath the slider:

```
[ power ]  [ bulb ]  [ colour wheel ]  [ W ]
                        ^^^^^^^^^^^
                        click this
```

The bulb is brightness. The rainbow circle is the colour wheel. The **W** is the
white LED channel. Once you click the rainbow, the wheel stays open for that
entity.

## Colouring a pattern from the light card

Pick a pattern from the card's **Effect** dropdown, then move the **colour
wheel**. The colour goes to the pattern that is running — it does not stop it.
The effect keeps its speed, density and direction and simply takes the new
colour. The choice is saved, so picking that pattern again later brings the
colour back.

**On a multi-colour pattern the wheel replaces the first colour and leaves the
rest alone.** The card can only ever express one colour, so treating a wheel
touch as the whole palette used to collapse a three-colour `Chase` to a single
colour the moment you nudged the wheel. It now substitutes into slot one, which
is the colour the wheel is showing, so the other slots survive.

Pick **Static** from the Effect dropdown to leave pattern mode. From then on the
colour wheel sets a flat colour again.

## Setting the whole palette from the device page

The light card's wheel can only carry one colour, so a second surface exists for
the list: **Effect colours**, a text field in the device page's *Controls* card.
It is enabled by default — no gear icon, no second menu.

It always shows the palette the running effect is using, in order:

```
Effect colours
#ff0000, #00ff00, #0000ff
```

Type a new list and press Enter. The running effect is recoloured in place and
the new palette is saved against it, exactly as if you had used the Configure
panel. The grammar is the same one the services and the CLI use:

| Spelling | Example |
|---|---|
| Colour name | `red, green, blue` |
| Hex | `#ff0000, #00ff00` |
| RGB triple | `255,0,0, 0,0,255` |
| Real white channel | `#000000, w100` — drives the W LED, not a simulated grey |

Anything it cannot use is refused with the reason in a notification, and nothing
is sent to the strip:

* more colours than the effect takes — `4 colours given but this effect takes at
  most 3`;
* a palette on `Rainbow Bridge`, which paints its own;
* an unparseable token, or an empty field.

The field's attributes say what the current effect accepts — `effect`,
`max_colours`, and a `hint` — so you do not have to remember the cap table.

## Where the controls live

| You want to… | Use |
|---|---|
| Turn the strip on or off | The light card |
| Set a plain colour, or a real white | The light card's **rainbow icon**, with **Static** selected |
| Set overall brightness | The light card's bulb icon |
| Recolour one colour of the running pattern | The light card's colour wheel |
| Set the whole palette with colour pickers | **Configure → Change the running effect's colours** |
| Set the whole palette by typing it | **Effect colours** on the device page |
| Change a per-LED pattern's colours | **Configure → Configure a per-LED look** |
| Change speed or density while it runs | The *Effect speed* / *Effect density* sliders |
| Reverse an effect | The *Direction* selector |
| How often Home Assistant polls | **Configure → Change the poll interval** |

Configure is the **gear icon** on the device page, or *Settings → Devices &
services → Surplife → Configure*.

## Recolouring with pickers, in two taps

**Configure → Change the running effect's colours** is the short path. It is the
first item in the menu because it is what you do to an effect *after* it is set
up.

It skips the effect picker entirely — the target is whatever is showing — and
shows only that effect's colour wheels, pre-filled with the colours the strip is
using right now. Submit and the effect takes the new colours and keeps running.
Add and remove rows the same way the effect panel does, one colour at a time.

If nothing is running it says so instead of showing empty pickers, and an effect
that paints its own colours (`Rainbow Bridge`) is refused for the same reason.

## What the panel asks for

Open **Configure an effect** and every effect is listed with what it actually
uses, so you can tell them apart before applying anything:

```
Wave — 1 colour
Chase — 1-8 colours
Static Gradient — 1-4 colours
Rainbow Bridge — no colour · saved
```

Pick one and you get only the fields that do something:

* **Colour rows**, one per colour the effect currently has. Each has its own
  **white level**, so you can light the real white LED alongside or instead of
  the colour. This is not simulated white: `#000000` with a white level of 100
  drives the W channel and no colour channel at all.
* **Add another colour** / **Remove the last colour**, which grow or shrink the
  palette by one row without saving.
* **Speed** and **Density** sliders, shown only when the effect responds to them.
* **Direction**, shown only when reversing means something.

### Building a multi-colour palette

The panel starts with the number of colours the effect is designed around — one
for `Wave`, two for `Chase`, four for `Static Gradient` — and grows one colour at
a time, the way the phone app does:

1. Pick the first colour.
2. Turn on **Add another colour** and submit. The form comes back with one more
   row, and everything already picked still filled in.
3. Repeat until the effect's cap. The tick disappears at the cap.
4. **Remove the last colour** drops the last row the same way, without saving.

Nothing is written to the controller until you press Submit without one of those
two ticks on, so you can build the whole palette first and send it once.

The caps are per effect, not one number for everything:

| Effect | Colours |
|---|---|
| `Wave`, `Meteor`, `Breathe`, `Jump`, `Strobe`, `Twinkling Stars`, `Horse Racing`, `Static Fill` | 1 |
| `Chase`, `Streamer`, `Building Blocks`, `Flowing Water`, `Cycle`, `Stars wink`, `Warning`, `Comet`, `Superluminal` | up to 8 |
| `Collision`, `Fireworks`, `Volcano` | up to 3 |
| `Static Gradient`, `Gradient Meteor`, `Gradient Overlay` | up to 4 |
| `Rainbow Bridge` | none — it paints its own |

A single-colour effect shows one picker and no Add tick, because a second colour
would be inert. The wire format itself carries eight colours, so an automation
or service call may still pass up to eight regardless; the panel never overrules
it.

Saving the panel **applies it immediately** and stores it. Selecting that effect
later — from the light card, the effect selector, or an automation — restores
the colours and parameters you saved. So the panel is how you configure an
effect once, and the dropdown is how you use it every day.

Recolouring from the card writes back to the same place, so a colour you picked
on the card and a colour you picked in the panel are one stored setting, not two
competing ones. The panel is still where you go for a *multi-colour* palette,
since the card's wheel carries only one colour.

If the controller is offline when you save, the settings are still stored; you
can apply them when it comes back.

## Per-LED patterns

The same idea replaces typing a pattern string. **Configure a per-LED look**
lists each pattern with the number of colours it distinguishes:

```
Solid — Every LED the same colour. (1 colour)
Stripes — Alternating three-LED blocks in palette order. (2 colours)
Rainbow — Full hue sweep across the strip. (fixed colours)
```

Pick one and you get a colour picker per slot it uses, plus the per-LED
animation, speed, density and direction. `Rainbow` gets no pickers because its
colours are generated.

The pattern text box is still there under the device as *Per-LED pattern*. It
remains the escape hatch for an exact per-LED layout, but nothing requires it.

## Two units, one rule

The **white level** in every picker is a percentage, 0-100. That is the unit the
protocol uses for the W channel everywhere. Home Assistant's own `rgbw_color`
uses 0-255, and the conversion happens at that boundary only — so a service call
with `rgbw_color: [0, 0, 0, 255]` and a panel with white level 100 mean the same
thing.

## When the light card is enough

If you only ever want one colour, or one of the single-colour effects at its
saved settings, the light card is all you need. The panel exists for the cases
it cannot express, not to replace it.

## A worked example: a two-colour gradient

1. **Configure → Configure an effect → Static Gradient.**
2. First colour `#ff0000` (red), white level 0.
3. Second colour `#00ff00` (green), white level 0.
4. Leave the remaining colours empty — they are optional.
5. Submit. The strip shows the gradient immediately.

From then on, selecting `Static Gradient` in the light card's effect dropdown
brings that gradient back, and `light.turn_on` with
`effect: Static Gradient` does the same from an automation.

To make it a warm white instead, set the first colour to `#000000` with a white
level of 80. That lights the white LED, not three colour LEDs imitating white.

## A worked example: a three-colour chase, from the device page

1. On the light card, pick **Effect → Chase**.
2. On the device page, find **Effect colours** in *Controls* and type
   `#ff0000, #00ff00, #0000ff`.
3. Press Enter. The strip starts chasing red → green → blue and the field now
   reads `#ff0000, #00ff00, #0000ff`.
4. Nudge the colour wheel on the light card. Slot one changes; green and blue
   stay. The field updates to match.

From an automation, the same result without the UI:

```yaml
action:
  - service: text.set_value
    target:
      entity_id: text.soffit_west_effect_colours
    data:
      value: "#ff0000, #00ff00, #0000ff"
```

