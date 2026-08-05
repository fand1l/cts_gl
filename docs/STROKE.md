# The lasso stroke

> A wide white line, and a coloured glow where the finger is.

This is item 2 of `docs/POLISH.md`.

## What the screenshots actually show

An earlier draft of this file described a four-colour gradient running along the
stroke.  That was wrong, and two photographs of the real thing settled it:

* **The line is white.**  All of it, end to end, at full opacity, with round
  caps.  There is no colour on the line anywhere.
* **The colour is a glow at the head** — a soft round blob of light under the
  end of the line, about three times its width across.
* **The glow changes colour** over time: orange in one photograph, yellow in the
  other.
* **The glow stretches when the finger moves.**  Moving, it is an elongated
  smear pointing back the way it came; nearly stopped, it is a bright round
  circle.
* **The line is open.**  In the second photograph it goes right round an icon
  and the two ends simply pass each other without joining.

So: one plain white polyline, and one small animated light source at its end.

---

## The line

Wide (~12 logical px), opaque white, round caps and round joins so it reads as
one ribbon.  Under it, a translucent black stroke a few pixels wider — Android
draws over photographs and does not need one, but a white line on a white page
is invisible, and we are drawing over whatever the user had on screen.

Open, not closed.  `_selection_path()` today ends with `closeSubpath()`, which
at one pixel is a hint and at twelve is a bar across the middle of whatever is
being circled.  That method has to keep closing, because `_reveal_path()` uses
it when `lasso_mask` is on and a mask needs a closed shape — so the *visible*
stroke gets its own open path, which is what these two things always were.

## The glow

A radial gradient — the colour at the centre, transparent at the rim — filled as
an ellipse under the head of the line.  Drawn **before** the white stroke, so
the cap sits on top of it, which is how it looks in both photographs.

The colour drifts continuously through Google's four (blue `#4285F4`, red
`#EA4335`, yellow `#FBBC05`, green `#34A853`) on a cycle of a few seconds.  Two
photographs a moment apart showing orange and yellow is exactly what a slow
hue drift looks like caught twice.

## The stretch, as a decay trail

The obvious way to elongate a glow is to compute a shape: take the velocity,
build an ellipse along it, scale by speed.  That means differentiating a noisy
pointer signal, and it produces a shape that snaps around when the direction
changes.

The user's suggestion is better, and it is what this will do: **keep the last
handful of head positions and draw the glow at every one of them, fading with
age.**

* Moving fast, those positions are far apart, so the blobs lay out along the
  path and the glow *is* a smear — no velocity is ever calculated.
* Nearly still, they pile up on the same spot, so the blobs stack and the glow
  is round and brighter — which is exactly the difference between the two
  photographs.
* Lifting the finger, or stopping, lets the tail age out over a fifth of a
  second, so the smear settles into a circle by itself.

Each remembered position keeps the colour it had when it was recorded, so a fast
sweep shows a slight hue drift along the smear — a glow that both stretches and
changes colour, from one mechanism.

Stacking is plain source-over blending, not additive: a stationary glow
saturates towards its colour instead of blowing out to white.

```
    fast                              slow
    ·∘○◍●  ← older, fainter, spread   ◉  ← all on top of each other
```

## Why this is not the cursor glow that had to be removed

That one was a separate always-on-top window that followed the pointer around
the desktop.  It stole focus, and it repainted a large area at about ten frames
a second.

This one is *inside* the overlay that already has the focus and is already
frozen, and it repaints **only the glow's own rectangle** — a few hundred pixels
square — using the same partial-update trick as the "reading the screen" badge.
The screenshot underneath is not re-blitted; Qt clips it to the damaged region.

There is one timer, running at ~60 Hz while a drag is in progress and for a
fifth of a second after it ends, so the tail can be seen fading rather than
disappearing.

## What to delete while doing this

The dashed accent rectangle drawn around a lasso.  With `lasso_mask` off — the
default — the un-dimmed area *is* the bounding box already, so the dashed
rectangle is a second drawing of the same fact, and next to a ribbon this wide
it is visual noise.  Android does not draw one.

## Testing

The trail is a list of (point, colour, age) and the fade is a function of age,
so most of it is arithmetic and needs no screen:

* points older than the lifetime are dropped, newer ones kept in order;
* a stationary pointer collapses the trail to one place; a fast one spreads it
  over a distance that matches how far the pointer went;
* the colour cycle is continuous and returns to where it started.

The drawing is checked by rendering offscreen and reading pixels, the way the
lit text layer already is:

* a pixel on the line is white, and stays white next to a bright glow — the
  colour never leaks onto the stroke;
* a pixel in the glow is coloured, and one a glow-radius away is not;
* a white line drawn on a white screenshot is still distinguishable, which is
  what the shadow is for;
* the damaged rectangle asked for during a drag stays small — this is the one
  that matters most, because it is the thing that went wrong last time.

## Numbers to tune on real hardware

All constants at the top of the file, one line each to change:

| | first guess |
|---|---|
| line width | 12 px |
| shadow width | line + 4 px |
| glow radius | 38 px |
| trail lifetime | 180 ms |
| trail sample rate | every pointer move, capped at 90 per second |
| colour cycle | 3 s for all four |
| settle after release | 200 ms |

## Deliberately not doing

* **Velocity maths.** The trail gives the stretch for free and never snaps.
* **A shimmer on the resting line.** Colour means "here is the pointer".  A line
  that keeps sparkling after the pointer has gone says something untrue.
* **A setting.** It is lasso only; the rectangle is untouched, and that is the
  setting.
