# Roadmap

Agreed with the user, in the order they are being built.  Each item is done on
its own, with tests, and committed separately.

**Not in scope, ever:** Russian services (Yandex and anything of the kind).  If
an alternative search back end is ever added it will not be one of those.

---

## 1. Gesture calibration wizard  ✅

Tuning six thresholds by hand is the wrong job for a user.  Instead: "shake the
way that feels natural, a few times", measure it, and write thresholds with a
margin.

* KWin script gains a `calibrating` mode: it never fires the overlay, and
  reports the raw measurements of every swing it sees
  (`CalibrationSample(length, speed, curvature, diagonal, turn, duration)`).
* `calibration.py` turns a list of samples into a `DetectionSettings` — a pure
  function, so the arithmetic is unit-testable without Qt or KWin.
* A dialog collects the samples live, shows what it will change, and applies.

## 2. Learning from real misfires  ✅

* After a trigger, occasionally ask "did you mean to open this?" — at most
  **10 times in total**, and no more often than **every 5th opening**, so it
  never becomes nagging.  Switchable off.
* A "no" answer saves the cursor trace that caused it, so it can be replayed
  through `tests/replay-trace.js` and turned into a regression case.
* Ship a corpus of recorded traces in `tests/traces/` and replay them all in
  the harness.

Done, plus one thing found on the way: `Notify` was being called with plain
Python values, which marshal as `sisssava{sv}i` instead of the declared
`susssasa{sv}i`, so a strict notification server rejected every notification the
application has ever sent.  `tests/test_dbus_surface.py` now drives the real
D-Bus surface on a private bus so that class of failure cannot be silent again.

## 3. Confirm and adjust before uploading  ✅

* The selection is not sent the instant the button is released.  `Enter`
  searches, `C` copies, `S` saves to a file, `Esc` cancels, and the rectangle
  can be nudged by its edges first.

Eight drag handles, arrow-key nudging (`Ctrl` for 10 px, `Shift` to stretch),
and a press outside the box to start again.  Adjusting the box of a lasso drops
the loop outline rather than masking against a shape that no longer fits.  On by
default; `confirm_selection` turns it off.

## 4. OCR (optional, off by default)  ✅

* Copy the text out of the selection instead of searching for it.  Asked about
  on first use, never enabled behind the user's back, and the dependency is
  optional — the application must work exactly as now without it.

Done with the `tesseract` binary rather than a Python OCR package: no new
dependency, nothing uploaded, no key.  `T` in the overlay; `Suggests:` in the
spec file, so it is never pulled in by default.

## 5. Overlay on every screen at once  ← next

* A selection that spans two monitors.  A setting, off by default.

## 6. Recent captures in the tray

* The last five crops: search again, copy, or save without redoing the gesture.

## 7. "Capture now" through kglobalaccel

* `invokeShortcut` on the KWin script's own shortcut, so the pointer position
  comes from the compositor instead of Qt's guess (which Wayland does not
  answer honestly).

## 8. Restore focus after cancelling

* Give the keyboard back to the window that had it.  With a switch.

## 9. Halve the overlay's memory

* Two full-resolution pixmaps (plain and dimmed) are ~66 MB on a 4K screen.
  Paint the dimming instead of keeping a second copy.

## 10. First-run experience

* One notification explaining the gesture, the few settings worth choosing up
  front, and an offer to run the calibration from item 1.

## 11. CI

* GitHub Actions: `ruff`, the three test suites, and an RPM build on every
  push.
