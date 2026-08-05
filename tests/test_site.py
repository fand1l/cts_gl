"""The website says the same numbers the program does.

Run with::

    python3 tests/test_site.py

``site/js/md3.js`` is a port of :mod:`circle_to_search.material` and
``site/js/ramp.js`` and ``site/js/stroke.js`` are ports of
:mod:`circle_to_search.stroke`.  A port is a copy, and a copy drifts: somebody
tunes ``GLOW_RADIUS`` on real hardware, the site keeps the old one, and the page
that claims to draw by the same rule quietly stops doing it.

So the JavaScript is *run* — under node, which CI already has for the KWin
script — and its answers are compared with Python's.  Every colour role at six
seeds in both schemes, the ramp at a spread of heights, and the constants at the
top of the stroke.  The CSS is read too, because the colours it ships as
defaults are the same function's output and have to still be.

Needs ``node`` on PATH; skips itself loudly without it, which is a failure in CI
and a shrug on a machine that has no node.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import fields
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
sys.path.insert(0, str(ROOT / "src"))

from circle_to_search import material, stroke  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name} {detail}")
    if not condition:
        failures.append(name)


if shutil.which("node") is None:
    print("SKIP  node is not on PATH, so the JavaScript ports cannot be run")
    sys.exit(0)


# --- ask the JavaScript ----------------------------------------------------

BRIDGE = r"""
global.window = global;
global.document = {
  documentElement: { style: { setProperty() {} } },
  createElement() {
    return { getContext() { return {
      createRadialGradient() { return { addColorStop() {} }; },
      beginPath() {}, arc() {}, fill() {}, scale() {},
    }; } };
  },
};
require(SITE + '/js/ramp.js');
require(SITE + '/js/md3.js');
require(SITE + '/js/stroke.js');
const md3 = CTS.md3;
const out = { schemes: {}, ramp: [], blob: {} };
for (const [name, seed, dark] of SEEDS) {
  const scheme = md3.schemeFor(seed, dark);
  const roles = {};
  for (const role of md3.ROLE_NAMES) roles[role] = md3.hex(scheme[role]);
  out.schemes[name] = roles;
}
for (const [y, height] of HEIGHTS) out.ramp.push(md3.hex(CTS.glowColour(y, height)));
out.constants = {
  LINE_WIDTH: CTS.Stroke.LINE_WIDTH,
  GLOW_RADIUS: CTS.Stroke.GLOW_RADIUS,
  TRAIL_LIFETIME_MS: CTS.Stroke.TRAIL_LIFETIME_MS,
};
/* Every pair the pages actually put together, at every point down the scroll
   ramp, in both schemes.  The page regrows its whole scheme from where you are
   on it, so "the colours are readable" is a claim about two hundred schemes,
   not about one. */
out.contrast = 21;
out.contrastWhere = '';
for (const dark of [false, true]) {
  for (let i = 0; i <= 200; i++) {
    const scheme = md3.schemeFor(CTS.rampColour(i / 200), dark);
    for (const pair of PAIRS) {
      const ratio = md3.contrast(scheme[pair[0]], scheme[pair[1]]);
      if (ratio < out.contrast) {
        out.contrast = ratio;
        out.contrastWhere = pair[1] + ' on ' + pair[0] +
          (dark ? ' (dark) ' : ' (light) ') + 'at ' + Math.round(i / 2) + '% down';
      }
    }
  }
}
/* The trail's arithmetic, on the same numbers Python is asked for. */
const trail = new CTS.Trail();
for (let i = 0; i < 45; i++) trail.add(i * 10, i * 10, [255, 0, 0], i * 12);
const last = 44 * 12;
out.trail = {
  kept: trail.blobs.length,
  drawn: trail.alive(45 * 12).length,
  left: trail.alive(45 * 12).map((item) => Math.round(item.left * 1000) / 1000),
  /* Exactly on the gap, one under it and one over it — the boundary is where a
     1000 // 90 that became 11.11 would show up. */
  gap: [10, 11, 12].map((delta) => {
    const probe = new CTS.Trail();
    probe.add(0, 0, [255, 0, 0], last);
    return probe.add(1, 1, [255, 0, 0], last + delta);
  }),
};
console.log(JSON.stringify(out));
"""

SEEDS = [
    ("blue-light", (0x42, 0x85, 0xF4), False),
    ("blue-dark", (0x42, 0x85, 0xF4), True),
    ("red-light", (0xEA, 0x43, 0x35), False),
    ("red-dark", (0xEA, 0x43, 0x35), True),
    ("yellow-light", (0xFB, 0xBC, 0x05), False),
    ("yellow-dark", (0xFB, 0xBC, 0x05), True),
    ("green-light", (0x34, 0xA8, 0x53), False),
    ("green-dark", (0x34, 0xA8, 0x53), True),
    ("breeze-light", material.FALLBACK_SEED, False),
    ("breeze-dark", material.FALLBACK_SEED, True),
    # A seed with no hue worth reading, which is the one place material.py
    # departs from the specification's arithmetic.
    ("grey-light", (0x80, 0x80, 0x80), False),
    ("grey-dark", (0x80, 0x80, 0x80), True),
]

HEIGHTS = [(y, 1000.0) for y in (0, 100, 200, 400, 500, 620, 800, 999, 1000)] + [
    (-50, 1000.0),
    (2000, 1000.0),
    (10, 0.0),
]

#: The colour pairs the pages really put together, read off site.css.  A pair
#: that is not here is a pair nothing on the site draws.
PAIRS = [
    ("surface", "on-surface"),
    ("surface", "on-surface-variant"),
    ("surface", "primary"),
    ("surface-container-low", "on-surface"),
    ("surface-container-low", "on-surface-variant"),
    ("surface-container-low", "primary"),
    ("surface-container-high", "on-surface"),
    ("surface-container-high", "on-surface-variant"),
    ("surface-container-high", "primary"),
    ("surface-container-highest", "on-surface"),
    ("surface-container-highest", "primary"),
    ("surface-container-highest", "error"),
    ("primary", "on-primary"),
    ("secondary-container", "on-secondary-container"),
    ("primary-container", "on-primary-container"),
]

with tempfile.TemporaryDirectory() as workdir:
    script = Path(workdir) / "ask.js"
    script.write_text(
        f"const SITE = {json.dumps(str(SITE))};\n"
        f"const SEEDS = {json.dumps(SEEDS)};\n"
        f"const HEIGHTS = {json.dumps(HEIGHTS)};\n"
        f"const PAIRS = {json.dumps(PAIRS)};\n" + BRIDGE
    )
    answer = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=120
    )
if answer.returncode != 0:
    print("FAIL  the site's JavaScript did not run")
    print(answer.stderr.strip()[:4000])
    sys.exit(1)
js = json.loads(answer.stdout)


def hexof(colour) -> str:
    return f"#{colour.red():02x}{colour.green():02x}{colour.blue():02x}"


# --- every colour role, both schemes, six seeds ----------------------------

ROLES = [field.name for field in fields(material.Scheme) if field.name != "dark"]
mismatched: list[str] = []
for name, seed, dark in SEEDS:
    scheme = material.scheme_for(seed, dark=dark)
    for role in ROLES:
        want = hexof(getattr(scheme, role))
        got = js["schemes"][name][role.replace("_", "-")]
        if want != got:
            mismatched.append(f"{name} {role}: material.py {want}, md3.js {got}")

check(
    f"md3.js gives material.py's own answer for all {len(ROLES)} roles"
    f" across {len(SEEDS)} schemes",
    not mismatched,
    "" if not mismatched else "\n      " + "\n      ".join(mismatched[:10]),
)

# --- the ramp --------------------------------------------------------------

off_ramp = []
for y, height in HEIGHTS:
    want = hexof(stroke.glow_colour(y, height))
    got = js["ramp"][HEIGHTS.index((y, height))]
    if want != got:
        off_ramp.append(f"y={y} of {height}: stroke.py {want}, ramp.js {got}")

check(
    "ramp.js reads the same colour off a height as stroke.py, clamps included",
    not off_ramp,
    "" if not off_ramp else "\n      " + "\n      ".join(off_ramp),
)

# --- the constants ---------------------------------------------------------

check(
    "the line is stroke.py's line",
    js["constants"]["LINE_WIDTH"] == stroke.LINE_WIDTH,
    f'{js["constants"]["LINE_WIDTH"]} vs {stroke.LINE_WIDTH}',
)
check(
    "the glow reaches as far as stroke.py's",
    js["constants"]["GLOW_RADIUS"] == stroke.GLOW_RADIUS,
    f'{js["constants"]["GLOW_RADIUS"]} vs {stroke.GLOW_RADIUS}',
)
check(
    "and a remembered position lasts as long",
    js["constants"]["TRAIL_LIFETIME_MS"] == stroke.TRAIL_LIFETIME_MS,
    f'{js["constants"]["TRAIL_LIFETIME_MS"]} vs {stroke.TRAIL_LIFETIME_MS}',
)

# --- the trail, against the Python one on the same input -------------------

reference = stroke.Trail()
from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402

red = QColor(255, 0, 0)
for index in range(45):
    reference.add(QPoint(index * 10, index * 10), red, index * 12)

check(
    "the trail keeps what Python's keeps",
    js["trail"]["kept"] == len(reference),
    f'{js["trail"]["kept"]} vs {len(reference)}',
)
alive = reference.alive(45 * 12)
check(
    "and thins to the same handful when it draws",
    js["trail"]["drawn"] == len(alive),
    f'{js["trail"]["drawn"]} vs {len(alive)}',
)
check(
    "with the same amount left in each",
    js["trail"]["left"] == [round(left, 3) for _blob, left in alive],
    f'{js["trail"]["left"]} vs {[round(left, 3) for _blob, left in alive]}',
)

def python_gap(delta: int) -> bool:
    probe = stroke.Trail()
    probe.add(QPoint(0, 0), red, 44 * 12)
    return probe.add(QPoint(1, 1), red, 44 * 12 + delta)


wanted_gap = [python_gap(delta) for delta in (10, 11, 12)]
check(
    "and takes or refuses a sample at the same 11 ms boundary stroke.py uses",
    js["trail"]["gap"] == wanted_gap,
    f'{js["trail"]["gap"]} vs {wanted_gap}',
)

# --- every pair is readable, everywhere down the page ----------------------

# The scheme is regrown from where the reader is on the page, so this is a
# claim about the 402 schemes the ramp passes through rather than about one.
check(
    "every X/on-X pair the site draws is at least AA, all the way down, both themes",
    js["contrast"] >= 4.5,
    f'worst {js["contrast"]:.2f}:1 — {js["contrastWhere"]}',
)

# --- the CSS ships the same scheme -----------------------------------------

css = (SITE / "css" / "site.css").read_text(encoding="utf-8")
for scheme_name, dark in (("blue-light", False), ("blue-dark", True)):
    scheme = material.scheme_for((0x42, 0x85, 0xF4), dark=dark)
    missing = [
        f"--{role.replace('_', '-')}: {hexof(getattr(scheme, role))};"
        for role in ROLES
        if f"--{role.replace('_', '-')}: {hexof(getattr(scheme, role))};" not in css
    ]
    check(
        f"site.css carries the {scheme_name} scheme material.py generates",
        not missing,
        "" if not missing else f"missing {missing[:4]}",
    )

# The dark scheme has to be there twice: once behind the switch, once behind
# the desktop's own preference, or one of the two ways in is a white page.
check(
    "and carries the dark one twice — for the switch and for the desktop",
    css.count("--surface: #121319;") == 2,
    f'found {css.count("--surface: #121319;")}',
)

# --- the type scale --------------------------------------------------------

off_scale = []
for role, (size, weight, tracking) in material._TYPE_SCALE.items():
    ratio = size / 14.0
    wanted = f"calc({ratio:.6f} * var(--type-anchor))".rstrip("0").rstrip(".")
    # A role's name also appears in the grouped selector that zeroes the
    # margins, so every block bearing it is read, not the first one.
    import re as _re

    bodies = _re.findall(rf"\.{role} \{{(.*?)\}}", css, _re.S)
    if not bodies:
        off_scale.append(f"{role} has no rule")
        continue
    body = "\n".join(bodies)
    if size == 14 and "var(--type-anchor)" in body:
        pass  # the anchor itself is written without the multiplication
    elif f"{ratio:.6f}".rstrip("0").rstrip(".") not in body:
        off_scale.append(f"{role} is not {ratio:.6f} of the anchor")
    if f"font-weight: {weight}" not in body:
        off_scale.append(f"{role} is not weight {weight}")
    if tracking and f"{tracking / size:.6f}".rstrip("0").rstrip(".") not in body:
        off_scale.append(f"{role} tracking is not {tracking}px at {size}px")

check("site.css puts every role at material.py's ratio, weight and tracking",
      not off_scale, "" if not off_scale else "\n      " + "\n      ".join(off_scale))

# --- the shape and state tokens -------------------------------------------

for token, value in (
    ("--shape-xs", material.SHAPE_EXTRA_SMALL),
    ("--shape-sm", material.SHAPE_SMALL),
    ("--shape-md", material.SHAPE_MEDIUM),
    ("--shape-lg", material.SHAPE_LARGE),
    ("--shape-xl", material.SHAPE_EXTRA_LARGE),
):
    check(f"{token} is {value}px", f"{token}: {value}px;" in css)

check("the hover film is the specification's 0.08",
      f"--state-hover: {material.STATE_HOVER};" in css)
check("and the pressed one 0.12", "--state-pressed: 0.12;" in css)

# --- the gesture the site draws is the gesture the installer asks for ------

kwinrc_defaults = (ROOT / "install.sh").read_text(encoding="utf-8")
gesture_js = (SITE / "js" / "gesture.js").read_text(encoding="utf-8")
from circle_to_search.config import DetectionSettings  # noqa: E402

defaults = DetectionSettings()
for name, value in (
    ("REVERSALS", defaults.reversals),
    ("AMPLITUDE_PX", defaults.minAmplitudePx),
    ("SPEED_PX_PER_SEC", defaults.minSpeedPxPerSec),
    ("ANGLE_TOLERANCE_DEG", defaults.angleTolerance),
):
    check(f"gesture.js draws with {name} = {value}", f"var {name} = {value};" in gesture_js)
    check(f"and install.sh seeds the same {name}",
          f"{value}" in kwinrc_defaults)

print()
if failures:
    print("FAILURES:", ", ".join(failures))
    sys.exit(1)
print("all good")
