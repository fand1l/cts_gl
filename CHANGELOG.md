# Changelog

Every release has a **number**, a **name** and a **build**.

* The **number** says what changed in relation to the last one.
* The **name** says *which* one it is — the question somebody about to run
  `./install.sh update` is actually asking.
* The **build** is five flat digits that only ever go up. It says nothing about
  what changed; it is the one thing a machine compares, and it exists because
  the number cannot answer "is this older than what I have" while `dev` and
  `deploy` sit on the same version for as long as the work takes.

All three are kept apart in the files and joined only for display —
`1.2.0 “better-version-control” (build 10000)`. `1.2.0-better-version-control`
is not a version pip will take (under PEP 440 a hyphen introduces a
*pre-release*, so that string sorts *below* `1.2.0`) and not one RPM will take
at all, because its `Version` field uses the hyphen to separate the version from
the release.

`./install.sh update` prints all of it, and what you were on before.

---

## 1.3.0 — “another-way”

* **A QR code wears its own button.** The whole screen is read as it freezes,
  and every code found gets a small pill on it saying where it goes — press it
  and you are there, with nothing uploaded and nothing to select first. Two
  codes get two buttons, because one button in the action bar can only say
  "Open the link" about whichever it decided to mean. *Enter* takes the only
  code when there is one. A code that is not an http link is copied instead,
  because handing an arbitrary `WIFI:` or `bitcoin:` URI to the browser on a
  press is not a thing to do on the strength of a colon. It needs `zbar`, which
  the installer now offers; unlike text recognition it is on by default and is
  not asked about first, because the trade is the other way round — it costs
  nothing and its whole effect is to stop an upload. Pressing one is immediate:
  no badge, nothing queued in front of it, and the overlay out of the way
  before the browser arrives rather than on top of it.
* **Select without a mouse.** The arrow keys raise a crosshair and move it —
  *Ctrl* to travel, *Shift* for one pixel at a time. *Space* pins one corner,
  the arrows size the box from it, *Space* again takes it, and from there it is
  the same waiting selection with the same keys on it. *Esc* lets go of the
  corner without letting go of the capture. Half of this existed: the arrows
  already moved a box that had been taken, and there was no way to take one.
* **“Try another way” when an upload fails.** The notification gets a button
  that sends the same picture by the *other* mechanism — if your browser was
  posting it, the daemon uploads; if the daemon was, your browser gets the
  page. They fail for unrelated reasons, so swapping them is a real second
  attempt, where retrying a different endpoint would not be: the automatic mode
  already walked all of them before it gave up. Offered once, and only when the
  notification server has buttons to press.

---

## 1.2.0 — “better-version-control”

What 1.1.0 started, finished: it gave releases names, this makes them
comparable, and quietens the installer that reports them.

* **A build number.** Five digits beside the version, up by one on every push.
  Deliberately not derived from the version: `dev` and `deploy` share a version
  number for as long as a release takes to write, which is exactly when
  something needs to know which of two copies is the later one.
* **An update cannot quietly go backwards.** `update` compares what it is about
  to install against what you have and stops if that is older — before anything
  is fetched over, moved or removed. Going back is still allowed, because
  retreating from a `dev` build that broke is the whole point of being able to,
  but it takes `--downgrade` and it **erases the settings**: the newer build
  wrote settings the older one has never heard of, and reading those back
  through older code fails in ways that look like a bug and are not. Captures
  and saved traces are kept, and it asks again before erasing anything.
* **`update` stops when there is nothing to do.** Nothing fetched *and* the
  version installed is the version here means the work is already done;
  stopping the daemon to put the identical thing back was a minute of churn to
  arrive where it started. It still reinstalls without asking when the two
  differ, or when any commit arrived. `--force` does it regardless.
* **The installer stopped shouting.** Five numbered steps and a tick each,
  instead of forty lines. What a step's commands print is held back and shown
  only if that step failed — its warnings are shown either way. The long
  "if nothing happens" list now appears only when the final check found
  something. `--debug` (or `--verbose`) prints all of it as before.
* **`circle-to-search --doctor`.** One command that checks every joint — the
  session, the daemon, its service, the KWin script *and whether KWin is
  running the copy that is installed*, the shortcut, the screen capture by
  really taking one, the browser, the text recognition — and prints the fix
  next to whichever line is wrong. Exit code 1 when something is broken.
* The build is visible everywhere the version is: `circle-to-search --version`,
  the tray's *About*, the daemon's first line in the journal, and both of
  `update`'s version lines.

`--dev`, from 1.1.0, is what makes all of that necessary: it is the one command
that installs code nobody has looked at yet.

KWin script **1.11.0** — unchanged, so nothing here needs a log-out.

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
