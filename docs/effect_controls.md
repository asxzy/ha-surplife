# Controlling effects without raw commands

The light card gives you three controls: a colour wheel, a brightness slider and
an effect dropdown. That is the right shape for a plain colour, and the wrong
shape for this firmware's 24 effects. `Static Gradient` wants a first and a last
colour. `Rainbow Bridge` wants no colour at all. `Chase` wants a speed and a
direction but not a density. No single RGB picker expresses any of that, so the
only way to find out what an effect does would be to send a command and look at
the strip.

This integration therefore puts the per-effect controls in the device's
**Configure** dialog, and makes the light card the everyday control.

## Where the controls live

| You want to… | Use |
|---|---|
| Turn the strip on or off | The light card |
| Set a plain colour, or a real white | The light card's colour picker |
| Set overall brightness | The light card's brightness slider |
| Change an effect's colours | **Configure → Configure an effect** |
| Change a per-LED pattern's colours | **Configure → Configure a per-LED look** |
| Change speed or density while it runs | The *Effect speed* / *Effect density* sliders |
| Reverse an effect | The *Direction* selector |
| How often Home Assistant polls | **Configure → Change the poll interval** |

Configure is the **gear icon** on the device page, or *Settings → Devices &
services → Surplife → Configure*.

## What the panel asks for

Open **Configure an effect** and every effect is listed with what it actually
uses, so you can tell them apart before applying anything:

```
Wave — 1 colour
Static Gradient — 4 colours
Rainbow Bridge — no colour · saved
Static Fill — 1 colour
```

Pick one and you get only the fields that do something:

* **Colour pickers**, one per palette slot the effect uses. Each has its own
  **white level**, so you can light the real white LED alongside or instead of
  the colour. This is not simulated white: `#000000` with a white level of 100
  drives the W channel and no colour channel at all.
* **Speed** and **Density** sliders, shown only when the effect responds to them.
* **Direction**, shown only when reversing means something.

`Rainbow Bridge` shows no colour pickers, because it paints its own. `Static
Gradient` shows four, because a gradient's whole point is multiple stops.
Neither shows direction or density, because a still image has nothing to
reverse or repeat.

Four is a **UI** limit, chosen so the gradient form stays a phone-sized form
rather than seventeen controls. The wire format itself carries eight colours, so
an automation or service call may still pass up to eight; the panel never
overrules it.

Saving the panel **applies it immediately** and stores it. Selecting that effect
later — from the light card, the effect selector, or an automation — restores
the colours and parameters you saved. So the panel is how you configure an
effect once, and the dropdown is how you use it every day.

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
