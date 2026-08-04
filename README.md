# Circle to Search for KDE Plasma 6 (Wayland)

Shake the pointer, circle anything on screen with a freehand lasso, get Google
Lens results in your browser. A Linux take on Android's "Circle to Search".

No OCR, no vision model, no API keys: the program only cuts out the region and
uploads it to Google Lens — all the intelligence is Google's.

| | |
|---|---|
| ![shake gesture](docs/screenshot-gesture.png) | ![selection overlay](docs/screenshot-overlay.png) |
| *1. Shake the cursor diagonally* | *2. Circle something on the frozen screen* |
| ![settings](docs/screenshot-settings.png) | ![lens result](docs/screenshot-result.png) |
| *3. Tune the gesture in the tray settings* | *4. Lens opens in your browser* |

> The four images above are placeholders — drop your own PNGs into `docs/`
> with those names and they will show up.

**Target:** Fedora 44, KDE Plasma 6, Wayland session, KWin 6, Python 3.13 +
PyQt6. Developed for a Dell XPS 15 9560 (4K panel, so HiDPI and fractional
scaling are handled explicitly), works with additional monitors at other DPIs.

---

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
   swap the default in Settings).
4. **Lens upload** (`lens.py`) — the daemon does **not** upload. It writes a
   small self-contained HTML page with the JPEG inlined and opens it, and the
   *browser* posts it to `lens.google.com/v3/upload`. That is not a detour:
   Google binds the result page to the session that uploaded, so an upload made
   here produces a URL your browser cannot resolve (see below). As a side
   effect the daemon never makes a network connection at all. Direct uploading
   is still implemented and selectable.

### The cursor glow

The gesture is invisible until it fires, which makes it hard to learn. Once the
script has accepted the first swing it starts sending the pointer position
(`GestureProgress`) and `glow.py` lights the cursor up: a halo whose ring closes
as the remaining swings are made. It is click-through
(`Qt.WindowTransparentForInput` → an empty `wl_surface.set_input_region`) and
never takes focus, so it cannot get in the way of what is underneath.

This is the one place that spends D-Bus calls on cursor movement, so it is
bounded on purpose: nothing is sent until a swing has been accepted, updates are
throttled to 3 px / 40 ms, and a shake is over in well under a second. Turning
the glow off in the settings removes the traffic entirely — the script then
never calls out at all until the gesture fires.

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
`sudo dnf install` anything missing, and then installs **entirely into your home
directory** — nothing else needs root:

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

Flags: `-y` (don't ask before dnf), `--no-deps` (never call dnf), `--force`
(install even if the session checks fail).

Uninstall with `./uninstall.sh` (add `--purge` to drop the settings too).

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
what you want to look up.

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
| `angleTolerance` | `30` | Allowed deviation from 45° |
| `pollMs` | `50` | Cursor polling interval while moving |
| `cooldownMs` | `1500` | Ignore further shakes for this long |
| `minStepPx` | `6` | Movement below this is noise |
| `glow` | `true` | Light the cursor up once the gesture is being recognised |
| `disableInFullscreen` | `true` | Ignore the shake while a full screen window has the focus (games, video). The global shortcut still works. |
| `shortcut` | `Meta+Shift+L` | Fallback global shortcut |

Application-only settings live in
`~/.config/circle-to-search/circle-to-search.conf`:

| Key | Default | Meaning |
|---|---|---|
| `selection_mode` | `lasso` | `lasso` (freehand) or `rectangle`; *Shift* swaps it for one drag |
| `lasso_mask` | `false` | Keep only the inside of the loop and whiten the rest — off, because the loop is there to set the bounds |
| `lens_backend` | `browser` | `browser` (the browser uploads), `auto`, `lens`, `searchbyimage`, or one variant name |
| `max_side` | `1000` | Longest side of the uploaded JPEG |
| `jpeg_quality` | `85` | |
| `copy_to_clipboard` | `false` | Also put the selection on the clipboard |
| `dim_percent` | `40` | Overlay dimming |
| `language` | `auto` | `auto`, `uk` or `en` |
| `use_layer_shell` | `false` | See above |
| `keep_launcher` | `false` | Keep the browser launcher page instead of deleting it after ten minutes |

Lens always receives a rectangle. By default a lasso is uploaded as the plain
crop around the loop — nothing is painted over, exactly like circling something
on Android. `lasso_mask=true` instead whitens everything outside the loop, which
is useful for isolating one object from a busy background; the overlay follows
the setting, so the bright area is always what gets sent.

The UI is available in Ukrainian and English; it follows the system locale
unless you pick one in Settings.

---

## Development

```bash
ruff check src tests               # lint (clean)
python3 tests/test_logic.py        # HiDPI crop math, Lens parsing, raw decode, overlay, lasso
node    tests/test_detection.js    # the real main.js against a fake KWin API
python3 tests/test_browser_upload.py   # the launcher page, in a real Chromium
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
missing.

Run the daemon in the foreground while hacking:

```bash
systemctl --user stop circle-to-search.service
python3 src/circle_to_search/__main__.py --verbose
```

---

## What can break, and how to debug it

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

### The trigger fires too often / never fires

Raise or lower the thresholds in Settings → Detection:

* **too often** — increase `minAmplitudePx` (200–250), lower `angleTolerance`
  (15–20°), increase `reversals` to 3, or shorten `windowMs` to 400.
* **never** — lower `minAmplitudePx` to 80–100, raise `angleTolerance` to 40°,
  lengthen `windowMs` to 900, and make sure the movement really is diagonal:
  both `|dx|` and `|dy|` must exceed `minStepPx` on every sample.
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

## License

MIT — see [LICENSE](LICENSE).
