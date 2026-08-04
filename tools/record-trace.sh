#!/usr/bin/env bash
#
# Record real cursor movement so a wrong decision can be replayed offline.
#
# When the overlay opens while you were only drawing — or refuses to open when
# you meant it — run this, redo the movement, and press Ctrl-C.  You get a JSON
# file that tests/replay-trace.js feeds through the very same detector, which
# turns "it misfires sometimes" into a reproducible case.
#
#   ./tools/record-trace.sh drawing-false-positive.json
#   node tests/replay-trace.js drawing-false-positive.json
#
# The trace holds nothing but pointer coordinates and timestamps.
#
set -euo pipefail

OUTPUT="${1:-cursor-trace.json}"
SCRIPT_ID="circletosearch"

GREEN=$'\033[32m'; YELLOW=$'\033[33m'; BOLD=$'\033[1m'; RESET=$'\033[0m'
info() { printf '%s==>%s %s\n' "$GREEN$BOLD" "$RESET" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YELLOW$BOLD" "$RESET" "$*" >&2; }

if ! command -v kwriteconfig6 >/dev/null 2>&1; then
    warn "kwriteconfig6 is missing; is this a Plasma 6 session?"
    exit 1
fi

reconfigure() {
    if command -v qdbus6 >/dev/null 2>&1; then
        qdbus6 org.kde.KWin /KWin reconfigure >/dev/null 2>&1 || true
    elif command -v busctl >/dev/null 2>&1; then
        busctl --user call org.kde.KWin /KWin org.kde.KWin reconfigure >/dev/null 2>&1 || true
    fi
}

set_trace() {
    kwriteconfig6 --file kwinrc --group "Script-$SCRIPT_ID" --key trace --type bool "$1"
    kwriteconfig6 --file kwinrc --group "Script-$SCRIPT_ID" --key debug --type bool "$1"
    reconfigure
}

cleanup() {
    set_trace false
    info "Recording stopped."
}
trap cleanup EXIT

set_trace true
# The script re-reads its settings within a few seconds.
sleep 5

RAW="$(mktemp)"
trap 'rm -f "$RAW"; cleanup' EXIT

info "Recording. Reproduce the movement now, then press Ctrl-C."
journalctl --user -u plasma-kwin_wayland -f --since "now" -o cat 2>/dev/null \
    | grep --line-buffered -E "CTS-TRACE|circle-to-search: (swing|turn)" > "$RAW" || true

python3 - "$RAW" "$OUTPUT" <<'PY'
import json
import re
import sys
from pathlib import Path

raw = Path(sys.argv[1]).read_text(errors="replace").splitlines()
samples = []
notes = []
for line in raw:
    match = re.search(r"CTS-TRACE\s+(-?\d+)\s+(-?\d+)\s+(\d+)", line)
    if match:
        x, y, t = (int(value) for value in match.groups())
        samples.append({"x": x, "y": y, "t": t})
    elif "circle-to-search:" in line:
        notes.append(line.split("circle-to-search:", 1)[1].strip())

if not samples:
    print("No samples were captured.", file=sys.stderr)
    print("Check that the KWin script is loaded:", file=sys.stderr)
    print("  journalctl --user -u plasma-kwin_wayland -n 50 | grep -i circle", file=sys.stderr)
    raise SystemExit(1)

# Timestamps come from Date.now(); make them relative so the file is portable.
start = samples[0]["t"]
for sample in samples:
    sample["t"] -= start

Path(sys.argv[2]).write_text(
    json.dumps({"samples": samples, "notes": notes[-40:]}, indent=1) + "\n"
)
span = samples[-1]["t"] / 1000.0
print(f"{len(samples)} samples over {span:.1f} s written to {sys.argv[2]}")
PY

info "Replay it with:  node tests/replay-trace.js $OUTPUT"
