# The lasso stroke

> A wide white line, with colour only where the pointer is moving right now —
> the way Circle to Search draws on Android.

This is item 2 of `docs/POLISH.md`.  It replaces what was originally proposed
there (a dark outline under the thin accent line) and folds that outline in, as
the shadow that keeps a white stroke visible on a white background.

Lasso only.  A rectangle has no "where you are moving", so it keeps a plain
outline; the same shadow trick applies to it.

---

## What it looks like

* The stroke is **wide** — about 12 logical pixels — with round caps and round
  joins, so it reads as one continuous ribbon rather than a chain of segments.
* Behind the pointer it is **white**.
* The last stretch of it — roughly 160 logical pixels of *path length*, not of
  straight-line distance — carries a gradient running blue → red → yellow →
  green → white, with the newest colour at the pointer.  Circling slowly draws a
  short colourful head; a fast sweep stretches it out.
* Under all of it, a translucent black stroke a few pixels wider, so the white
  is visible on a white page.
* When the button is released the colour **slides off the end** over about a
  quarter of a second and the stroke settles to plain white.  Nothing is moving
  any more, so nothing is coloured.

## Why it has to be drawn segment by segment

`QPen` takes a `QBrush`, so a gradient pen is possible — but a `QLinearGradient`
is laid out in **device space**, not along the path.  On a straight line that
looks right; on a loop it paints a gradient across the *screen* while the stroke
wanders through it, which is not the effect at all.  `QPainterPathStroker` has
the same problem: it gives an outline polygon, still filled in device space.

So the head is drawn as individual line segments, each with its own solid
colour, which is the ordinary way to get a gradient along a path in Qt.  Round
caps and joins make the seams disappear, and every colour is **fully opaque** —
overlapping translucent segments would darken every joint and every place the
loop crosses itself.

## Keeping it cheap

A lasso accumulates a point every 3 px (`_LASSO_MIN_STEP`), so a long loop is
several hundred points, and the overlay repaints on every mouse move.  Several
hundred wide antialiased segments per frame is not free.

Only the head needs per-segment work.  Everything older than the coloured band
is one colour, so it is one `QPainterPath` and one stroke:

```
points:  [0] ....................................... [n-3] [n-2] [n-1]
         └────────── one white path ──────────┘ └── ~40 coloured segments ──┘
```

The split point is found from a running total of path length kept beside the
points — appended when a point is appended, so finding "the index 160 px back"
is a short walk from the end, not a rescan.

That also bounds the work: the coloured part is a fixed *length* of path, so it
is a roughly constant number of segments no matter how long the loop gets.

## The colour ramp

A pure function of one number — how far back along the path a segment sits — so
it can be unit-tested without a screen:

```python
def stroke_colour(distance_from_head: float, band: float = BAND) -> QColor
```

* `0.00` → Google blue `#4285F4`
* `0.25` → red `#EA4335`
* `0.50` → yellow `#FBBC05`
* `0.75` → green `#34A853`
* `1.00` and beyond → white

with linear interpolation between the stops, in plain RGB.  The stops are the
familiar four, in the order the Android animation uses them.

The release animation is the same function with an offset added to every
distance: as the offset grows past `band`, the whole stroke is white and the
timer stops.  One value to animate, no second code path.

## Drawing order

Inside the existing `paintEvent`, where `drawPath(self._selection_path())` is
today:

1. the shadow — the whole polyline, translucent black, `WIDTH + 4`;
2. the white tail — one path, one stroke;
3. the coloured head — segment by segment, newest last;
4. the dashed bounding box, thinner and quieter than it is now, because with a
   ribbon this wide it no longer has to carry the whole message of "this is what
   gets uploaded".

## One thing to change while doing it

`_selection_path()` currently ends with `closeSubpath()`, which draws a line
from the pointer back to where the loop started.  At one pixel that is a hint;
at twelve it is a bar across the middle of whatever is being circled.

The visual stroke should be an **open** polyline.  `_selection_path()` itself
has to keep closing, because it is also what `_reveal_path()` uses when
`lasso_mask` is on and a mask needs a closed shape — so these become two paths
for two purposes, which they always were in truth.

## How it gets tested

The ramp is a pure function: stops land on their colours, values between them
interpolate, anything past the band is white, and adding the release offset
whitens the whole thing.

The drawing is checked the way the lit text layer already is — render the
overlay offscreen and read pixels back:

* a pixel on the stroke near the pointer is coloured (its channels differ), one
  far down the tail is white (they do not);
* sampling across the stroke finds white for at least the full width, and the
  darker shadow just outside it;
* a stroke drawn over a white screenshot is still distinguishable from it, which
  is the whole point of the shadow;
* a long loop still only colours a bounded number of segments — the split index
  is a plain function of the running lengths, so this is arithmetic, not a
  benchmark.

## Deliberately not doing

* **Colour that reacts to speed.** Android stretches the band when you move
  fast. That comes for free here: the band is a fixed length of *path*, so a
  fast sweep already lays it over more screen.
* **A shimmer on the resting stroke.** Colour means "moving". A stroke that
  keeps sparkling after the pointer has stopped says the opposite.
* **A setting.** It is lasso only, and the rectangle is untouched; anyone who
  dislikes it already has the rectangle.
