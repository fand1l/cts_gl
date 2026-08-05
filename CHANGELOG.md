# Changelog

Every release has a **number** and a **name**. The number says what changed in
relation to the last one; the name says *which* one it is, which is the question
somebody about to run `./install.sh update` is actually asking.

The two are kept apart in the files and joined only for display —
`1.1.0 “screenshot-window”`. `1.1.0-screenshot-window` is not a version pip will
take (under PEP 440 a hyphen introduces a *pre-release*, so that string sorts
*below* `1.1.0`) and not one RPM will take at all, because its `Version` field
uses the hyphen to separate the version from the release.

`./install.sh update` prints both, and what you were on before.

---

## 1.1.0 — “screenshot-window”

Seven things, all of them about what happens *after* the screen freezes.

* **Search the text, not a picture of it.** With a run of words selected, the
  primary action is now a plain web search for the string — the overlay has
  already read them, so nothing is uploaded to answer a question that is already
  answered. If what you picked out is a link, the button opens it instead.
* **The readout says what will actually be sent.** Under `1200 × 480 px`, a
  second line: `→ 1000 × 400, ~30 KB JPEG`. The dimensions are arithmetic and
  instant; the bytes are measured by really preparing the crop, off the GUI
  thread.
* **The same area as last time** (*R*), to the pixel — for watching a number
  that changes, where a rectangle redrawn by hand is never quite the same one.
* **Black out part of the selection** (*B*) before it leaves. Filled solid
  before the image is made, so no version of it with them still in ever exists —
  and the words underneath are dropped from the recognised text too.
* **A colour picker** (*K*). The screen is already frozen and already magnified
  under the pointer; click a pixel and its `#rrggbb` is in the clipboard.
* **Pin a crop to the screen** (*P*) and keep it there while you work, for when
  what you need to read is behind the window you have to type into.
* **A window for the kept captures**, with a box that searches the text
  recognised inside them. How many to keep became a setting; five was a
  judgement about a tray menu.

Also: `./install.sh update` — fetch the `deploy` branch and reinstall, in the
order that cannot leave you worse off, with `--dev` to take the `dev` branch
instead when a change needs testing before it is merged. And a repaint bug fixed
that smeared the handles, the grip and the action bar across the screen when the
selection was moved.

**A crash fixed** that was in 1.0.0 from the start: *About* and the one-time
text-recognition question each opened a message box that blocked everything
until it was answered, and that could freeze the daemon and then take it down
with a SIGSEGV on the way out. Both are ordinary windows now — the tray keeps
working while they are open, and neither can be destroyed while it is still on
screen, which is what the segfault was.

KWin script **1.11.0**. Log out and back in after updating, or the pinned
windows will not be kept above the rest.

## 1.0.0

The first one. Shake detection in a KWin script, capture through
ScreenShot2 / Spectacle / the portal, the selection overlay, and the Google Lens
upload done by the browser rather than by the daemon.

Everything in `docs/POLISH.md` shipped in it: the calibration wizard, learning
from real misfires, confirm-before-sending, optional local text recognition, an
overlay on every screen, recent captures in the tray, a tray icon that says why
shaking will not work, the magnifier, and the Android-style stroke.
