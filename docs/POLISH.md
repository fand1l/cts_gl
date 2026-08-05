# Polish

Small things, agreed after the first real week of use.  Not new capabilities —
the same program, less friction.  Order is roughly cheapest first; the last one
is big enough to deserve its own day.

Each item says what convinced me it was needed, because "it would be nice" is
not a reason and the answer is usually already visible in a screenshot or in the
code.

---

## 1. An action bar instead of a line of key names  ← next

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

## 2. The Android stroke

A wide white lasso line with colour only where the pointer is moving.  Designed
in `docs/STROKE.md` — it replaces the plain contrast outline that was originally
proposed here, and folds that outline in as the dark under-stroke.

## 3. Say that the sending is happening

Keep the overlay up with a small badge until the launcher page is on disk,
instead of vanishing into a second of nothing.

*How I knew:* the user's own argument about OCR — "it does not happen instantly,
so there has to be a loading animation or people think it has frozen" — applies
here unchanged.  `_on_selected` calls `_release_overlay()` *before* `_UploadTask`
has even started.

Reuses the scanning badge, which already repaints only its own rectangle.  Plus
a five-second dead-man's switch, so a failure elsewhere can never leave the
overlay up for good.

## 4. A magnifier while dragging  — **with a setting**

A small 4× loupe with a crosshair beside the pointer, while a drag or a handle
is being moved.  Off by default is wrong for a thing this useful, but it is a
matter of taste, so: `magnifier` in the application settings, on.

*How I knew:* arrow-key nudging exists because precision was missing — but it
only helps *after* the miss.  The placement logic is already written in
`_draw_size_label`, which flips the label when there is no room.

## 5. A tray icon that shows the state

Dimmed icon and a live tooltip when shake detection is off.

*How I knew:* from our own debugging.  "The toggle does not work" came up twice
and cost two rounds; the cause was KWin still running the old script.  An icon
that showed the real state would have said so immediately.  Read the state
fresh — the whole bug was a disagreement between what was written and what was
running.

## 6. The numbers behind "Advanced"

Collapse the ten threshold spin boxes into a closed `QGroupBox`; leave the
switches and the calibration button in the open.

*How I knew:* the calibration was written on the argument that tuning six
thresholds by hand is the wrong job for a user, and then those six thresholds
were left as the most prominent thing in the window.

## 7. Show the gesture instead of describing it

A small panel in the welcome window and beside the calibration button where a
dot travels the exact path the detector wants.

*How I knew:* the welcome window explains a physical movement in three lines of
prose, and the README has a placeholder for a screenshot of it that does not
exist.  Both are the same admission.

Built from `read_detection()`: `minAmplitudePx` sets the swing, `minSpeedPxPerSec`
the speed, `reversals` the count.  So it is not a drawing of the gesture, it is
*the current settings*, animated — after a calibration it shows your own
gesture, and absurd thresholds are visible as absurd movement.

## 8. Highlight the window under the pointer

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
