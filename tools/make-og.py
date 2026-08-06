#!/usr/bin/env python3
"""Render the site's link preview from the site's own code.

The preview picture has a stroke on it, and there is exactly one description of
how that stroke is drawn — ``docs/STROKE.md``, implemented once in
``src/circle_to_search/stroke.py`` and once in ``site/js/stroke.js``.  Drawing it
a third time here, in some image library, would be a third thing to keep in
step.  So this writes a throwaway page that loads the real ``site/js`` and
``site/css``, photographs it with headless Firefox, and deletes the page again.

    python3 tools/make-og.py

Needs ``firefox`` on PATH.  Nothing is downloaded: the page it renders makes no
network requests, which is the same promise the site itself makes.
"""

from __future__ import annotations

import http.server
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

WIDTH, HEIGHT = 1200, 630

CARDS = {
    "og.png": {
        "lang": "uk",
        "title": "Обвести й&nbsp;знайти",
        "tagline": "Потрясіть мишею. Обведіть будь-що на екрані.<br>Отримайте результат Google&nbsp;Lens.",
        "foot": "KDE&nbsp;Plasma&nbsp;6 на Wayland · MIT · github.com/fand1l/cts_gl",
    },
    "og-en.png": {
        "lang": "en",
        "title": "Circle to Search",
        "tagline": "Shake the mouse. Circle anything on your screen.<br>Get a Google&nbsp;Lens result.",
        "foot": "KDE&nbsp;Plasma&nbsp;6 on Wayland · MIT · github.com/fand1l/cts_gl",
    },
    # A shared installation link should not arrive looking like the home page.
    "og-install.png": {
        "lang": "uk",
        "title": "Три команди",
        "tagline": "<code>git clone</code> · <code>cd</code> · <code>./install.sh</code><br>і вийти з сеансу, щоб KWin завантажив скрипт.",
        "foot": "Потрібні KDE&nbsp;Plasma&nbsp;6 на Wayland і Python&nbsp;3.11 або новіший",
    },
    "og-install-en.png": {
        "lang": "en",
        "title": "Three commands",
        "tagline": "<code>git clone</code> · <code>cd</code> · <code>./install.sh</code><br>then log out, so KWin loads the script.",
        "foot": "Needs KDE&nbsp;Plasma&nbsp;6 on Wayland and Python&nbsp;3.11 or newer",
    },
}

PAGE = """<!doctype html>
<html lang="{lang}" data-theme="dark">
<head>
<meta charset="utf-8">
<title>preview</title>
<link rel="stylesheet" href="css/site.css">
<style>
  html, body {{ margin: 0; padding: 0; }}
  body {{ background: var(--surface-container-lowest); }}
  .card {{
    width: {width}px;
    height: {height}px;
    position: relative;
    overflow: hidden;
    background: var(--surface-container-lowest);
    display: flex;
    flex-direction: column;
    justify-content: center;
    padding: 0 88px;
  }}
  .card canvas {{ position: absolute; inset: 0; width: 100%; height: 100%; }}
  .card__mark {{ position: absolute; top: 56px; left: 88px; display: flex;
                 align-items: center; gap: 16px; color: var(--on-surface-variant); }}
  .card h1 {{ font-size: 78px; line-height: 1.06; margin: 0 0 76px;
              letter-spacing: -0.004386em; max-width: 15ch;
              position: relative; z-index: 1; }}
  .card p {{ margin: 0; color: var(--on-surface-variant); font-size: 30px;
             line-height: 1.45; position: relative; z-index: 3; }}
  .card p code {{ font-family: var(--font-mono); font-size: 26px;
                  background: var(--surface-container-high); border-radius: var(--shape-xs);
                  padding: 2px 8px; color: var(--on-surface); }}
  .card__foot {{ position: absolute; left: 88px; bottom: 56px;
                 font-size: 20px; color: var(--on-surface-variant); }}
</style>
</head>
<body>
<div class="card">
  <canvas id="ink"></canvas>
  <div class="card__mark">
    <img src="icon.svg" width="44" height="44" alt="">
  </div>
  <h1 id="og-title">{title}</h1>
  <p>{tagline}</p>
  <div class="card__foot">{foot}</div>
</div>
<script src="js/ramp.js"></script>
<script src="js/md3.js"></script>
<script src="js/stroke.js"></script>
<script>
  /* The top of the page is the top of the ramp, so the card is seeded blue —
     the same scheme the home page opens with. */
  CTS.md3.apply(CTS.rampColour(0), true);
  var canvas = document.getElementById('ink');
  var stroke = new CTS.Stroke(canvas);
  var here = canvas.getBoundingClientRect();
  /* The words, not the block — the same measurement the hero makes. */
  var range = document.createRange();
  range.selectNodeContents(document.getElementById('og-title'));
  var there = range.getBoundingClientRect();
  var path = CTS.loopAround({{
    x: there.left - here.left,
    y: there.top - here.top,
    width: there.width,
    height: there.height
  }}, 1.7, {{ width: stroke.width, height: stroke.height }});
  /* The picture the loop ends on: the whole ribbon, and the glow over the part
     of it drawn in the last half second. */
  stroke.paintDrawn(path, 2600);
  document.title = 'ready';
</script>
</body>
</html>
"""


def serve(directory: Path) -> tuple[socketserver.TCPServer, int]:
    handler = type(
        "Handler",
        (http.server.SimpleHTTPRequestHandler,),
        {
            "__init__": lambda self, *a, **kw: http.server.SimpleHTTPRequestHandler.__init__(
                self, *a, directory=str(directory), **kw
            ),
            "log_message": lambda *a: None,
        },
    )
    server = socketserver.TCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main() -> int:
    if shutil.which("firefox") is None:
        print("firefox is not on PATH", file=sys.stderr)
        return 1

    server, port = serve(SITE)
    profile = Path(tempfile.mkdtemp(prefix="cts-og-"))
    (profile / "user.js").write_text(
        'user_pref("browser.shell.checkDefaultBrowser", false);\n'
        'user_pref("toolkit.telemetry.enabled", false);\n'
        'user_pref("network.dns.disabled", true);\n'
    )
    try:
        for name, card in CARDS.items():
            page = SITE / f"_og-{card['lang']}.html"
            page.write_text(PAGE.format(width=WIDTH, height=HEIGHT, **card))
            try:
                subprocess.run(
                    [
                        "firefox",
                        "--headless",
                        "--profile",
                        str(profile),
                        "--window-size",
                        f"{WIDTH},{HEIGHT}",
                        "--screenshot",
                        str(SITE / name),
                        f"http://127.0.0.1:{port}/{page.name}",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=180,
                )
            finally:
                page.unlink(missing_ok=True)
            print(f"wrote site/{name}")
    finally:
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
