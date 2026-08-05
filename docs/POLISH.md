# Polish

Small things, agreed after the first real week of use.  Not new capabilities —
the same program, less friction.  Order is roughly cheapest first; the last one
is big enough to deserve its own day.

Each item says what convinced me it was needed, because "it would be nice" is
not a reason and the answer is usually already visible in a screenshot or in the
code.

---

## 1. An action bar instead of a line of key names  ← done

A floating pill under the selection with real buttons — search, copy, save,
close — each carrying its shortcut as a small second label, plus a chip that
swaps lasso and rectangle.

*Why:* the whole gesture is mouse work, and then it demands the keyboard.
*How I knew:* the bottom of the overlay currently reads
"Enter — пошук · C — копіювати · S — зберегти · Esc — скасувати | тягніть за
краї…", which is documentation, not an interface, and `mousePressEvent` has no
button hit-testing at all — there is nothing to click.

Painted, not child widgets: real widgets would swallow the events the drag needs.
Rectangles computed when the selection changes, hit-tested *before* the resize
handles and the text layer, hover repainting only the bar's own rectangle.
`_commit(action)` already exists as the single entry point, so the buttons map
straight onto it.

**As built.**  One bar with three faces, because the states never overlap: the
mode chips before anything is drawn, search/copy/save/cancel while a selection
waits, copy/all/back over selected text.  Three things came out of doing it
that were not in the plan:

* The three prose hints all disappeared into it.  The mode chips took the
  opening hint, the selected-word count went *onto* the copy button rather than
  into a sentence beside it, and only the arrow keys were left — the one thing
  no button can announce — so the pill grew a caption row for them.
* The pixel readout had to stop following the pointer.  It is anchored above
  the box now: once the drag is over the pointer wanders, and a readout that
  follows it sat straight on top of the bar.
* The mode chip is a *setting*, not a per-capture switch.  It writes
  `selection_mode` and tells the other screens, because *Shift* is already the
  way to change it for exactly one drag.

## 2. Say that the sending is happening  ← done

Keep the overlay up with a small badge until the launcher page is on disk,
instead of vanishing into a second of nothing.

*How I knew:* the user's own argument about OCR — "it does not happen instantly,
so there has to be a loading animation or people think it has frozen" — applies
here unchanged.  `_on_selected` calls `_release_overlay()` *before* `_UploadTask`
has even started.

Reuses the scanning badge, which already repaints only its own rectangle.  Plus
a five-second dead-man's switch, so a failure elsewhere can never leave the
overlay up for good.

**As built.**  One badge with two messages, because the screen is never being
read and sent at the same time.  Four things worth writing down:

* The selection stays *drawn* under it, undimmed.  "This is what is on its way"
  is more use than an empty frozen screen, so what goes is only the things that
  invite another click: the bar, the handles and the pixel readout.
* Every way out is covered.  The five-second timer is the last resort; the
  ordinary ends are the launcher landing on disk, the browser taking the focus,
  and any key or click — a progress note is not a question.
* `_busy` is cleared the moment the selection leaves, badge or no badge.  A
  courtesy must never be able to lock the program out of the next capture.
* Copying and saving deliberately do not get one.  They are finished by the time
  the overlay would have closed, and a badge for them would be claiming to wait
  for something that has already happened.

## 3. A magnifier while dragging  — **with a setting**  ← done

A small 4× loupe with a crosshair beside the pointer, while a drag or a handle
is being moved.  Off by default is wrong for a thing this useful, but it is a
matter of taste, so: `magnifier` in the application settings, on.

*How I knew:* arrow-key nudging exists because precision was missing — but it
only helps *after* the miss.  The placement logic is already written in
`_draw_size_label`, which flips the label when there is no room.

**As built.**  A circle rather than a square, cut from the screenshot already in
memory, at nearest-neighbour so the pixels are pixels.  Three decisions worth
recording:

* **Only while something is being aimed**, never on hover.  A magnifier that
  follows the pointer around a frozen screen is the cursor glow again, and what
  is being solved here is putting an *edge* in the right place.
* **Near the edges of the screen the source is not clamped.**  Clamping would
  slide the magnified image sideways exactly where careful aiming happens, so
  the part that exists is drawn where it belongs and the rest stays black.
* **The pixel readout gave way to it.**  Both wanted the space below and right
  of the pointer; the loupe is the one that has to be there.

## 4. A tray icon that shows the state  ← done

Dimmed icon and a live tooltip when shake detection is off.

*How I knew:* from our own debugging.  "The toggle does not work" came up twice
and cost two rounds; the cause was KWin still running the old script.  An icon
that showed the real state would have said so immediately.  Read the state
fresh — the whole bug was a disagreement between what was written and what was
running.

**As built.**  Five states, not two, because "it does nothing" has four
different causes and shaking harder tells them apart from none of them:
working, switched off, no script, script not enabled, and *script running an
older copy than the one on disk*.

That last one needed something new: the script announces its own version over
D-Bus (`ScriptReady`) when it loads and again on every configuration change,
and the daemon compares it against the version it reads out of the installed
`main.js`.  Two rules keep it honest:

* **Silence proves nothing.**  A daemon restarted mid-session has never heard
  from the script, and that is not a fault.  The version is only ever used to
  *contradict* the file on disk.
* **Changing any setting brings the answer.**  It makes KWin reconfigure, which
  makes the running script re-read and re-announce — and it is the first thing
  anyone does when a toggle appears to do nothing.

Read fresh on every menu opening.  Anything cached would be capable of exactly
the disagreement it exists to expose.

## 5. The numbers behind "Advanced"  ← done

Collapse the ten threshold spin boxes into a closed `QGroupBox`; leave the
switches and the calibration button in the open.

*How I knew:* the calibration was written on the argument that tuning six
thresholds by hand is the wrong job for a user, and then those six thresholds
were left as the most prominent thing in the window.

**As built.**  Hidden rather than greyed out — a checkable `QGroupBox` disables
its contents by default, and ten disabled spin boxes say "you may not change
this", which is untrue; the box is simply shut.  Its margins collapse with it,
so a closed section is a title and not a title over an empty frame.

One Qt trap, worth the comment it got in the code: resizing the window down
towards `minimumSizeHint()` after opening the section lays the word-wrapped
hint labels on top of the spin boxes, because `heightForWidth` is not part of
that number.  So it only ever grows.

## 6. Show the gesture instead of describing it  ← done

A small panel in the welcome window and beside the calibration button where a
dot travels the exact path the detector wants.

*How I knew:* the welcome window explains a physical movement in three lines of
prose, and the README has a placeholder for a screenshot of it that does not
exist.  Both are the same admission.

Built from `read_detection()`: `minAmplitudePx` sets the swing, `minSpeedPxPerSec`
the speed, `reversals` the count.  So it is not a drawing of the gesture, it is
*the current settings*, animated — after a calibration it shows your own
gesture, and absurd thresholds are visible as absurd movement.

**As built.**  A fifth setting joined in, and it settled the one real design
question.  The strokes have to be separated or the animation is a dot sliding
along a single line segment — but any separation invented for the drawing would
have distorted the two things the detector actually measures.  Taking the lean
out of `angleTolerance` instead means every stroke is exactly `minAmplitudePx`
long *and* within the accepted angle: the picture is of a gesture that would
really be accepted, which is the whole claim.

The spin boxes are wired straight into it, so a threshold set to something
absurd is absurd movement before the dialog is even closed.  And the panel says
one thing the numbers cannot: when the strokes would not fit inside `windowMs`
at the minimum speed.  In amber, not red — it is not impossible, it means you
will have to shake faster than the threshold, and those two numbers look
independent until something says otherwise.

## 7. Highlight the window under the pointer  ← next

Before a drag starts, outline the window the pointer is over; a click without a
drag captures exactly it.

*How I knew:* the data is already on the bus — the KWin script walks
`workspace.windowList()` in `checkForOverlay()` and already reports geometry.
And the screenshot of a lasso drawn laboriously around a rectangular panel is
the case this removes.

`WindowRects` from the script in the same encoding as the movement trace
(`x,y,w,h;…`, global logical pixels, stacking order, excluding our own overlay),
sent with the trigger.  Known rough edges to handle: windows partly off-screen,
and KWin's shadows not being part of `frameGeometry`.

## 8. The Android stroke  — last, and on its own

A wide white lasso line with a coloured glow at the pointer.  Designed in
`docs/STROKE.md`, from two photographs of the real thing — which corrected the
first draft: the line carries no colour at all, the colour is a glow under its
head, and it stretches because it is a fading trail of the last few positions
rather than a shape computed from velocity.

It also folds in the dark outline originally proposed here, as the shadow that
keeps a white line visible on a white page.

Deliberately at the end and by itself.  It is the only item here that replaces
something already working rather than adding to it, it is the only one with an
animation timer, and the last thing wearing that description had to be taken
back out again.  So: after everything else is in and settled, on its own branch
of work, with its own round of testing on real hardware.
