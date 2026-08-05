<div align="center">

# Circle to Search

**Shake the mouse. Circle anything on your screen. Get a Google Lens result.**

Google's phone gesture, for KDE Plasma 6 on Wayland.

[![CI](https://github.com/fand1l/cts_gl/actions/workflows/ci.yml/badge.svg)](https://github.com/fand1l/cts_gl/actions/workflows/ci.yml)
&nbsp;·&nbsp;
[**🇺🇦 Українською**](README.uk.md)
&nbsp;·&nbsp;
[Technical documentation](docs/TECHNICAL.md)

![Circling something on screen](docs/images/overlay-drawing.png)

</div>

---

## What it does

Shake the pointer diagonally — down-right, up-left, down-right — and the screen
freezes and dims. Draw a loop around whatever you were curious about. It opens in
Google Lens in your browser.

That is the whole idea. Everything below is what happens around it.

* **Circle it, or drag a box.** Freehand by default, like on a phone. Hold
  *Shift* for a rectangle, or click a window to take exactly that window.
* **Nothing is sent until you say so.** The selection waits with handles you can
  drag and buttons that say what each one does. An upload cannot be taken back,
  so there is a moment to check first.
* **Copy or save instead of searching.** The same selection, three other places
  to send it.
* **Select the text on your frozen screen** and copy it, without any of it
  leaving the machine — an optional local text recognition, off until you turn
  it on.
* **Ukrainian and English**, following your system language.

---

## Screenshots

| | |
|---|---|
| ![The moment before you draw](docs/images/overlay-start.png) **Shake, and the screen freezes.** Lasso or rectangle — the chip remembers which. | ![Drawing the loop](docs/images/overlay-drawing.png) **Circle it.** A wide white line with a coloured glow that follows your hand. |
| ![The selection waiting with its buttons](docs/images/overlay-actions.png) **Nothing has been sent yet.** Adjust the edges, then pick what happens to it. | ![Selecting text on the frozen screen](docs/images/overlay-text.png) **Drag across text and you get the text**, not a picture of it. |
| ![The window under the pointer, outlined](docs/images/overlay-window.png) **Click a window** instead of drawing carefully around it. | ![The magnifier while dragging](docs/images/overlay-magnifier.png) **A magnifier while you aim**, so the edge lands where you meant. |

---

## Installing it

You need **KDE Plasma 6 on Wayland** and **Python 3.11 or newer**.

```bash
git clone https://github.com/fand1l/cts_gl.git
cd cts_gl
./install.sh
```

That is all. The installer works out what your distribution is — Fedora, Debian,
Ubuntu, Arch, openSUSE — and asks before installing anything. It never needs
root for the application itself; only your package manager will ask.

It installs four things into your home directory: the daemon, the KWin script
that watches the cursor, a `systemd --user` service so it starts with your
session, and a desktop entry.

**Log out and back in** after installing, so KWin loads the script.

<details>
<summary>Other commands</summary>

```bash
./install.sh reinstall            # replace an existing installation
./install.sh reinstall --config   # …and wipe the settings too (it asks twice)
./uninstall.sh                    # remove it, keep your captures
./uninstall.sh --purge            # remove it and the captures
```

</details>

---

## Using it

### The gesture

Shake the pointer **diagonally** — down-right, up-left, down-right — briskly, in
one place. Roughly 150 pixels a swing, twice, inside about half a second.

If it does not fire, or fires while you are just working, do not go hunting
through settings: **Settings → Calibrate the gesture…** measures a dozen of your
own shakes and writes all the thresholds from them. Above the button, a dot
travels the exact movement your current settings are asking for, so you can see
what they mean.

You can also press **Meta+Shift+L**, or use **Capture now** in the tray menu.

### Once the screen is frozen

| | |
|---|---|
| **Drag** | draw a loop, or hold *Shift* for a rectangle |
| **Click a window** | take exactly that window |
| **Drag across text** | take the text instead of a picture |
| **Esc** or right-click | forget it |

Then the selection waits. Drag the eight handles to resize, the grip in the
middle to move it, the arrow keys to nudge it a pixel at a time — and press
anywhere else to start again.

| button | key | |
|---|---|---|
| **Search** | *Enter* | send it to Google Lens |
| **Copy** | *C* | to the clipboard |
| **Save** | *S* | a PNG in Pictures |
| **Cancel** | *Esc* | forget it |

### Reading the text on screen

Turn on **Make the text on screen selectable** and the words on the frozen screen
become selectable, like text in a browser. Drag across a sentence and press
*Enter* to copy it, or *T* to take everything found.

It runs `tesseract` on your own machine. Nothing is uploaded, no key is needed,
and it is off until you ask for it.

### The tray

The icon dims when shaking will not work, and its tooltip says which of the
several possible reasons it is — including the one that is genuinely hard to
guess, where KWin is still running an older copy of the script than the one on
disk. **Recent captures** keeps the last five so you can search one again
without re-taking it.

---

## Settings

![The settings window](docs/images/settings.png)

Most of it you will never need. The ones worth knowing:

| | |
|---|---|
| **Calibrate the gesture…** | measures your shake and sets every threshold from it |
| **Detect cursor shake** | off leaves the shortcut and the tray working |
| **Selection shape** | lasso or rectangle; *Shift* swaps it for one drag |
| **Check the selection before sending it** | on — off goes back to sending the moment you let go |
| **Magnify while dragging** | the loupe beside the pointer |
| **Make the text on screen selectable** | the optional local text recognition |
| **Open an overlay on every monitor** | so a selection can cross the seam |

The ten raw thresholds are folded away behind **Advanced**, because calibrating
is nearly always the better way round.

---

## If something does not work

**Nothing happens when I shake.** Hover the tray icon — it will tell you which
part is wrong. Most often the answer is *log out and back in*: KWin loads a
script once, at login, and keeps running that copy.

**It opens while I am just working.** Calibrate. Failing that, raise *Minimum
swing speed* under **Advanced** — deliberate shaking is a flick, and drawing and
dragging are slow.

**The overlay is behind my panel.** The KWin script is what lifts it; if that is
not loaded, it cannot. Check *System Settings → Window Management → KWin
Scripts*.

**The Lens page opens empty.** The upload is done by your browser, not by this
program, so a browser that blocks the request is the usual cause. Try it in a
window without content blocking.

Longer answers, and about twenty other failure modes with the reasoning behind
each, are in the [technical documentation](docs/TECHNICAL.md#what-can-break-and-how-to-debug-it).

---

## Privacy, plainly

* The daemon **makes no network connections of its own**. It writes a small
  local page with your selection inlined and hands it to your browser, and the
  browser is what talks to Google. So the session that uploads is your session,
  which is also why the result page opens where you can read it.
* **Text recognition is local.** `tesseract`, on your machine, and only if you
  turn it on.
* **No API keys, no accounts, no telemetry.** Nothing is collected.
* The last five captures are kept in `~/.local/share/circle-to-search/recent`
  so the tray can offer them again. Turn it off, or use `./uninstall.sh --purge`.

---

## Who wrote this

**This program was written by Claude**, Anthropic's AI model, over a series of
sessions — the architecture, the code, the tests, and this documentation.

[@fand1l](https://github.com/fand1l) directed the work: chose what to build,
tested every version on real hardware, and sent back the screenshots and bug
reports that corrected it. Several of the things that make it worth using —
selectable text on the frozen screen, the Android-style stroke, catching the
window that could not be seen — exist because they tried it and said what was
wrong.

It seemed better to say so than to let you assume otherwise.

---

## License

MIT — see [LICENSE](LICENSE).

Not affiliated with Google. "Circle to Search" and "Google Lens" are Google's
names for Google's things; this is an independent program that opens their
public web endpoint in your browser.
