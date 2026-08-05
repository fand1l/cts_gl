# Circle to Search — the technical half

Everything about how this is built and why, and what to do when a piece of it
stops working.  If you only want to install it and use it, the front page is
[`README.md`](../README.md) and it will not send you here.

Four processes have to agree for a shake of the mouse to end in a browser tab,
and most of this document is about the seams between them.

## How it works

```
   you shake the mouse
            │
            ▼
┌────────────────────────────────────┐  D-Bus: Trigger(x, y, "eDP-1")
│  KWin script (JavaScript)          │ ───────────────────────────────┐
│  · polls workspace.cursorPos       │                                │
│  · finds diagonal reversals        │                                │
│  · raises the overlay above panels │                                │
└────────────────────────────────────┘                                │
                                                                      ▼
                                     ┌───────────────────────────────────────┐
                                     │  Python daemon (systemd --user)       │
                                     │  · io.github.fand1l.CircleToSearch    │
                                     │  · capture: ScreenShot2 → spectacle   │
                                     │    → xdg-desktop-portal               │
                                     │  · full-screen selection overlay      │
                                     │  · crop → JPEG → local launcher page  │
                                     │  · xdg-open it; the browser uploads   │
                                     └───────────────────────────────────────┘
```

Four components, each in its own place:

1. **KWin script** (`kwinscript/`) — on Wayland an ordinary client cannot ask
   for the global pointer position, but the compositor knows it. The script
   polls `workspace.cursorPos` on one QTimer (50 ms while moving, 250 ms when
   idle), recognises the gesture, and does exactly one D-Bus call when it fires.
   No D-Bus traffic while the pointer is used normally — that is what made
   `plasma-cursor-eyes` infamous for eating CPU. It also refuses to look at the
   cursor at all while a full screen window has the focus, so a shake in a game
   is just a shake.
2. **Python daemon** (`src/circle_to_search/`) — a resident `systemd --user`
   service exporting `Trigger(int32 x, int32 y, string screen)` over
   `PyQt6.QtDBus` (one Qt event loop for D-Bus and GUI alike).
3. **Overlay** (`overlay.py`) — a frameless full-screen widget showing the
   screenshot, dimmed, with the selection as a "hole" like Spectacle's region
   mode. The default gesture is a **freehand lasso**, and like Circle to Search
   on a phone the loop only *marks out the edges*: what gets uploaded is the
   plain rectangle around it, so the un-dimmed area always shows exactly what
   Google will receive. Hold *Shift* while starting a drag for a rectangle (or
   swap the default in Settings). Letting go does not send anything: the
   selection waits with handles on its edges and a bar of buttons underneath —
   search, copy, save, cancel — each also reachable as **Enter**, **C**, **S**
   or **Esc**.
4. **Lens upload** (`lens.py`) — the daemon does **not** upload. It writes a
   small self-contained HTML page with the JPEG inlined and opens it, and the
   *browser* posts it to `lens.google.com/v3/upload`. That is not a detour:
   Google binds the result page to the session that uploaded, so an upload made
   here produces a URL your browser cannot resolve (see below). As a side
   effect the daemon never makes a network connection at all. Direct uploading
   is still implemented and selectable.

The overlay keeps **one** copy of the screenshot and paints the dimming over
the parts that are not selected, rather than holding a second pre-dimmed pixmap:
at 3840×2160 that second copy is another ~33 MB resident, and with `all_screens`
there is one overlay per monitor.

The HiDPI arithmetic lives in `hidpi.py`: KWin's coordinates and Qt's widget
coordinates are **logical** pixels, the screenshot is **physical** pixels, and
the scale is *measured* (screenshot size ÷ logical screen size) rather than
assumed, which also covers fractional scaling like 150 %.

### Why layer-shell is off by default

There are no Python bindings for `layer-shell-qt` (a C++ library without
GObject introspection). The only piece reachable from Python is its Qt
*shell-integration plugin*, requested with
`QT_WAYLAND_SHELL_INTEGRATION=layer-shell` before `QApplication` is created.
That is implemented (Settings → General → "Use layer-shell for the overlay";
the plugin file is checked to exist first, because Qt aborts when asked for a
shell integration it cannot load) but **off by default**: without the C++ API
the surface cannot be anchored or given an exclusive zone, so the result is less
predictable than a plain full-screen window that the KWin script pushes above
the panels. Try it if you like; the fallback is the tested path.

---

## Installation

```bash
git clone https://github.com/fand1l/cts_gl.git
cd cts_gl
./install.sh
```

`install.sh` checks the session (Wayland + Plasma 6 + KWin running), offers to
install anything missing through **dnf, apt, pacman or zypper**, and then
installs **entirely into your home directory** — nothing else needs root:

| What | Where |
|---|---|
| Python package | `~/.local/share/circle-to-search/` |
| Launcher | `~/.local/bin/circle-to-search` |
| KWin script | `kpackagetool6 --type=KWin/Script --install ./kwinscript` |
| Desktop entry | `~/.local/share/applications/io.github.fand1l.CircleToSearch.desktop` |
| Icon | `~/.local/share/icons/hicolor/scalable/apps/` |
| Service | `~/.config/systemd/user/circle-to-search.service` |

It then enables the script (`kwriteconfig6 --file kwinrc --group Plugins --key
circletosearchEnabled true` + `reconfigure`) and starts
`systemctl --user enable --now circle-to-search.service`.

Flags: `-y` (don't ask before the package manager), `--no-deps` (never call it),
`--force` (install even if the session checks fail, and update over a checkout
with local changes).

The package names are a guess on every distribution but Fedora, so the installer
**checks again afterwards** and names anything that is still missing rather than
assuming the guess worked. On a package manager it does not know it prints the
requirements in words — PyQt6 with QtDBus, Pillow, requests, and the KConfig and
KPackage command line tools — and carries on.

### Updating

```bash
./install.sh update               # git pull, then reinstall
```

`reinstall` has never had anything to do with git — it installs *this checkout*,
whatever state it is in — so keeping up with the project was two commands and
remembering the second one. `update` is both, and the order is the point:

* **the pull happens first.** A pull that fails has removed nothing, so the
  installation that was working is still the one running. Every refusal below
  leaves the machine exactly as it was found;
* **fast-forward only.** An update is not the moment to discover that a merge
  wanted a decision, and refusing beats a conflicted checkout installed over the
  top of a working one;
* it stops before pulling if the checkout has **local changes** (`--force` skips
  that check; git still refuses to overwrite a file you edited), if **HEAD is
  detached**, if the branch **tracks nothing**, or if the directory is **not a
  git checkout** at all — each with the command that fixes it;
* it prints the **commits it pulled**, so "what did I just get" is answered
  without going to look;
* it **reinstalls even when nothing was pulled**, because that is the guarantee
  being asked for: no file left over from a version that no longer ships it.

Then it **`exec`s the installer it just pulled**. Two reasons, and the second is
the one that bites: the new code is what knows where the new code goes — an old
installer would not place a file this version has only just started shipping —
and bash reads a script as it runs it, so carrying on inside a file that has
changed underneath is a way to execute something nobody wrote.

If the KWin script changed, `verify` says so at the end and names the fix; see
below.

### Reinstalling

```bash
./install.sh reinstall            # take the old installation out, then install
./install.sh reinstall --config   # ...and erase the settings as well
```

A plain install already overwrites everything it owns, so `reinstall` is for the
case that does not cover: a file the project used to ship and no longer does,
left behind and still being loaded. It removes what the installer put there —
the package, the launcher, the desktop entry, the icon, the systemd unit and the
KWin script — and nothing else. **Settings, recent captures and saved traces all
survive.**

`--config` additionally erases `~/.config/circle-to-search/` and the
`[Script-circletosearch]` group in `kwinrc`: every setting, the calibrated
thresholds, the shortcut, and the answers to the questions the program only asks
once. It lists exactly that and asks you to type `yes` — `-y` does not skip it,
and it refuses to run at all without a terminal to ask on. Answering anything
else stops before a single file is touched, so the working installation is left
exactly as it was. Recent captures and saved traces are kept even then; use
`./uninstall.sh --purge` for those.

Uninstall with `./uninstall.sh` (add `--purge` to drop the settings and the data
too).

### Verify

```bash
systemctl --user status circle-to-search.service      # should be active
busctl --user list | grep CircleToSearch              # the name is on the bus
busctl --user call io.github.fand1l.CircleToSearch \
    /io/github/fand1l/CircleToSearch \
    io.github.fand1l.CircleToSearch Ping              # prints the version
journalctl --user -u plasma-kwin_wayland -f | grep -i circle
                                                      # "KWin script started"
```

Then shake the pointer: **down-right, up-left, down-right** at roughly 45°,
about 150 px per swing, inside 600 ms. Or press **Meta+Shift+L**. Then circle
what you want to look up and press **Enter**.

Check the upload path on its own, without the GUI:

```bash
circle-to-search --test-lens ~/Pictures/something.png --verbose   # the configured endpoint
circle-to-search --probe-lens ~/Pictures/something.png            # all of them, compared
```

### Packaging (RPM / COPR)

`circle-to-search.spec` builds a noarch package.

```bash
sudo dnf install rpm-build rpmdevtools copr-cli
rpmdev-setuptree
git archive --format=tar.gz --prefix=circle-to-search-1.0.0/ \
    -o ~/rpmbuild/SOURCES/circle-to-search-1.0.0.tar.gz HEAD
rpmbuild -ba circle-to-search.spec        # local RPM
```

To publish it:

1. Log into <https://copr.fedorainfracloud.org>, **API keys** → paste the token
   block into `~/.config/copr`.
2. `copr-cli create circle-to-search --chroot fedora-44-x86_64 --description "Circle to Search for KDE Plasma"`
3. `rpmbuild -bs circle-to-search.spec`
4. `copr-cli build circle-to-search ~/rpmbuild/SRPMS/circle-to-search-1.0.0-1.*.src.rpm`
5. Users then run
   `sudo dnf copr enable <your-fedora-account>/circle-to-search && sudo dnf install circle-to-search`.

The RPM cannot enable the KWin script or the service for you (both are per-user
settings) — `%post` prints the three commands to run once.

---

## Settings

Tray icon → **Settings**, or `circle-to-search --settings`.

Detection values are stored in `~/.config/kwinrc` under
`[Script-circletosearch]` via `kwriteconfig6`, which is exactly where the KWin
script's `readConfig()` reads them, and KWin is asked to `reconfigure`
afterwards. The same values are editable from System Settings → Window
Management → KWin Scripts → Circle to Search ⚙.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Detect the shake at all |
| `reversals` | `2` | Direction reversals needed to fire |
| `windowMs` | `600` | They must all happen inside this window |
| `minAmplitudePx` | `150` | Minimum length of one swing |
| `minSpeedPxPerSec` | `700` | **A swing slower than this is drawing, not shaking** |
| `maxCurvaturePct` | `140` | How much a swing may curve (100 % = a straight line) |
| `angleTolerance` | `30` | Allowed deviation from 45° |
| `reversalTolerance` | `40` | Allowed deviation from a full 180° turn-back |
| `pollMs` | `50` | Cursor polling interval while moving |
| `cooldownMs` | `1500` | Ignore further shakes for this long |
| `minStepPx` | `6` | Movement below this is noise |
| `disableInFullscreen` | `true` | Ignore the shake while a full screen window has the focus (games, video). The global shortcut still works. |
| `restoreFocus` | `true` | Give the keyboard back to the window that had it once the overlay closes |
| `debug` | `false` | Log why each swing was accepted or rejected |
| `trace` | `false` | Log every cursor sample, for `tools/record-trace.sh` |
| `calibrating` | `false` | Set by the calibration window while it is open; see below |
| `collectTraces` | `false` | Set by the daemon while it is still learning from misfires; see below |
| `shortcut` | `Meta+Shift+L` | Fallback global shortcut |

Changing any of them takes effect on the running session: the script re-reads
its settings on `options.configChanged` and, as a safety net, at least every
four seconds.

### Calibrate the gesture instead of guessing

Six thresholds are six chances to get it wrong by hand, which is why the
Detection tab does not open on a wall of spin boxes. What it offers is the
switch, the **Calibrate the gesture…** button, and two other switches; the ten
raw numbers are all still there, unchanged, inside **Advanced: the raw
thresholds**, one click away for the cases calibration cannot help with.
Writing the calibration on the argument that tuning six numbers by hand is the
wrong job for a person, and then putting those numbers first, said the
opposite.

Above the button is the gesture itself: a dot travelling the exact path the
current settings are asking for. It is not a drawing of "the" gesture that
could drift out of step with the code — `reversals` sets how many strokes there
are, `minAmplitudePx` how long each one is, `minSpeedPxPerSec` how fast the dot
travels, `angleTolerance` how far the strokes lean off the 45° diagonal, and
`windowMs` decides whether it says the strokes will not all fit in the time
allowed at the minimum speed. Change a threshold and the movement changes while
you watch; calibrate, and it becomes your own shake. The same panel is the
second thing the first-run window shows, because a movement is very hard to
write down and very easy to show.

Settings → **Calibrate the gesture…** (also in the tray menu) measures your own
shake and writes the numbers for you:

1. The dialog puts the KWin script into `calibrating` mode. In that mode the
   script **never opens the overlay** — it only measures — so nothing pops up
   while you are shaking on purpose. It measures even when detection is
   switched off or a full screen window has the focus, so a broken threshold
   cannot lock you out of fixing it.
2. Shake the cursor the way that feels natural, about a dozen times. Every
   swing is reported over D-Bus as
   `CalibrationSample(length, speed, curvature, diagonal, turn, duration)`, and
   swings shorter than 40 px are ignored as twitches.
3. After six usable swings the table fills in with *setting → now → suggested*
   and keeps refining as you continue. **Apply** writes them; **Cancel**
   changes nothing.

The margins are deliberately one-sided: the amplitude and speed suggestions sit
**below** what you actually did (0.6× and 0.55× of the median), and the
tolerances sit **above** your worst swing. A threshold that is slightly too
loose costs one stray overlay; one that is slightly too tight makes the feature
look broken.

Closing the window always leaves `calibrating` mode — Apply, Cancel and the
titlebar × all go through the same exit — so the gesture can never stay dead
afterwards. If it ever does, the culprit is visible:

```bash
kreadconfig6 --file kwinrc --group Script-circletosearch --key calibrating
kwriteconfig6 --file kwinrc --group Script-circletosearch --key calibrating false
qdbus6 org.kde.KWin /KWin reconfigure
```

The arithmetic lives in `src/circle_to_search/calibration.py` as one pure
function, so `python3 tests/test_logic.py` checks it without Qt, D-Bus or KWin.

Application-only settings live in
`~/.config/circle-to-search/circle-to-search.conf`:

| Key | Default | Meaning |
|---|---|---|
| `selection_mode` | `lasso` | `lasso` (freehand) or `rectangle`; *Shift* swaps it for one drag |
| `lasso_mask` | `false` | Keep only the inside of the loop and whiten the rest — off, because the loop is there to set the bounds |
| `all_screens` | `false` | Open an overlay on every monitor at once (see below) |
| `keep_recent` | `true` | Keep the last five selections for the tray menu (see below) |
| `first_run_done` | `false` | Set once the welcome window has been through |
| `confirm_selection` | `true` | Wait for a key instead of sending the moment the button is released |
| `save_directory` | *(Pictures)* | Where **S** writes |
| `ocr_enabled` | `false` | Let **T** read text (see below) |
| `ocr_asked` | `false` | Whether the one-time question was shown |
| `ocr_languages` | *(auto)* | e.g. `ukr+eng`; empty follows the interface language |
| `lens_backend` | `browser` | `browser` (the browser uploads), `auto`, `lens`, `searchbyimage`, or one variant name |
| `max_side` | `1000` | Longest side of the uploaded JPEG |
| `jpeg_quality` | `85` | |
| `copy_to_clipboard` | `false` | Also put the selection on the clipboard |
| `learn_from_misfires` | `true` | Occasionally ask whether a trigger was wanted (see below) |
| `dim_percent` | `40` | Overlay dimming |
| `language` | `auto` | `auto`, `uk` or `en` |
| `use_layer_shell` | `false` | See above |
| `keep_launcher` | `false` | Keep the browser launcher page instead of deleting it after ten minutes |

### The lasso is a ribbon

The lasso is drawn the way Android draws it: a wide white line — twelve logical
pixels, opaque, round caps — with a soft coloured glow under its head. The line
carries no colour at all; the colour is in the glow, and it comes from **where
on the screen the glow is**: blue at the top, red across the middle, yellow a
little below that, green at the bottom. A fast swipe from one corner to the
other lays all four out at once.

The stretch is not computed from velocity. The last half-second of head
positions is kept, each with the colour its own height gave it, and the glow is
drawn at every one of them fading with age — so moving fast they lie far apart
and the glow *is* a smear, and standing still they stack on one spot and it is a
bright round circle that settles into a deep amber. Nothing snaps when the
direction changes, because no direction is ever calculated. It keeps fading for
a fifth of a second after the button comes up, and while it fades only the
trail's own rectangle is repainted — a few hundred pixels square, never the
screenshot.

**The stroke ends with the gesture.** Let go and the line and its glow are
gone: what is left is the un-dimmed box, its handles and the bar, which say
everything the line was saying, and a twelve-pixel ribbon lying across the
result is in the way of reading it. With `lasso_mask` off there is no thin
outline in its place either — the crop is the bounding box, the un-dimmed area
already is that box, and one more line tracing the loop is the same fact drawn
twice. With the mask on the loop stays outlined, because then it *is* the shape
that gets cut out.

The drawn line is **open**: the two ends pass each other without joining, as
they do in the photographs. The path used for `lasso_mask` still closes,
because a mask needs a closed shape — at one pixel the closing line was a hint,
at twelve it is a bar across the middle of whatever you circled. There is no
dashed rectangle around the loop any more either: with `lasso_mask` off the
un-dimmed area *is* the bounding box, so it was drawing the same fact twice.

Under the white line there is a translucent dark one a few pixels wider.
Android draws over photographs and does not need one; we draw over whatever you
had on screen, and a white line on a white page is invisible.

Drawing it is kept cheap on purpose. The settled part of the ribbon is baked
into a layer and only the last few dozen points are re-stroked each frame, so
the cost of a frame does not grow with the length of the line; and the glow is
one pre-rendered blob per colour, blitted, rather than a radial gradient
rasterised forty times a frame. Without those two a long scribble ran at about
one frame a second — Qt takes 88 ms to stroke a 1500-point antialiased
twelve-pixel path, and there are two passes of it in a frame.

A drag repaints a **region**, not the window: on a 3840×2160 screen the overlay
is 33 MB, and handing the compositor that sixty times a second is what made a 4K
panel crawl while a 1080p one was fine. The box contributes a *ring* around its
outline and never its filled inside, so a loop already covering most of the
screen does not damage most of the screen each time it grows by five pixels.

The price of that is that **everything drawn near the selection has to name
itself** in `_floating_rects()` — the handles hang half outside the outline, the
move grip sits in the middle of the box, and the action bar and the size readout
follow the box around. Anything left out of that list is never erased from where
it was: moving the box smeared the handles, the grip and the bar across the
screen in stripes until they were added.

The full design, the two drafts the photographs corrected, the measurements and
that repaint bug are in [`STROKE.md`](STROKE.md).

Lens always receives a rectangle. By default a lasso is uploaded as the plain
crop around the loop — nothing is painted over, exactly like circling something
on Android. `lasso_mask=true` instead whitens everything outside the loop, which
is useful for isolating one object from a busy background; the overlay follows
the setting, so the bright area is always what gets sent.

The UI is available in Ukrainian and English; it follows the system locale
unless you pick one in Settings.

### Check it before it goes anywhere

Releasing the button used to upload immediately, which is one slip away from
sending the wrong part of the screen to Google — and an upload cannot be taken
back. So the drag now only *marks the area out*, and the overlay waits:

A small bar appears under the selection with the four things you can do to it
— **Search**, **Copy**, **Save**, **Cancel** — and each button carries its
shortcut beside it, so the keyboard is still there once you have learnt it:

| Key | |
|---|---|
| **Enter** (or Space) | search it with Lens |
| **C** | copy it to the clipboard |
| **S** | save it as a PNG (Pictures, or `save_directory`) |
| **T** | select all the recognised text (optional, see below) |
| **Esc** / right-click | cancel |

The bar is painted, not made of widgets — real widgets would swallow the presses
the drag, the handles and the text layer all need — but it behaves like one:
hover highlights, pressing and sliding off takes the press back, and it is
hit-tested before everything else, so a button over a word acts as a button. It
flips above the selection when there is no room below and is pushed back on
screen at the edges.

Before anything is drawn the same bar sits at the top and carries the two
selection shapes instead — **Lasso** and **Rectangle**, the current one lit,
*Shift* shown against the other because holding it has always swapped them for
a single drag. Clicking one keeps it: it is the setting from *Settings →
General*, put where it is actually wanted. With one overlay per screen the
others follow immediately.

A third chip, **Colour** (*K*), turns the overlay into a screen colour picker
instead of a selection tool. Click any pixel and its `#rrggbb` goes to the
clipboard; *Shift* copies `rgb(…)` for a stylesheet; *Esc* goes back to
selecting and a second one closes the overlay.

Plasma has an eyedropper of its own and this is not a new capability for the
desktop — what is different is where it sits. On Wayland no client can read a
pixel off another's window, so a colour picker has to be summoned first and then
aimed at a live screen. Here the screen is *already* frozen and *already*
magnified under the pointer, so by the time the question occurs to you the
answer is on screen and about to be thrown away. It is a value that already
exists being kept.

Four things follow from doing it inside the overlay:

* **one pixel is copied, not the screenshot.** `self._sharp.copy(QRect(x, y, 1,
  1)).toImage()` is a few bytes; `toImage()` on a 4K pixmap would be the 33 MB
  this overlay has spent two rounds of work learning not to touch per frame. The
  coordinates go through the same `logical_rect_to_physical` the crop uses, so it
  is the real pixel and not an interpolated one;
* **the loupe follows the pointer here, and only here.** Not doing that on hover
  was a deliberate decision — a magnifier trailing around a frozen screen is the
  cursor-glow mistake — but in this mode the loupe *is* the tool, and nobody pays
  for it who did not choose the mode. Only its own rectangle and the swatch's
  are repainted on each move;
* **nothing is dimmed.** Asking what colour something is, over a wash, would be
  answering about a different picture than the one being looked at;
* **the label over the swatch is black or white by linear luminance**
  (`colours.readable_on`), not by the cheap weighted sum of the stored channels.
  The shortcut is wrong exactly where it shows: a mid green like `#00c800` comes
  out "dark" on it and gets white text, when black is four times as readable.

Unlike the two shapes, choosing it does **not** stick. Lasso and rectangle are
two ways to do the same job and are worth remembering; picking a colour is a
different job, and nobody wants yesterday's colour pick to be what happens when
they shake the mouse today.

Beside them, when there is one to offer, **The same area** (*R*) — the exact
rectangle of the previous capture. Comparing something that changes, a build
log or a download counter or a number on a dashboard, means taking the *same*
rectangle twice, and a rectangle drawn by hand is never quite the same twice,
which is what makes the two results hard to compare in the first place.

It is four numbers and a screen name in the settings file (`lastarea.py`), not
a file beside the kept crops: offering the rectangle again is useful whether or
not the pictures are being kept, and four numbers are not a copy of anything
that was on screen. **Logical** pixels, so it still means the same place after
the scale factor changes, and it is the space the overlay itself works in.

* It is only offered to the screen it was taken on. A rectangle from the other
  monitor would put the selection somewhere arbitrary, and a chip that did that
  would be worse than no chip. A selection that spanned several screens is
  filed under `*` in global logical pixels and offered back only to a group,
  each overlay receiving it shifted into its own coordinates.
* It is clamped to the screen as it is *now*, and dropped outright if nothing of
  it is left there — monitors get rearranged between captures.
* The parser is strict and silent: five fields or nothing. It reads a file a
  person can edit, the only cost of refusing a line is that a convenience is not
  offered, and half a parsed rectangle would select somewhere nobody asked for.

While a selection is being drawn — and while an edge is being moved — a 4×
loupe with a crosshair follows the pointer, so the pixel an edge is about to
land on is visible while it is still being placed. It is cut from the
screenshot that is already in memory, nothing is captured again, and it shows
the frozen screen undimmed. Turn it off with **Settings → General → Magnify
while dragging** if a thing that follows the pointer bothers you.

Before pressing anything the box can be adjusted: drag any of the eight
handles, drag the **grip in the middle** to move the whole thing, or use the
**arrow keys** (*Ctrl* for 10 px steps, *Shift* to stretch the far edge instead
of moving). The arrow keys are the one thing the buttons cannot announce, so a
line inside the bar says so.

**Pressing anywhere else — inside the box included — starts a new selection.**
The whole inside used to be the move grab, which meant a selection covering
most of the screen could never be redrawn: there was nowhere left to press that
did not move it. So moving got a grip of its own, and everything else went back
to meaning "no, that one". Text still outranks both: a press on a recognised
word takes the words.

Adjusting the box of a *lasso* selection drops the loop outline, because a loop
that no longer matches its box would produce a wrong mask when `lasso_mask` is
on. The selection simply becomes the rectangle you adjusted.

**The readout says what will be sent, not how big the crop is.** Under
`1200 × 480 px` there is a second line — `→ 1000 × 400, ~30 KB JPEG` — because
those are two different numbers: `prepare_image` resizes to *Longest side* and
re-encodes at *JPEG quality*, and neither of those settings could be judged from
a readout that only ever showed the one number the user does not control.

The two halves arrive separately, on purpose:

* **the dimensions are arithmetic**, so they are there the instant the selection
  settles. `imageops.scaled_size()` is the same function `prepare_image` calls,
  which is why the line cannot drift away from what actually happens;
* **the byte count needs a real JPEG**, because a guess from pixels and quality
  is wrong by a factor of three between a photograph and a page of text — and a
  made-up number would defeat the whole point. So the overlay emits
  `estimate_requested`, the app really prepares the crop in a `QThreadPool`
  worker, and the answer comes back with the crop it was measured for so a slow
  answer about a box that has since moved is dropped instead of drawn under the
  new one. A full-screen 4K crop costs about 200 ms of somebody else's thread.

It is asked for 250 ms after the box last changed, so dragging a handle across
the screen measures once at the end rather than sixty times a second, and the
count disappears the moment the box moves — a stale weight under a box of a
different size is worse than no weight at all. Small crops that are not resized
show only `→ ~8 KB JPEG`, since repeating the size on the line above with an
arrow in front of it would say nothing.

With `all_screens` on, the second line is **not** shown. The crop there is
stitched from several screenshots at the highest scale involved, and a number
that was nearly right would be worse than the honest absence of one — which is
the argument the whole readout rests on.

**Black out part of it before it goes.** *Black out* (**B**) turns the next
drags into solid rectangles over the selection; **Backspace** undoes the last
one, **Esc** leaves the mode without throwing the capture away, and the button
carries the count.

This is the other half of the argument the confirmation step was written on. If
an upload cannot be taken back, being able to change only the *bounds* of the
selection is half an answer: when a token or an address happens to sit next to
the thing you want to look up, reframing the crop until it excludes them is not
always possible.

Three things it has to get right, and they are all about *when*:

* the rectangles are **baked into the pixels**, in `imageops.black_out()`, and
  the very first thing done to the crop in `_on_selected` — before the lasso
  mask, before `prepare_image`, and long before `write_browser_launcher` puts a
  JPEG on disk for ten minutes. There is no code path that produces the
  unredacted version, and the copy, the saved PNG and the kept capture are all
  made from what comes out;
* **the recognised words go too.** The screen has already been read, and the
  words inside a crop are kept beside it in a `.txt` so the tray's *Read the
  text* is instant. A word the black rectangle so much as clips is dropped
  (`ocr.words_outside`) — otherwise the thing that was covered in the picture
  would be sitting next to it in plain UTF-8;
* the **byte count in the readout** is measured on the redacted crop, because
  solid black compresses to nearly nothing and the number would otherwise be
  about a different image. That made the estimate answer need a serial as well
  as a crop: covering something does not move the box, so "same rectangle" was
  no longer enough to tell a fresh answer from a stale one.

While it is on, the handles and the grip are not drawn and the whole box belongs
to the drag — what needs covering is usually in the middle of what was selected,
which is exactly where the grip lives. What is on screen is solid black, not an
outline or a blur, because the picture being checked has to be the picture that
is sent.

**Searching does not make the overlay vanish.** Preparing the image and writing
the launcher page take a moment, and the browser takes longer still, so the
frozen screen stays up with the selection still lit and a badge saying
*Надсилаю в Google Lens…* until the page is on disk — the same argument that
produced the reading badge. It comes down by itself the moment the browser is
handed the page, and it has a five-second dead-man's switch and a
take-it-away-now on any key, click or focus change, so nothing that goes wrong
downstream can leave the screen frozen. Copying and saving still close it
outright: they are finished by the time it would have shut.

Uncheck **Settings → General → Check the selection before sending it** to get
the old send-on-release behaviour back.

### The first start

A tray icon appearing after an install tells nobody what to do with it, and
"shake the pointer" is not something anyone guesses. So the first start opens
one window that says it, offers the few decisions worth making up front
(language, lasso or rectangle, detection on or off, start at login) and points
at the calibration.

It appears once. Whatever you do with it — answer it or close it unread —
`first_run_done` is set and it does not come back. To see it again:

```bash
circle-to-search --welcome
```

### Click a window instead of drawing round it

Before a drag starts, the window under the pointer is outlined and lifted out
of the dimming, and a plain click takes exactly that rectangle. The case this
removes is a lasso drawn laboriously around a rectangular panel that the
compositor could have named in one number.

It has to come from KWin: a Wayland client cannot see anybody else's geometry.
The script reads `workspace.stackingOrder` — **not** `windowList()`, which
returns everything in no particular order; reading that as a stack is what once
made the overlay offer a window sitting behind the browser that you could not
see at all — and sends the layout with the trigger as `WindowRects("x,y,w,h;…")`
— global logical pixels, front-most first — in the same encoding as the trace,
and immediately *before* the trigger, because D-Bus keeps the order of calls on
one connection. It is sent once per trigger and never from the poll tick, so
the CPU budget is untouched.

Only what is on the screen *right now* is offered. Being in the window list is
not the same as being visible, and every one of these points at nothing you can
see: minimised windows, windows on another virtual desktop or activity, the
desktop background, KWin's own selection outline, the drag-and-drop surface, and
our own overlay. Panels and docks are deliberately kept — circling one is exactly
the kind of thing this is for.

Three more things it gets right that are easy to get wrong:

* **Windows partly off screen.** A window can hang off an edge or straddle two
  monitors, so each rectangle is clipped to the screen and moved into its
  coordinates before it is offered — a click can never ask for a crop that is
  not in the screenshot.
* **Shadows are not part of the frame.** `frameGeometry` excludes KWin's drop
  shadow, so the outline sits on the window and not on its blur.
* **Text still wins.** Over a recognised word the outline disappears, because a
  press there takes the words; offering the whole window would promise
  something that will not happen.

The outline is only for *before* the drag. Once the button is down you are
drawing, and an outline arguing with the selection would be noise. If the KWin
script is older than the daemon it simply never sends a layout, and there is no
outline — nothing else changes.

### The tray icon says whether it will work

Hovering the icon answers "is this thing on?", because there are four quite
different reasons a shake might do nothing and no way to tell them apart by
shaking harder:

| icon | |
|---|---|
| normal | detection is on, the script is installed and enabled — shaking works |
| dimmed | *detect cursor shake* is switched off; the shortcut still works |
| dimmed | the KWin script is not installed at all — run `install.sh` again |
| dimmed | it is installed but switched off in *System Settings → Window Management → KWin Scripts* |
| dimmed | **KWin is running an older copy of the script than the one on disk** |

That last one is why this exists. KWin loads a script once, at login, and
`reconfigure` re-reads its *settings* without re-reading its *code*, so after
an upgrade the file on disk and the code in the compositor can be two different
things — and every symptom of that looks like "the checkbox does not work". It
cost two rounds of debugging during development, twice.

So the script now announces its own version over D-Bus (`ScriptReady`) when it
loads and again whenever the settings change, and the daemon compares it with
the version it reads out of the installed `main.js`. If they differ the tooltip
names both and says to log out. Nothing is claimed until the script has spoken:
a daemon restarted mid-session has heard nothing yet, and silence is not
evidence — but changing any setting, which is the first thing anyone does when
a toggle appears to do nothing, makes KWin reconfigure and brings the answer
with it.

Everything above is read **fresh** every time the menu is opened. Caching it
would reintroduce precisely the bug it is there to expose.

### Getting the keyboard back

The overlay takes the focus while it is up — it has to, or *Enter* and *Esc*
would go somewhere else. `restoreFocus` (on) makes the KWin script remember
which window had the keyboard beforehand and give it back once the overlay is
gone. It has to be the script's job: a Wayland client cannot hand the focus to
another client's window, but the compositor can.

It only acts when the keyboard ended up **nowhere**. KWin usually refocuses the
previous window by itself, and a browser tab opened by a search is entitled to
the focus it took — stealing it back a moment later would be worse than doing
nothing.

### Why "Capture now" goes the long way round

The tray's **Capture now** does not open the overlay itself. It asks
kglobalaccel to press the KWin script's own shortcut:

```bash
busctl --user call org.kde.kglobalaccel /kglobalaccel \
    org.kde.KGlobalAccel invokeShortcut ss kwin CircleToSearch
```

The script's handler then reads `workspace.cursorPos` and calls `Trigger` with
the *real* pointer position. Doing it locally would mean asking Qt, and a
Wayland client only knows where it last saw the pointer inside one of its own
windows — which is right while the tray menu is open and a guess at every other
moment.

kglobalaccel does not complain about an action it has never heard of, so a
successful call is not proof that anything happened. If no trigger arrives
within 600 ms the old behaviour takes over and the journal says so:

```
INFO  the compositor did not act on the shortcut; falling back to Qt's idea of
      the pointer position
```

That line means the KWin script is not loaded, or its shortcut is registered
under a different name — check System Settings → Shortcuts → KWin.

### The last five, in the tray

Tray icon → **Recent captures** lists the last five selections with a thumbnail
and their size. Each one can be searched again, copied, saved, or read as text,
without redoing the gesture — useful when the answer was "that was the wrong
result, try the same crop again" or when the screen has already changed.

They are PNGs in `~/.local/share/circle-to-search/recent/`, pruned to five as
new ones arrive. That is a real thing on disk, so: **Forget this one** and
**Forget all of them** are in the same menu, and unticking Settings → General →
*Keep the last few selections* stops it entirely (the list then says so instead
of pretending to be empty).

### Every screen at once

By default the overlay opens on the monitor the pointer was on. Settings →
General → **Show the overlay on every screen** captures and dims all of them
instead, so you can select on whichever one you like without shaking there
first — and a drag that runs past an edge carries on onto the next screen.

It is off by default because on a single monitor it changes nothing while
costing an extra capture, and on three monitors it is three captures per
trigger.

How it works, and where it can disappoint:

* There is no such thing as one Wayland surface spanning two outputs, so this
  is one full-screen overlay per screen. They agree about a single rectangle
  in **global logical coordinates**; the one you started the drag on owns it,
  the others draw their share, and the seam falls exactly on the screen edge.
* Crossing an edge relies on the compositor's implicit pointer grab: the
  surface where the button went down keeps receiving motion, with coordinates
  that run past its own bounds. KWin does this. If a compositor does not, the
  drag simply stops at the edge and everything else still works.
* The crop is stitched back together at the **highest scale factor involved**,
  so a 4K panel is not downsampled to match the 1080p one beside it; the part
  from the coarser screen is upscaled instead. In an L-shaped layout the area
  no screen covers comes out black, because that is what is there.
* The KWin script promotes every overlay it finds, not just the first, or the
  ones on the other screens would sit under their panels.

### Selecting the text on the frozen screen

Sometimes the answer is not "what is this" but "let me copy that". With text
recognition on, the overlay **starts reading the screen the moment it opens**
and the words become selectable where they are:

* **the recognised lines stay lit while the rest of the screen dims**, with a
  soft highlighter wash and a rule under each one. That is the whole
  affordance: you can see what can be taken before the pointer goes anywhere
  near it, and the marks stay put once you draw a rectangle, because the text
  is still takeable then;
* **drag across a line** and you get text instead of an area — the way a browser
  tells text and pictures apart, so there is no mode to switch into. Text has
  priority: pressing on a line takes the text even when it lies inside an area
  you already selected, and even the gaps between words count, because being
  made to hit a five-pixel glyph exactly is how you end up dragging a rectangle
  by mistake. Only the eight resize handles outrank it, so a box drawn over a
  paragraph can still be adjusted;
* **double-click** takes one word, **T** takes everything that was found;
* **Enter** searches for the words, **C** copies them, **Esc** lets go of the
  selection (a second **Esc** closes the overlay).  The bar changes with it:
  *Search*, *Copy text (12)*, *All text*, *Back* — the same two keys as the
  area bar underneath, doing the same two things, and the count of selected
  words is on the button rather than in a sentence beside it;
* the pointer turns into an I-beam over anything that can be taken.

**Searching for the words is not the same as searching for a picture of them.**
By the time a sentence is selected the program already has the text, so sending
an image of it to Lens would be a round trip through the network to answer a
question that is already answered — and a picture of a sentence is a worse query
than the sentence. So the text bar's primary action opens a plain Google web
search for the string (`websearch.py`, `SEARCH_URL`), with the interface
language as `hl=`; no image is made, nothing is uploaded, and the whole thing is
one `xdg-open`.

If what you selected **is a link**, searching for it is the wrong thing
entirely, so the button says *Open the link* and opens it. The guess is
deliberately conservative, because opening something nobody asked for is much
worse than making them copy and paste: it takes an explicit `http(s)://`, a
`www.` host, or a single dotted token whose last part is not in a blocklist of
file extensions — a terminal full of `main.py` and `notes.txt` is exactly where
people circle things, and `README.md` is not a Moldovan website. Anything with a
space in it is a sentence and is never a link. All of that is a pure function
with no Qt in it (`looks_like_url`), which is why every edge of it is pinned in
the tests.

The overlay stays up with an *Opening it in the browser…* badge until the
browser window takes the focus, exactly as it does for an image search — the
wait here is the browser's cold start, and an overlay that vanished into a
second of nothing would look like a dropped selection.

Reading a 4K screen takes a couple of seconds, so a small badge at the bottom
says *Читаю текст на екрані…* while it works — the overlay is usable the whole
time, and the words simply appear when they are ready.

Whatever was recognised **inside a crop is kept with it**, so *Recent captures →
Read the text* is instant instead of running `tesseract` a second time. Entries
that carry text are marked with a ¶ in the tray.

It is **off until you say so**. The first time an overlay closes you are asked
once, in a notification with two buttons; the welcome window offers it too. The
switch afterwards is Settings → General → *Make the text on screen selectable*,
which also says whether `tesseract` was found and which language packs it has.

```bash
sudo dnf install tesseract tesseract-langpack-ukr tesseract-langpack-eng   # Fedora
sudo apt install tesseract-ocr tesseract-ocr-ukr tesseract-ocr-eng         # Debian
sudo pacman -S tesseract tesseract-data-ukr tesseract-data-eng             # Arch
```

The program shows whichever of those matches the package manager it finds, so
nobody is handed a `dnf` command on Arch.

The language is picked from the interface language plus English, restricted to
the packs that are actually installed — naming a missing one makes `tesseract`
fail outright instead of doing its best. Set `ocr_languages=deu+eng` in
`circle-to-search.conf` to override that.

A **dark desktop is inverted before `tesseract` ever sees it**. Light text on a
dark background is the one polarity it is not built for; it can retry with an
inverted copy, but only after a first pass has already gone badly. Doing it up
front is faster and far more reliable — without it, a dark terminal comes back
as fragments of noise rather than words. The decision is the image's mean
brightness, so a light desktop is passed through untouched, and `tesseract`'s
own retry is left enabled for screens that are a mixture of both.

This is the only part of the program that may be absent: there is no Python
dependency for it, nothing is uploaded, no key is needed, and if `tesseract` is
missing nothing happens except a line in the journal. The "no OCR" rule the rest
of the README describes still holds for the default path — the image search is
still entirely Google's job.

### Learning from the times it was wrong

Calibration fits the thresholds to how you shake. This fits them to what
actually happens on your desktop: after a trigger the daemon sometimes asks
**"Did you mean to open Circle to Search?"**, with a *Yes* and a *No, that was
accidental* button.

* At most **10 questions in the lifetime of the installation**, with at least
  **5 openings in between**. After the tenth it never asks again.
* Only after the *gesture*. Pressing the shortcut is deliberate and is never
  questioned.
* Switchable off in Settings → Detection, which also stops the recording.

While it still has questions left, the daemon sets `collectTraces` and the KWin
script keeps the **last four seconds of pointer movement** in a ring buffer.
When the gesture fires, that movement is handed over as
`GestureTrace("x,y,t;x,y,t;…")` immediately before the trigger. Answering saves
it to `~/.local/share/circle-to-search/traces/` — `misfire-*.json` for a *no*,
`shake-*.json` for a *yes* — in exactly the format `tools/record-trace.sh`
writes, so it goes straight into a test:

```bash
node tests/replay-trace.js ~/.local/share/circle-to-search/traces/misfire-20250107-114530.json
cp ~/.local/share/circle-to-search/traces/misfire-*.json tests/traces/
node tests/test_detection.js          # replays the whole corpus
```

`tests/traces/` is that corpus, and every file in it is replayed on each test
run: `expect: "no-fire"` files must not open the overlay, `expect: "fire"` files
must. Both directions are checked on purpose — tightening a threshold until
nothing misfires is easy and useless if real shakes stop working. See
`tests/traces/README.md`.

Nothing is uploaded. A trace is pointer coordinates and timestamps, it is
written under your own home directory, and once the budget is spent the script
stops recording altogether.

---

## Development

```bash
ruff check src tests               # lint (clean)
python3 tests/test_logic.py        # HiDPI crop math, Lens parsing, raw decode, overlay, lasso
node    tests/test_detection.js    # the real main.js against a fake KWin API
node    tests/replay-trace.js FILE # replay a recorded cursor trace
python3 tests/test_browser_upload.py   # the launcher page, in a real Chromium
python3 tests/test_dbus_surface.py     # the exported D-Bus methods, on a private bus
```

`tests/test_detection.js` loads `kwinscript/contents/code/main.js` unchanged
into a sandbox with a stubbed `workspace`/`QTimer`/`callDBus` and replays
synthetic cursor paths — diagonal shakes, horizontal moves, slow drift, small
wiggles — so gesture tuning can be checked without logging out.
`tests/test_logic.py` runs Qt on the `offscreen` platform, so it needs no
display. `tests/test_browser_upload.py` loads the generated launcher in
Chromium through Playwright, intercepts the request it makes and checks that the
multipart body carries the exact JPEG bytes — the one part that cannot be
verified by reading the code. It skips itself when Playwright or Chromium is
missing. `tests/test_dbus_surface.py` re-executes itself under
`dbus-run-session`, exports the real service on that private bus, calls every
method with `busctl` using the exact signature the KWin script uses, and drives
a stub notification server in a second process to check that an action button
comes back to the callback that showed it. It skips itself when `busctl` or
`dbus-run-session` is missing.

`.github/workflows/ci.yml` runs all of that on every push, plus a `ruff` pass, a
parse of the KWin script, the shell scripts and the shipped JSON, and an RPM
build in a Fedora container whose output is attached to the run. Each suite is
its own job: a Chromium that will not start must not be able to hide a logic
failure. The two suites that skip themselves when their tools are missing —
sensible on a laptop — are made to fail instead when they skip in CI, since a
green tick for a test that never ran is worse than no test.

Run the daemon in the foreground while hacking:

```bash
systemctl --user stop circle-to-search.service
python3 src/circle_to_search/__main__.py --verbose
```

---

## What can break, and how to debug it

### A setting has no effect, or a new feature is missing

KWin keeps a script running until it is explicitly unloaded, so upgrading the
package on disk changes nothing by itself — `reconfigure` only re-reads
*settings*. Check which version is actually in memory:

```bash
journalctl --user -u plasma-kwin_wayland | grep "script started"
#  circle-to-search: KWin script started (v1.4.0)
grep SCRIPT_VERSION kwinscript/contents/code/main.js
```

If they differ, KWin is running old code. `install.sh` now forces a reload
(`unloadScript` over D-Bus, then a plugin off/on toggle) and warns when the
version still does not match. By hand:

```bash
kwriteconfig6 --file kwinrc --group Plugins --key circletosearchEnabled false
qdbus6 org.kde.KWin /KWin reconfigure
kwriteconfig6 --file kwinrc --group Plugins --key circletosearchEnabled true
qdbus6 org.kde.KWin /KWin reconfigure
```

or untick and re-tick **Circle to Search** in System Settings → Window
Management → KWin Scripts.

Detection settings themselves do not need any of that: the running script
re-reads them on `options.configChanged` and at least every four seconds. The
"Detect cursor shake" checkbox is additionally honoured by the daemon — a
gesture arrives as `TriggerShake`, which is ignored while the box is unticked,
whereas the global shortcut sends a plain `Trigger` and always works.

### The KWin script never runs

```bash
journalctl --user -u plasma-kwin_wayland -f | grep -i circle
```

No `circle-to-search: KWin script started` line means KWin did not load it:

* Is it installed? `kpackagetool6 --type=KWin/Script --list | grep circletosearch`,
  or check `~/.local/share/kwin/scripts/circletosearch/metadata.json`.
* Is it enabled? `kreadconfig6 --file kwinrc --group Plugins --key circletosearchEnabled`
  must print `true`. Fix with
  `kwriteconfig6 --file kwinrc --group Plugins --key circletosearchEnabled true`
  followed by `qdbus6 org.kde.KWin /KWin reconfigure`.
* Still nothing → System Settings → Window Management → KWin Scripts, tick
  **Circle to Search**, and as a last resort log out and back in.

**Live-test the detection code** without reinstalling: System Settings → Window
Management → KWin Scripts → *KWin Script Console* (or run `kwin-script-console`
/ `plasma-interactiveconsole --kwin`), paste `main.js`, press Run, and shake.
`print()` output goes to the console and to the KWin journal. Offline,
`node tests/test_detection.js` replays recorded gestures against the same file.

### What counts as a shake

Counting direction reversals is not enough — dragging a window, scribbling,
resizing from a corner and hunting through a menu all reverse direction
constantly. A swing is only accepted when **all** of these hold, and a swing
that fails any of them breaks the chain:

| Test | Why it separates a shake from ordinary work |
|---|---|
| **speed** ≥ `minSpeedPxPerSec` | Drawing and dragging happen at 200–400 px/s; a deliberate shake is a flick at 1500+ |
| **straightness** ≤ `maxCurvaturePct` | A swing is a straight line; a drawn stroke curves |
| **turn-back** within `reversalTolerance` of 180° | Consecutive swings must return along the same line — "both axes changed sign" also accepts a 90° corner |
| **symmetry** | Swings must be of comparable length, not one long and one short |
| **containment** | The whole gesture stays in one place instead of travelling across the screen |
| **diagonal** within `angleTolerance` of 45° | The gesture is deliberately not a horizontal or vertical wiggle |

Tick **Log why each swing was accepted or rejected** in the settings and watch:

```
journalctl --user -u plasma-kwin_wayland -f | grep circle
  swing len=283 speed=2357 curve=1 diag=0 ok
  turn=180 ratio=1 -> reversal
  swing len=96 speed=310 curve=1.8 diag=12 rejected:slow
```

The rejection reason names the test that said no, which is what to loosen.

### Turning a misfire into a test

If it fires while you are drawing — or refuses to fire when you mean it —
record the actual movement and replay it through the same detector:

```bash
./tools/record-trace.sh drawing-false-positive.json   # redo the movement, Ctrl-C
node tests/replay-trace.js drawing-false-positive.json
```

The replay prints the per-swing diagnostics and whether the overlay would have
opened. Settings can be overridden to see what a change would have done to that
exact movement, without touching your session:

```bash
node tests/replay-trace.js drawing-false-positive.json minSpeedPxPerSec=1000
```

A trace is only pointer coordinates and timestamps, so it can go straight into
`tests/` as a regression case. `tests/test_detection.js` already replays
synthetic versions of the movements that used to misfire: wavy drawing,
scribbling, circles, box corners, corner-resizing, window dragging, a flick
across the screen and back, and half a shake followed by a drawn stroke.

### The trigger fires too often / never fires

Raise or lower the thresholds in Settings → Detection:

* **too often** — raise `minSpeedPxPerSec` first (1000–1400); it is the test
  that separates intent from work. Then `minAmplitudePx` (200–250), a lower
  `angleTolerance` (15–20°), `reversals` = 3, or a shorter `windowMs` (400).
* **never** — turn on the swing log above and read the reason. `slow` →
  lower `minSpeedPxPerSec` (300–500) *and* raise `windowMs` (a slower shake
  needs a longer window); `curved` → raise `maxCurvaturePct` to 180;
  `off-diagonal` → raise `angleTolerance` to 40°; `short` → lower
  `minAmplitudePx` to 80–100.
* A HiDPI screen makes swings *shorter in logical pixels* than they feel —
  `minAmplitudePx` is in logical px, so 150 logical px on a 200 %-scaled 4K
  panel is 300 physical px of travel.
* A very fast flick can outrun a 50 ms poll: lower `pollMs` to 30 ms.
* Turn detection off entirely from the tray and use `Meta+Shift+L`.

After any change the daemon calls `reconfigure` itself; if you edited `kwinrc`
by hand, run `qdbus6 org.kde.KWin /KWin reconfigure`.

### D-Bus

```bash
busctl --user list | grep CircleToSearch
busctl --user introspect io.github.fand1l.CircleToSearch /io/github/fand1l/CircleToSearch
busctl --user call io.github.fand1l.CircleToSearch \
    /io/github/fand1l/CircleToSearch \
    io.github.fand1l.CircleToSearch Trigger iis 100 100 ""
```

The last command must open the overlay immediately. If it does but shaking does
not, the problem is in the KWin script; if it does not, look at
`journalctl --user -u circle-to-search.service -e`.

### Screen capture fails

The daemon logs which back end won:
`captured 3840x2160 px via kwin-screenshot2 in 47 ms`.

* `org.kde.KWin.ScreenShot2 … AccessDenied` — KWin only allows callers whose
  desktop file declares `X-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2`
  *and* whose `Exec=` starts with the caller's real binary. For a Python app the
  real binary is the interpreter, so `install.sh` writes the resolved path
  (`readlink -f $(command -v python3)` → `/usr/bin/python3.13`). If Python was
  upgraded since installing, re-run `./install.sh`. Compare the two:

  ```bash
  grep ^Exec ~/.local/share/applications/io.github.fand1l.CircleToSearch.desktop
  readlink -f /proc/$(systemctl --user show -p MainPID --value circle-to-search)/exe
  ```

  Run `kbuildsycoca6` once afterwards so KService sees the new desktop file.
* Falling through to **spectacle** is fine, just slower
  (`sudo dnf install spectacle`).
* Falling through to the **portal** shows a confirmation dialog — that is the
  portal's design, not a bug.
* Wrong-looking crops on a multi-monitor setup mean the fallback back ends had
  to cut one output out of a whole-desktop image; the log line
  `cropping desktop image (…) to (…)` tells you it happened.

### Two panels, or the screenshot looks shifted down

The overlay is showing the screenshot from its own top-left corner, but the
window was left in the *work area* — below the panel. You then see the live
panel at the top and, right under it, the panel that was in the screenshot.

Full screen is asked for four times over, because on Wayland a client can only
*request* it:

1. The window picks its QScreen while it is still virtual and is shown with the
   full-screen state already set. Creating the platform window first and then
   moving it to another screen makes QtWayland rebuild the surface, and the
   pending state does not always survive that — which is how the window ended up
   an ordinary one inside the work area.
2. If the first configure comes back smaller than the output, the daemon drops
   the state and asks again, up to three times, a quarter of a second apart.
3. The KWin script promotes it, and keeps doing so for six seconds rather than
   once at map time, because `windowAdded` fires before the client has committed
   its size. Promotion is tried `fullScreen = true` first, then each window property
independently (a single unsettable one — `noBorder` and `skipSwitcher` are not
writable for every window type — used to abort the whole promotion), then the
frame geometry forced to the output.
4. Because none of that is guaranteed, the script *tells the daemon where the
   window actually landed* (`OverlayGeometry`), repeatedly, and only when it
   changes. A Wayland client cannot ask for its
own position, so this is the only way the overlay can know; with the offset in
hand it draws the screenshot — and computes the crop — in screen coordinates, so
the picture lines up even when the window was left in the work area. The journal
shows both halves:

```
circle-to-search: promoted the overlay (fullScreen=true geometry=3840x2160+0+0)
the overlay is at +0+35 inside its screen instead of the corner; compensating…
```

The second line means the promotion did not fully work but the drawing was
corrected. If the window later does become full screen — the retry usually gets
there — the offset is dropped again on the resize, because a window the size of
the output is at its corner by definition:

```
the overlay came up 1920x1044 instead of 1920x1080, asking for full screen again (1)
the overlay is full screen after 1 retry
```

Those two lines together mean it ended up right. If you see it every time, the usual cause is the previous section:
KWin is still running the old script.

### The overlay appears *under* the panel

That is exactly what the KWin script's second job prevents: it looks for a
window captioned `Circle to Search Overlay` and sets `keepAbove`, `noBorder`
and `fullScreen` on it. If it still ends up behind the panel:

* Look for `circle-to-search: promoted the overlay window` in the KWin journal.
  Missing → the window was not recognised; check the caption in the KWin debug
  console (`qdbus6 org.kde.KWin /KWin org.kde.KWin.showDebugConsole`, Windows
  tab). The caption must match `OVERLAY_WINDOW_TITLE` in
  `src/circle_to_search/__init__.py`.
* Make sure the KWin script is loaded at all (first section) — without it the
  overlay is only a `WindowStaysOnTopHint` window, which Plasma panels can
  cover.
* Try the layer-shell path: Settings → General → *Use layer-shell for the
  overlay*, then `systemctl --user restart circle-to-search.service`. It needs
  `layer-shell-qt` installed (`sudo dnf install layer-shell-qt`); the daemon
  logs `using the layer-shell integration from …` when it takes that route.
* A "Keep Above Others" window rule on another window still wins — check
  System Settings → Window Management → Window Rules.

### The Lens page opens with no image on it

This is the failure that looks like a broken upload but is not one, and it is
why the daemon no longer uploads anything itself.

Since 2025 every Lens endpoint answers an upload with

```
https://www.google.com/search?vsrid=…&gsessionid=…&lsessionid=…&udm=26&vsdim=1000,562
```

`vsdim` echoes the dimensions that were posted, so Google **did** accept the
image. But `gsessionid`/`lsessionid` identify the HTTP session that uploaded.
Upload the same picture through lens.google.com in your browser and you get a
URL of exactly the same shape — the difference is that the cookies for that
session are in the browser. When a daemon uploads, those cookies live in a
`requests.Session` that is discarded seconds later, so the browser opens the
page with the wrong identity and Google renders the Lens interface with an empty
image slot and loading skeletons. No parameter fixes that URL.

So the default back end (**Settings → General → Google endpoint → "Let the
browser upload"**) writes `~/.cache/circle-to-search/lens-XXXX.html`, a
self-contained page holding the JPEG as base64, and opens it. The page fills a
file input through the `DataTransfer` API and submits a normal cross-origin
form, so the browser performs the upload with its own Google session and follows
the redirect to a result page that works — including `authuser=` when you are
signed in. The file is 0600, in a 0700 directory, and is deleted two minutes
later.

If that page stays blank or the browser shows a file-not-found error, your
browser is probably a Flatpak or Snap without access to `~/.cache`; open the
path from the log by hand to confirm, then either use a non-sandboxed browser or
grant it access to that directory.

To go back to uploading from the daemon (useful if Google ever returns
session-independent URLs), pick another entry in the same setting, or compare
what the endpoints answer today:

```bash
circle-to-search --probe-lens ~/Pictures/something.png
```

Each row is marked `usable` (no session id — it will work anywhere) or
`session-bound`, with the URLs printed so you can click them. Pin a winner with
`lens_backend=<variant>` (`lens-ccm`, `lens-crs`, `lens-subb`, `lens-v1`,
`searchbyimage`).

### It worked, then it did not, then it worked again

Everything from the selection onwards now says something when it goes wrong, so
start by finding out which step went quiet:

| What you see | Where it broke |
|---|---|
| No overlay at all when you shake | The KWin script — see the first section |
| Overlay works, no tab opens, a "Could not open the browser" notification | `xdg-open` (no handler, or a sandboxed browser that cannot read `~/.cache`) |
| Overlay works and nothing at all happens | Look at `journalctl --user -u circle-to-search -e`; the last line names the step |
| A local page that says "The upload did not start" | The browser never began the POST — press the button on that page to retry |
| Google's own error, "unusual traffic" or a CAPTCHA | Google is throttling *your browser session*; wait a minute and try again |
| The Lens page with an empty image slot | See the section above |

Two things that were silent before this and no longer are: `xdg-open` failing
(it is now waited on and reported) and a selection that dies half-way leaving
the daemon convinced one is still in progress — that flag is now cleared on the
next trigger instead of ignoring every one of them until a restart.

About rate limits: since the browser performs the upload with your own Google
session, any throttling looks exactly like it does when you use
lens.google.com by hand — Google answers with a "sorry" or "unusual traffic"
page rather than failing silently. Uploading the same region repeatedly in quick
succession is the way to provoke it. The launcher page is kept for ten minutes
under `~/.cache/circle-to-search/`, so a failed attempt can be re-opened and
retried with the exact same image; set `keep_launcher=true` in the config file
to stop the cleanup entirely while debugging.

Other causes, in order of likelihood:

* **The cookie-consent interstitial.** From the EU and Ukraine an anonymous
  upload can be answered with a redirect to `consent.google.com`; opening *that*
  ends on an error page. A `SOCS`/`CONSENT` cookie is sent to skip it, and a
  consent URL that comes back anyway has its `continue=` parameter unwrapped
  (`--verbose` logs `unwrapped an interstitial`).
* **The endpoint changed.** `--verbose` ends with `Google answered 200 without a
  usable result URL`. Open <https://lens.google.com> in Chrome, drop an image on
  it, copy the upload request from the network tab as cURL and compare it with
  `VARIANTS` at the top of `lens.py` — the URLs, the field names and the headers
  are all in that one file.

The same request by hand:

```bash
curl -sS -D- -o /dev/null -X POST \
  "https://lens.google.com/v3/upload?ep=ccm&s=&st=$(date +%s%3N)" \
  -H 'User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36' \
  -H 'Cookie: SOCS=CAESHAgBEhIaAB' \
  -F 'encoded_image=@/tmp/shot.jpg;type=image/jpeg' \
  -F 'processed_image_dimensions=1000,562' | grep -i '^location'
```

Expected: `location: https://lens.google.com/search?p=…`. A `location:` pointing
at `consent.google.com` confirms the first cause above.

* `Timeout` / `no connection` → the daemon raises a notification; nothing is
  ever swallowed silently.
* The upload runs on a worker thread, so a slow network never freezes the
  overlay or the desktop.
* Want to see exactly what was sent? Turn on *Also copy the selection to the
  clipboard* and paste it somewhere — that is the image, lasso mask and all.

### No notifications appear, but the log says they were sent

`notify()` writes the journal line before it touches D-Bus, so "Sending the
selection to Google Lens…" in the log proves nothing about what reached the
screen. The usual cause is an argument-type mismatch: `Notify` is declared
`susssasa{sv}i`, and a call built from plain Python values goes out as
`sisssava{sv}i` — `i` instead of `u` for the id, `av` instead of `as` for the
actions — which a strict server answers with `UnknownMethod`. That is why the
id and the action list are wrapped in `QDBusArgument` with explicit metatypes,
and why `tests/test_dbus_surface.py` drives a real server in another process.

To see what the server actually got:

```bash
busctl --user monitor org.freedesktop.Notifications
busctl --user call org.freedesktop.Notifications /org/freedesktop/Notifications \
    org.freedesktop.Notifications GetCapabilities      # "actions" must be there
```

Without `actions` in the capabilities the misfire question is never shown at
all — deliberately, because a question with no buttons cannot be answered.

### Everything else

```bash
journalctl --user -u circle-to-search.service -f               # the daemon
journalctl --user -u plasma-kwin_wayland -f | grep -i circle   # the KWin half
systemctl --user restart circle-to-search.service
```

Start it in the foreground with `--verbose` to see every decision, including
the measured HiDPI scale and the exact crop box:

```
INFO  eDP-1: logical 2560x1440+0+0, physical 3840x2160, scale 1.500x1.500
INFO  selection 420x260 logical → 630x390 physical at 1200,825
```

---

## Explicitly not used

`pyautogui`, `pynput`, `python-xlib`, `mss`, `pyscreenshot` — all of them rely
on X11 and are broken or silently wrong under Wayland. Screen access goes
through KWin/Spectacle/the portal, and the pointer position comes from the
compositor.

No vision model and no API key either. The image search is Google's job; the
only local recognition is the optional `tesseract` behind **T**, which is off
until you turn it on, adds no Python dependency, and never touches the network.

The only runtime dependencies are `PyQt6`, `Pillow` and `requests` — and
`requests` is unused on the default path, where the browser does the uploading.

## License

MIT — see [LICENSE](../LICENSE).
