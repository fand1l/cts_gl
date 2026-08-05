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
* **The glow's colour comes from where it is on the screen**, not from a clock.
  Tested directly: blue at the top, red across the middle, yellow a little below
  that, green at the bottom.  A fast swipe from one corner to the other lays all
  four out at once, in that order, top to bottom.
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
the cap sits on top of it, which is how it looks in every photograph.

It is far bigger and softer than a first guess suggests: on a fast swipe the
colour washes out over a good fraction of the screen, brightest along the line
and falling away gently for a hundred logical pixels or more.

### The colour is a vertical ramp

Not a clock.  The colour of a blob is read off **where that blob is**, top to
bottom of the screen:

| height | |
|---|---|
| top | blue `#4285F4` |
| ~40 % | red `#EA4335` |
| ~62 % | yellow `#FBBC05` |
| bottom | green `#34A853` |

with linear interpolation between them, so it is one pure function of one
number:

```python
def glow_colour(y: float, screen_height: float) -> QColor
```

This explains the earlier photographs, which I had read as a time cycle: one
showed orange and the other yellow because *both* heads happened to be in the
lower-middle of the screen, where the ramp runs from red through orange into
yellow.  Nothing was drifting; they were simply at similar heights.

Normalised against the height of **the screen the overlay covers**, not the
whole virtual desktop — otherwise the same gesture would come out a different
colour depending on which monitor it happened on.

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
* Lifting the finger, or stopping, lets the tail age out, so the smear settles
  into a circle by itself.

The third photograph is the strongest evidence for this model, and it was taken
to show something else.  A swipe across the whole screen in a fraction of a
second leaves **every point of it younger than the trail lifetime**, so the glow
runs the entire length of the line — while the slow, careful circle in the
second photograph has a glow only at its head, because everything behind it had
already aged out.  Same rule, opposite-looking results.

Each remembered position takes its colour from its own height, so a stroke that
covers vertical distance carries the whole ramp along itself.  That is precisely
what the fast-swipe photograph shows: one white line from the bottom-left corner
to the top-right, with blue at its top end, red across the middle, orange below
that and green at the bottom.  Nothing extra had to be written for it.

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
* the ramp hits its four colours at its four heights, interpolates between them,
  and clamps rather than wrapping above the top or below the bottom;
* a stroke drawn top to bottom carries all four colours; the same stroke drawn
  along one height carries one.

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
| glow radius | 140 px, with most of the falloff in the outer half |
| trail lifetime | 500 ms |
| trail sample rate | every pointer move, capped at 90 per second |
| ramp stops | 0 % blue · 40 % red · 62 % yellow · 100 % green |
| settle after release | 200 ms |

## Deliberately not doing

* **Velocity maths.** The trail gives the stretch for free and never snaps.
* **A shimmer on the resting line.** Colour means "here is the pointer".  A line
  that keeps sparkling after the pointer has gone says something untrue.
* **Additive blending.** It would read more like light, but stacked blobs would
  blow out to white where the pointer rests — and the photographs show a
  stationary glow saturating into a deep amber, not into white.  Worth trying
  once, on hardware, before ruling it out for good.
* **A setting.** It is lasso only; the rectangle is untouched, and that is the
  setting.

---

## As built

All of the above, with three things worth recording.

**Built and tested on its own**, after the other seven items of `docs/POLISH.md`
were in and settled — which is what it was moved to the end for.

**One repaint claim had to be narrowed.**  This file said the stroke "repaints
only the glow's own rectangle".  That is true of the *animation timer*: while
the pointer is still, only `Trail.bounds()` is damaged — a few hundred pixels
square, and the tests assert it directly.  It is not true of an ordinary drag
move, which repaints the whole overlay, because the un-dimmed bounding box grows
as the loop is drawn and a partial update there would leave stale pixels behind.
That was already the case before any of this.

**A bug came out of the drawing, not the design.**  A stroke drawn along one
axis has a bounding box with no area, and `paintEvent` returned early on exactly
that condition, so a horizontal line left the screen blank while it was being
drawn.  At one pixel nobody had noticed.  There is a test for it now.

**Additive blending is still worth trying once on hardware.**  Source-over was
the right call: a stationary pointer saturates into a deep amber, which is what
the photographs show, and the offscreen render confirms it.

### Corrected after the first real use

**The stroke does not survive the gesture.**  This file assumed the ribbon
stayed on screen once the loop was closed, and gave the tail a fifth of a second
to settle after the button came up.  On a real desktop that is wrong twice over:
a twelve-pixel white line lying across the selection is in the way of reading
it, and Android does not keep it either — the stroke is part of the gesture, not
part of the answer.  So the line and the glow both end with the drag, the
`SETTLE_MS` fade is gone, and the fade that remains is the one *during* a drag,
for a pointer held still.

What is left afterwards is the un-dimmed box, its handles and the action bar.
With `lasso_mask` off there is no thin outline either, by the same argument that
removed the dashed rectangle: the un-dimmed area already is the crop.

### The thing that actually cost frames

A long scribble dropped the overlay to about one frame a second, reported from a
real desktop with a screenshot.  Measured rather than guessed, on a 1920×1080
screen at 2×, with a 1500-point path:

| | |
|---|---|
| `drawPath`, antialiased, one pass | **88 ms** |
| the same path with antialiasing off | 5.2 ms |
| blitting the whole screenshot | 2.6 ms |
| rebuilding the path from `_points` | 2.3 ms |
| one glow blob | 0.65 ms |
| **whole frame** | **229 ms** |

So the cost was the ribbon, and it was linear in the length of the line — which
is exactly why it got worse the longer you drew.  Two things came out of that:

* **Clipping the painter does not help.**  A clipped frame measured *slower*
  than an unclipped one (266 ms against 229): Qt rasterises the whole stroked
  path before anything is clipped away, so a small damage rectangle saves
  nothing when the path is what costs.  This is worth knowing before anyone
  reaches for partial repaints to fix a drawing that is slow.
* **The path had to stop being re-stroked.**  The settled part is baked into a
  pixmap in chunks of `_STROKE_CHUNK` points, and only the tail is stroked per
  frame, so the per-frame cost is flat.  The chunk overlaps the tail by one
  segment so the round caps meet on a shared point, and the tail's *shadow* goes
  down before the layer is blitted so the frozen white covers it at the join.
  The layer is a full-screen pixmap, so it is allocated only when a line grows
  past one chunk and given back the moment the drag ends.

The glow was the other half.  It is the same shape every time — only the colour
and the amount left change, and "the amount left" is the painter's opacity — so
each colour is rendered once and blitted after that, keyed on the colour rounded
to five bits a channel.  Forty-five blobs went from 29 ms a frame to under two.

| | before | after |
|---|---|---|
| 400 points, full trail | 195 ms | 11 ms |
| 1500 points, full trail | 258 ms | 12 ms |
| 4000 points, full trail | 660 ms | 13 ms |

Still standing, and deliberately not done: the whole overlay is repainted on
every pointer move.  Clipping is worth about 20 % now that the path is cheap,
which is not enough to justify the stale-pixel risk of tracking damage across
the dimming, the size readout and the loupe — and it is what the program did
before the stroke existed.
