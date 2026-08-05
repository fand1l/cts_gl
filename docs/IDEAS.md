# Ideas

A second round, after `docs/POLISH.md` shipped and the program had a few real
weeks of use.  Same rule as last time: every item says **how I knew** it was
worth doing, because "it would be nice" is not a reason and the answer is
usually already sitting in the code or in a screenshot.

Ordered by what I think they are worth, not by how hard they are.

---

## 1. Black out part of the selection before it leaves

Drag rectangles inside the confirmed selection; they are filled solid before the
crop is prepared, and there is no way to get the unredacted version out.

*Why:* the whole program uploads a piece of your screen to Google.

*How I knew:* the confirmation step exists on the argument that "a selection is
easy to get slightly wrong and impossible to take back once it has been
uploaded" — and then it only lets you change the **bounds**, never the
**content**.  If a password manager, an email address or a token happens to sit
next to the thing you want to look up, the only options today are to reframe the
crop until it excludes them or to give up.  That is the same problem the confirm
step was written for, half-solved.

*How:* a `Redact` button in the action bar (or hold *R*) turns the next drags
into black rectangles instead of a new selection; they live in overlay state and
are painted over the crop in `_on_selected`, **before** `_deliver`.  Baked into
the image, not drawn on top of it, and into the copied and saved versions too —
one image, no path that produces the original.

*What to watch:* `_deliver` also writes the browser launcher page to disk with
the JPEG inlined, and it stays there for ten minutes
(`LAUNCHER_LIFETIME_MS`).  Redaction has to happen before `prepare_image`, or
the thing it was protecting is on disk in the clear.

## 2. Search the text, not the picture

When words are selected, offer *Search* alongside *Copy*.

*How I knew:* the text bar reads *Copy text (12) · All text · Back*.  There is
no Search — the one thing a person selecting a sentence on screen most obviously
wants.  And it needs nothing new: no upload, no image, no round trip.  A plain
`https://www.google.com/search?q=…` opened with the code that already opens the
result page, and it is *faster* than the image path because there is nothing to
send.

Its sibling costs one more line: if the selected text **is** a URL, the button
says *Open the link* instead.  Circling a link in a screenshot or a terminal and
having it open is worth a great deal on its own.

## 3. Capture after a delay

A tray item and a `--capture --after 5` flag: wait, then capture without an
overlay appearing first.

*How I knew:* this is the one thing the program cannot do at all.  The
screenshot is taken *before* the overlay appears, so a hover state does survive
— but a menu does not, because the shake needs the pointer to move and the
global shortcut cannot reach kglobalaccel through a menu's grab.  So circling an
open dropdown, a tooltip or a right-click menu is impossible today, and that is
the most ordinary thing anybody wants a screenshot tool for.

*How:* the countdown lives in the daemon, not the compositor: a `QTimer`, a tray
tick-down in the tooltip, then the ordinary capture path.  Nothing else changes.

## 4. `circle-to-search --doctor`

One command that checks every joint and prints what to do about each.

*How I knew:* the README has a long "what can break and how to debug it"
section, `install.sh` already ends with a verification pass, and the tray state
was built precisely because "the toggle does not work" cost two rounds of
debugging.  All three are the same admission: this program has six moving parts
in four processes, and when one of them is wrong the symptom is silence.

*What it checks:* the D-Bus name is on the bus; the KWin script is installed,
enabled in `kwinrc`, and **the version KWin is running matches the file**; which
screenshot back end actually works and how long it takes; whether `tesseract` is
there and which languages; whether anything handles `text/html`, because that is
what opens the result; whether kglobalaccel knows the action.  One line each,
and the fix next to the failure.

Most of the pieces exist already — `traystate.py`, `kwin_script_version()`,
`capture_screen`'s back-end loop, `ocr.is_available()` — it is mostly a matter
of printing them.

## 5. Pin the crop to the screen

Press *P* and the selection becomes a small always-on-top window you can drag
around and keep while you work.

*How I knew:* the commonest reason to capture something is to *look at it* while
typing somewhere else, and every one of the four actions today throws it away —
search sends it, copy hides it in the clipboard, save buries it in a folder,
Esc discards it.  Flameshot's pin is the feature people move to Flameshot for.

*How:* we already build frameless always-on-top windows and already hold the
cropped `QImage`; the pin is a `QLabel` in a frameless window with a drag
handler and Esc to close.  The KWin script already knows how to keep our windows
above panels.

## 6. A window for the captures, not a submenu

A grid of thumbnails with a search box that matches the text found in each.

*How I knew:* `history.py` keeps `LIMIT = 5` crops **and the OCR text of each
one alongside them** (`_text_path`).  So there is already a small searchable
corpus of everything you have looked up, and the only way to reach it is a tray
submenu that shows five items and cannot be searched.  The limit is five because
a menu of thirty would be unusable — which is a statement about the menu, not
about the right number.

With a window, the limit becomes a setting, and "what was that error message I
looked up last week" becomes answerable.

## 7. The loupe already knows the colour

Show the hex value under the magnifier, and a key to copy it.

*How I knew:* `_draw_loupe` reads pixels out of `self._sharp` at the pointer
every frame already.  The value is *right there*; it is thrown away after being
drawn.  Six lines of code turn the overlay into a screen colour picker, which on
Wayland is otherwise genuinely awkward — and it costs nothing when unused.

## 8. Say what will actually be sent

Next to `1046 × 750 px`, a second line: `→ 1000 × 717, ~112 KB JPEG`.

*How I knew:* `prepare_image(max_side, quality)` resizes and re-compresses every
capture, and both numbers are settings — *Longest side* and *JPEG quality*.
Nobody can evaluate either of them, because the readout on screen shows the crop
size and the crop size is not what leaves the machine.  Showing both makes two
otherwise meaningless spin boxes mean something.

Cheap: `prepare_image` already returns a `PreparedImage` with the size and the
bytes.  Run it on a scaled-down guess, or just compute the resized dimensions
and estimate.

## 9. The same area as last time

A key that re-selects the exact rectangle of the previous capture.

*How I knew:* `history.py` keeps the crops but not the **geometry** they came
from.  Anyone comparing a value that changes — a build log, a dashboard, a
download counter — currently redraws the same rectangle by hand every time and
gets it slightly different every time, which also makes the results
incomparable.  Storing four numbers next to the PNG fixes it.

## 10. Drag the crop out of the overlay

Press on the confirmed selection and drag it into another window: a chat, a
document, an image editor.

*How I knew:* copying to the clipboard already exists and this is the same data
by a different route — one `QDrag` with `image/png`.  The route matters, though:
dropping a picture into a chat is one gesture, while copy-then-switch-then-paste
is four, and the overlay is already holding the image.

*What to watch:* the inside of the box now starts a new selection, so this needs
its own affordance — most likely the move grip doing double duty, or a modifier.

## 11. "Try another way" when the upload fails

The failure notification gets a button that retries through a different variant.

*How I knew:* `lens.py` knows four upload variants and `--backend` exists to
choose between them from the command line — but at the moment it actually
matters, the notification says *Google Lens request failed* and offers nothing.
Meanwhile `notify.py` already has the whole action-button machinery, built for
the misfire survey, sitting unused for anything else.

## 12. Decode a QR code instead of uploading it

If the crop contains one, offer *Open the link* before offering to send it
anywhere.

*How I knew:* circling a QR code and sending it to Google is a network round
trip for something that decodes locally in about a millisecond, and it hands a
picture of the code to a third party on the way.

*What to watch:* it needs a decoder — `zbar` through `pyzbar`, or OpenCV.  That
is a real dependency for a narrow feature, so it belongs behind the same
treatment as the text recognition: optional, off unless the library is there, and
never a hard requirement.

## 13. Select without a mouse

*Space* starts a selection at the pointer, arrows size it, *Space* again
finishes.

*How I knew:* the arrow keys already move and resize a **confirmed** box, with a
caption in the bar advertising them — so half of this exists and the half that
is missing is the half that makes it usable without a mouse at all.

## 14. Annotations — probably not, and here is why

Arrows, boxes, text, a highlighter, before saving or sending.

Listing it because it is the obvious next request and the honest answer is *no*.
Lens does not want an arrow drawn on the picture — it wants the picture.  The
value of annotation is entirely on the *save* path, which makes it a second
program's worth of tools bolted to the side of this one for the sake of a
feature list.  If the pinned window (5) can be dragged into a real editor, the
editor does it better.

---

## Deliberately not doing

* **Scroll capture / long screenshots.**  It needs to synthesise scroll events
  into another client's window, which Wayland does not permit and should not.
* **Any Yandex or Russian service.**  Recorded at the top of `docs/ROADMAP.md`
  and not up for revisiting.
* **A second search engine that needs an API key.**  The whole design of this
  program is that it has no keys and makes no network connections of its own.
* **Cloud sync of the capture history.**  The captures are on the machine and
  that is a feature.
