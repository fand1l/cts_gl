# Ideas

A second round, after `docs/POLISH.md` shipped and the program had a few real
weeks of use.  Same rule as last time: every item says **how I knew** it was
worth doing, because "it would be nice" is not a reason and the answer is
usually already sitting in the code or in a screenshot.

Ordered by what I think they are worth, not by how hard they are.

**Where each stands.**  Fourteen were read through and decided; 15 was added
afterwards as a placeholder and now has an answer.

| | |
|---|---|
| done | **2 search the text** · **8 what will be sent** · **9 the same area** · **1 redact** · **7 colour** · **5 pin** · **6 history window** · **4 `--doctor`** · **11 try another way** · **13 no-mouse** |
| agreed, in this order | 12 QR · 10 drag out — see the note under it |
| last, on its own | 15 a new design, on Material 3 |
| dropped | 3 delay · 14 annotations |

The four that were left unordered were ordered on the same rule as before.  11
went first and is done: it was wiring between three things that already existed
and added no interface at all.  13 and 10 both change what a press does to a
confirmed selection, so they were put adjacent, and 13 went first because half
of it was already there.  10 has since moved *behind* 12: starting on it turned
up a question the sketch had not asked — there is nowhere to drop a picture
while a fullscreen overlay is on top of every window — and the answer decides
what the feature is.  12 needs a decoder that is not installed, which makes it
the only one carrying a dependency, the settings to make it optional, and three
files of documentation, but none of that is a question.  15 comes after all of them
because it repaints whatever they leave behind.

The order is not the ranking below.  It runs smallest-change-per-return first
and keeps anything that touches the same code adjacent, so each one lands on a
tree the last one already tidied: 8 and 9 both live on the confirmed selection,
1 and 7 both add a mode to the overlay, 5 and 6 are both new windows, and 4
reads what all of them have written.

---

## 1. Black out part of the selection before it leaves — **done**

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

*How:* a `Redact` button in the action bar turns the next drags into black
rectangles instead of a new selection; they live in overlay state and are painted
over the crop in `_on_selected`, **before** `_deliver`.  Baked into the image,
not drawn on top of it, and into the copied and saved versions too — one image,
no path that produces the original.

Not *R*, as this said before it was written: **R** now takes the same area as
last time (idea 9), and that one is on screen at the same moment.

*What to watch:* `_deliver` also writes the browser launcher page to disk with
the JPEG inlined, and it stays there for ten minutes
(`LAUNCHER_LIFETIME_MS`).  Redaction has to happen before `prepare_image`, or
the thing it was protecting is on disk in the clear.

*What shipped:* that, plus two leaks the sketch had not thought of.

* **The recognised words go too.**  Whatever was read inside a crop is kept
  beside it in a `.txt` so the tray's *Read the text* is instant — so a word the
  black rectangle so much as clips is now dropped (`ocr.words_outside`).
  Otherwise the thing that was covered in the picture would be sitting next to
  it in plain UTF-8.
* **The byte count had to be measured on the redacted crop.**  Solid black
  compresses to nearly nothing, so idea 8's readout would have been about a
  different image.  That in turn made the estimate need a serial as well as a
  crop rectangle: covering something does not move the box, so "the same
  rectangle" stopped being enough to tell a fresh answer from a stale one.

## 2. Search the text, not the picture — **done**

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

*What shipped:* `websearch.py` — two pure functions, no Qt — plus a
`BAR_TEXT_SEARCH` button that leads the text bar, so **Enter** searches and
**C** copies, exactly as they do on the area bar underneath.  Two things came
out of writing it that the sketch above had not thought about:

* the URL guess had to be made **conservative on purpose**, because opening
  something nobody asked for is far worse than making them paste it themselves.
  A bare dotted word is more often a file than a host, and a terminal full of
  `main.py` and `notes.txt` is exactly where people circle things, so a
  blocklist of file extensions sits in front of the bare-domain case;
* the badge could not say *Sending it to Google Lens*, because nothing is sent.
  It got its own line, `overlay.opening`, and the overlay stays up until the
  browser takes the focus — the wait here is the browser's cold start, which is
  real, and it is the only wait left.

## 3. Capture after a delay  — **the reason I gave was wrong**

A tray item and a `--capture --after 5` flag: wait, then capture without an
overlay appearing first.

*What I claimed:* that an open menu cannot be captured.  **That is false**, and
the user was right to push back — they tried it and menus come through fine.

*Why it is false:* the screenshot is taken *before* the overlay is built
(`app.py`: capture, then construct, then show), so whatever is on screen at that
instant is in the image.  And on Wayland a global shortcut is handled by the
compositor before the client ever sees the key, so `Meta+Shift+L` reaches
kglobalaccel straight through a popup's grab and nothing closes the popup.
Shaking works too — moving the pointer over a menu does not dismiss it.

*What is actually left,* and it is narrower:

* **Anything that needs the mouse button held down.**  A drag preview, the value
  tooltip on a slider being dragged, a press-and-hold menu.  You cannot press
  the shortcut mid-drag and shaking means letting go.
* **Tooltips.**  They die on pointer movement, so the shake is out; the shortcut
  works only if you can reach it without moving the mouse.
* **Anything on somebody else's schedule** — a toast, a progress dialog that
  finishes, one frame of an animation — where you want to be *ready* rather than
  reacting.

That is a real but much smaller feature than I sold.  Worth doing cheaply if at
all: a `QTimer` in the daemon and a countdown in the tray tooltip; the capture
path itself does not change.

## 4. `circle-to-search --doctor` — **done**

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

## 5. Pin the crop to the screen — **done**

Press *P* and the selection becomes a small always-on-top window you can drag
around and keep while you work.

*How I knew:* the commonest reason to capture something is to *look at it* while
typing somewhere else, and every one of the four actions today throws it away —
search sends it, copy hides it in the clipboard, save buries it in a folder,
Esc discards it.  Flameshot's pin is the feature people move to Flameshot for.

*What it does, concretely.*  You shake, you circle an error message.  Instead of
*Search*, *Copy*, *Save*, you press *P*.  The overlay disappears and in its place
a small borderless window is left holding exactly that crop, at its real size,
**on top of every other window**.  You drag it wherever it does not get in the
way with the mouse.  Then you go back to work: switch windows, type, scroll — it
stays floating above all of it until you press Esc on it or middle-click it.

*The case it is for.*  The thing you need to read is in window A, and the place
you have to type it is window B, and B covers A.  Today that is alt-tab, forget,
alt-tab, forget.  Pinned, both are on screen at once and you type it in one go.
Phone numbers, error codes, a diagram you are copying, a value from a dashboard.

*How:* we already build frameless always-on-top windows — that is what the
overlay is — and we already hold the cropped `QImage` in `_deliver`.  The pin is
a `QLabel` in a frameless `WindowStaysOnTopHint` window, a press-and-drag handler
to move it, and Esc to close.  The KWin script already knows how to keep our own
windows above the panels.  Worth adding while it is there: scroll to zoom, and
*Ctrl+C* on a pinned window to copy it, so a pin can turn into the other actions
without being recaptured.

*What shipped:* that, and the "press-and-drag handler to move it" turned out to
be the one line of the sketch that cannot be written.  **A Wayland client cannot
place its own window** — `move()` does nothing — so the drag is handed to the
compositor with `QWindow.startSystemMove()` and the pin is *born* wherever KWin
decides.  The KWin script's `promote()` was no use either: it forces
`fullScreen` and steals the focus, which is exactly right for the overlay and
exactly wrong for a pin, so pins get `floatPin()` and a caption of their own.

Three more things came out of using it rather than designing it:

* it opens **without taking the focus**, because pinning is not a request to
  stop typing where you were typing;
* a screen-sized crop is **scaled to at most 80 %** of the screen it lands on,
  or a pinned full-screen selection is a second copy of the screen on top of
  the first;
* it needs a **two-tone rim**, for the same reason everything else drawn over
  somebody else's screen does: a white crop pinned on a white background has no
  shape at all.

## 6. A window for the captures, not a submenu — **done**

A grid of thumbnails with a search box that matches the text found in each.

*How I knew:* `history.py` keeps `LIMIT = 5` crops **and the OCR text of each
one alongside them** (`_text_path`).  So there is already a small searchable
corpus of everything you have looked up, and the only way to reach it is a tray
submenu that shows five items and cannot be searched.  The limit is five because
a menu of thirty would be unusable — which is a statement about the menu, not
about the right number.

With a window, the limit becomes a setting, and "what was that error message I
looked up last week" becomes answerable.

*What shipped:* that, as a `QListWidget` in icon mode — a reflowing grid,
keyboard navigation, selection and scrolling all arrive with it, and each is
something a hand-rolled tile would get subtly wrong.  Three things the sketch
did not contain:

* **every thumbnail is padded onto one fixed tile size.**  With icons at their
  own shapes, a wide crop leaves room for two lines of caption and a tall one
  leaves room for none, so the captions land at different heights and the elided
  ones lose the size.  Found by looking at the first screenshot of it;
* the searchable text is read **once, when the window is filled**.  Going back to
  disk on every keystroke would make typing in that box feel like the disk;
* a tile the search hides is **deselected**, or the buttons act on something the
  box says is not there.

## 7. The loupe already knows the colour — **done**

A third chip beside *Lasso* and *Rectangle*: **Colour**.  Pick it and the overlay
stops being a selection tool and becomes a screen colour picker.

*How I knew:* `_draw_loupe` magnifies the frozen screenshot around the pointer
every frame already.  The pixel under the crosshair has a colour and it is thrown
away after being drawn.  On Wayland there is no ordinary way to read a pixel off
the screen — every client is blind to every other — so a program that has already
frozen the screen and is already magnifying it is in an unusually good position
to answer the question.

*How, exactly:*

* **Reading the pixel** is the one part that needs care.  `self._sharp` is a
  `QPixmap`, and `toImage()` on a 4K one every frame would be as bad as anything
  I have just spent two rounds removing.  So copy *one pixel*:
  `self._sharp.copy(QRect(x, y, 1, 1)).toImage().pixelColor(0, 0)` — a few bytes,
  not thirty-three megabytes.  Coordinates go through the same
  `logical_rect_to_physical` the crop uses, so it reads the real pixel and not an
  interpolated one.
* **Showing it**: the loupe grows a strip along its bottom edge with a swatch and
  `#RRGGBB`.  The crosshair already marks which pixel is meant.
* **Taking it**: click, or Enter.  The hex goes to the clipboard, the overlay
  closes, the notification says what was copied and shows the colour.
* **The mode is why it is cheap.**  The loupe deliberately does not follow the
  pointer on hover — that was the cursor-glow mistake, and I turned it down once
  already.  In colour mode it has to, and that is fine, because the mode was
  asked for: nobody pays for it who did not choose it.  It repaints only the
  loupe's own rectangle, which the damage-region work now makes straightforward.

Worth having on the same chip: *Shift* while picking copies `rgb(…)` instead, for
CSS.

*What shipped:* all of that, and two decisions the sketch had not made.

* **Choosing it does not stick**, unlike the two shapes beside it.  Lasso and
  rectangle are two ways to do the same job and are worth remembering; picking a
  colour is a different job, and nobody wants yesterday's colour pick to be what
  happens when they shake the mouse today.
* **Nothing is dimmed while picking.**  Asking what colour something is, over a
  wash, would be answering about a different picture than the one being looked
  at — the same argument the readout in idea 8 rests on.

One thing worth writing down for next time: the label over the swatch picks
black or white by *linear* luminance (`colours.readable_on`), not by the cheap
weighted sum of the stored channels.  The shortcut is wrong exactly where it is
most visible — a mid green like `#00c800` comes out "dark" on it and gets white
text, when black is four times as readable on it.

*But Plasma already has one.*  True — there is a `plasma-colorpicker` widget and
Spectacle can take a colour, and that is worth being honest about: this is not a
new capability for the desktop.  What is different is where it sits.  Those want
to be summoned first and then aimed at a live screen; here the screen is already
frozen and already magnified under the pointer, so by the time the question
"what colour is that" occurs to you, the answer is on screen and being thrown
away.  It is not a new tool, it is a value that already exists being kept.

## 8. Say what will actually be sent — **done**

Next to `1046 × 750 px`, a second line: `→ 1000 × 717, ~112 KB JPEG`.

*How I knew:* `prepare_image(max_side, quality)` resizes and re-compresses every
capture, and both numbers are settings — *Longest side* and *JPEG quality*.
Nobody can evaluate either of them, because the readout on screen shows the crop
size and the crop size is not what leaves the machine.  Showing both makes two
otherwise meaningless spin boxes mean something.

Cheap: `prepare_image` already returns a `PreparedImage` with the size and the
bytes.  Run it on a scaled-down guess, or just compute the resized dimensions
and estimate.

*What shipped:* both, and separately, which the sketch above had not seen.  The
dimensions are arithmetic (`imageops.scaled_size`, the same function
`prepare_image` calls) and appear the instant the selection settles.  The bytes
are **measured, not estimated** — a guess from pixels and quality is out by a
factor of three between a photograph and a page of text, and a made-up number
would defeat the point — so a worker thread really prepares the crop and the
answer comes back tagged with the crop it is about.  Two consequences:

* it is asked for 250 ms after the box last moves, and the count vanishes the
  moment it moves again.  A stale weight under a box of a different size is
  worse than no weight;
* with `all_screens` on there is no second line at all.  The crop there is
  stitched from several screenshots at the highest scale involved, and a number
  that was nearly right would be worse than the honest absence of one.

## 9. The same area as last time — **done**

A key that re-selects the exact rectangle of the previous capture.

*How I knew:* `history.py` keeps the crops but not the **geometry** they came
from.  Anyone comparing a value that changes — a build log, a dashboard, a
download counter — currently redraws the same rectangle by hand every time and
gets it slightly different every time, which also makes the results
incomparable.  Storing four numbers next to the PNG fixes it.

*What shipped:* four numbers and a screen name, but in the **settings file**
rather than beside the kept crops (`lastarea.py`).  Offering the rectangle again
is useful whether or not the pictures are being kept, and it turned out to be
worth being careful about which screen it came from — a rectangle from the other
monitor would select somewhere arbitrary, so it is only offered back to the
screen it was taken on, clamped to that screen as it is *now*, and dropped
outright when nothing of it is left there.  It appears as a third chip beside
*Lasso* and *Rectangle* — the one moment it is wanted is before a new rectangle
has been drawn over the old one by hand — and on **R**.

## 10. Drag the crop out of the overlay

Press on the confirmed selection and drag it into another window: a chat, a
document, an image editor.

*How I knew:* copying to the clipboard already exists and this is the same data
by a different route — one `QDrag` with `image/png`.  The route matters, though:
dropping a picture into a chat is one gesture, while copy-then-switch-then-paste
is four, and the overlay is already holding the image.

*What to watch:* the inside of the box now starts a new selection, so this needs
its own affordance — most likely the move grip doing double duty, or a modifier.
`Ctrl` and a press inside the box is the one going spare, and the confirm bar's
caption is where it would be advertised.

*And the thing that turns out to be in the middle of it,* found while starting
on it and worth writing down before the design is settled:

**The overlay is fullscreen, so there is nowhere to drop.**  A `QDrag` has to be
started while the button is still down, which is while the overlay is still up
— and the overlay covers every window the picture could be dropped into.  The
drop would land on the overlay itself.

That is not a detail to be discovered halfway through; it decides the shape of
the feature.  Three ways out, none free:

* **Hide the overlay and then `exec()` the drag.**  Qt allows it — the drag
  manager holds the grab, not the widget — and it is the smallest change.  But
  it is a fullscreen Wayland surface dropping its own grab mid-gesture, which
  is precisely the kind of thing that works on one compositor and not another,
  and it cannot be tested from here at all: `offscreen` has no drag-and-drop.
* **Drag out of the pinned window instead** (idea 5, already shipped).  A pin is
  a small ordinary window with the crop in it and nothing underneath it; a drag
  from there is an ordinary drag with an ordinary drop target, and *P* then
  drag is two gestures rather than four.  Cheapest and safest, and it moves the
  feature to where it is not fighting the compositor.
* **Both**, with the pinned window first.

Not started, deliberately: the first option is a guess that only real hardware
can settle, and the second may make the first unnecessary.  Worth ten minutes of
conversation before it is worth an afternoon of code.

## 11. "Try another way" when the upload fails — **done**

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

## 13. Select without a mouse — **done**

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

## 15. A new design, on Material 3 — **decided, last**

The placeholder that was here has an answer now: **Material Design 3**.

*How I knew:* it was picked rather than deduced, and the reason given was that
this program is already tied to Google — it opens Google Lens, in a gesture
Google shipped on Android — so Google's own interface rules are the ones with a
claim on it.  That is a better reason than it sounds: the gesture this imitates
is *Circle to Search*, which on a phone is drawn in MD3, and somebody who knows
what that looks like already has an expectation this can either meet or not.

**The specification is the source; the skill is a convenience.**  The rules to
follow are the ones at <https://m3.material.io/> — that is what "on Material 3"
means here.  The `material-3` skill installed alongside is a condensed reference
to reach for, not the authority: where the two differ, or where the skill is
silent, the spec decides.  It is also Compose-shaped in places the spec is not,
which is the next paragraph's problem.

**What actually transfers, and what does not.**  The MD3 reference is
Compose-first: Jetpack Compose is the primary target, Flutter second, and the
web components are explicitly in maintenance mode.  This is PyQt6 on Plasma, so
**none of the three implementation targets apply** and not one line of the
example code will move across.  What moves is the part that is platform-neutral
— which is also the part the spec states directly, independently of any
implementation:

| transfers | does not |
|---|---|
| the colour roles, and the rule that colours only pair as `X` + `on-X` | `MaterialTheme`, `@material/web`, every `md-*` element |
| the type scale — display / headline / title / body / label | Roboto as the typeface (see below) |
| the shape corners, as one small set of radii instead of the seven this uses now | `MaterialTheme.shapes` |
| elevation as *tone* rather than shadow | the dp table, mostly (see below) |
| the motion easings and durations, which are plain cubic-béziers | spring physics; Qt has no equivalent worth faking |
| the 8 dp spacing grid | adaptive scaffolds, window size classes — there is one window and it is the screen |

**Three places where MD3 and this program actually disagree**, and each needs an
answer before any of it is drawn:

* **Dynamic colour is already done, differently.** MD3 generates a scheme from
  the wallpaper; `_accent` already comes from the Qt palette's Highlight, which
  is the KDE accent colour the user chose.  Same idea, and the Plasma answer is
  the better one here — it agrees with the rest of their desktop.  So: take the
  MD3 *roles*, keep the KDE *seed*.
* **Tonal elevation needs a surface, and there is not one.**  MD3 replaces
  shadows with tinted surfaces — but everything this program draws sits on a
  frozen screenshot of somebody else's screen, which is any colour at all.  The
  dimming layer is the only surface there is.  Either the dimming becomes the
  MD3 surface and everything above it is toned against that, or the elevation
  system does not apply and shadows stay.  The first is more interesting and
  more work.
* **Roboto is the MD3 typeface, and using it would be wrong here.**  A KDE
  application that overrides the font the user set in System Settings is a
  badly behaved KDE application.  Take the *scale* — five roles, three sizes —
  and apply it to whatever `QFont` the desktop hands over.

**Where the work actually lands.**  The two plain dialogs are where MD3 would be
most visible and most of the effort, because they are currently whatever the
platform style gives; the overlay is already a designed thing and would be
re-tuned rather than rebuilt.  The table below is unchanged and is still the
inventory.

Everything under it still holds — in particular the three constraints, which
MD3 does not override.

**What a redesign would be touching.**  The look is not in a stylesheet — there
is no QSS in the project — so every one of these is a decision already made in
code, and each is somewhere different:

| | where | what it is now |
|---|---|---|
| the accent | `overlay.py`, `_accent` from the Qt palette's Highlight | the KDE accent colour, so it follows the user's theme |
| the dimming | `dim_percent`, default 40 % | flat black over everything the upload will not contain |
| the action bar | `_BAR_*` constants, `_draw_action_bar` | a black pill, 11 px corners, the primary button filled with the accent |
| the readouts | `_draw_box` | black at 190 alpha, 4 px corners, white text |
| the chips | the same bar, before anything is drawn | the current mode lit, *Shift* shown against the other |
| the stroke | `stroke.py`, and `docs/STROKE.md` for why | a white ribbon with a four-colour glow trailing under its head |
| the handles | `_HANDLE`, `_GRIP`, `_draw_handles` | accent squares with a white rim, and a four-way arrow in the middle |
| the loupe | `_LOUPE_*`, `_draw_loupe` | a circle, 4×, crosshair in two colours so it shows on anything |
| the two dialogs | `settings_dialog.py`, `welcome.py` | plain Qt widgets, whatever the platform style gives |
| the tray icon | `app.py`, `_ICON_NAMES` and the dimming for the off states | a themed icon, faded when shaking will not work |

**Two constraints worth putting on the table before the conversation starts**,
because both are load-bearing and neither is obvious:

* the un-dimmed area has to stay *exactly* what the upload will contain — that
  is not decoration, it is the promise the whole confirmation step rests on;
* whatever is drawn over the frozen screen is drawn over **somebody else's
  screen**, which may be any colour.  Every element that survived contact with
  real use has two-tone edges for that reason: a white line under a dark one, a
  crosshair in two colours, a rim around the loupe.  A flat single-colour design
  looks better in a mock-up and disappears against the wrong wallpaper.

**And one that is not negotiable:** the pictures in the documentation are
generated by `tools/make-screenshots.py` from the real widgets.  A redesign is
not finished until that run produces the new look, in both languages, or the
README starts lying.

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
