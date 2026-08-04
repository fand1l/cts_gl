"""Learning from the times the gesture was wrong.

A detector tuned on synthetic paths is tuned on a guess about how people move.
The only movements that matter are the ones that happen on a real desktop, so
after a trigger the daemon occasionally asks whether it was wanted and keeps the
pointer movement that caused it.  A "no" becomes a file that
``tests/replay-trace.js`` can feed back through the very same detector, which
turns "it misfires sometimes" into a case that can be fixed and kept fixed.

Two rules keep it from becoming nagging, and both are the user's to switch off:

* at most :data:`SURVEY_LIMIT` questions **ever**, and
* at least :data:`SURVEY_INTERVAL` openings in between.

The policy is a pure function of a small state record so it can be tested
without Qt, D-Bus or a desktop; the daemon stores that record in its settings.
Traces hold nothing but pointer coordinates and timestamps, they are written
under the user's own data directory, and nothing is ever sent anywhere.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("misfires")

#: Never ask more than this many times in the lifetime of the installation.
SURVEY_LIMIT = 10

#: ...and not until this many further openings have gone by.
SURVEY_INTERVAL = 5

#: Refuse to parse anything longer than this; the script sends ~80 samples.
MAX_SAMPLES = 5000

#: Where saved traces go, under ``~/.local/share``.
TRACE_DIR = Path.home() / ".local/share/circle-to-search/traces"


@dataclass(frozen=True)
class SurveyState:
    """How much of the learning budget is left.

    ``asks`` counts questions *shown*, not questions answered: someone who
    ignores them is telling us something too, and counting only answers would
    mean asking forever.
    """

    enabled: bool = True
    asks: int = 0
    since_ask: int = SURVEY_INTERVAL

    @property
    def remaining(self) -> int:
        return max(0, SURVEY_LIMIT - self.asks)


def still_learning(state: SurveyState) -> bool:
    """Is it worth having the KWin script record movement at all?

    This drives the ``collectTraces`` setting, so once the budget is spent the
    script stops keeping a ring buffer and stops sending anything.
    """
    return state.enabled and state.asks < SURVEY_LIMIT


def should_ask(state: SurveyState) -> bool:
    """Should the opening that just happened be asked about?"""
    return still_learning(state) and state.since_ask >= SURVEY_INTERVAL


def after_opening(state: SurveyState, *, asked: bool) -> SurveyState:
    """Advance the state by one overlay opening."""
    if asked:
        return replace(state, asks=state.asks + 1, since_ask=0)
    return replace(state, since_ask=state.since_ask + 1)


# ------------------------------------------------------------------- traces


def parse_trace(encoded: str) -> list[dict[str, int]]:
    """``"x,y,t;x,y,t;…"`` from the KWin script → the recorded-trace format.

    Malformed points are skipped rather than raising: this arrives over D-Bus
    from another process, and half a trace is still worth having.
    """
    samples: list[dict[str, int]] = []
    for chunk in encoded.split(";"):
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 3:
            continue
        try:
            x, y, t = (int(part) for part in parts)
        except ValueError:
            continue
        samples.append({"x": x, "y": y, "t": t})
        if len(samples) >= MAX_SAMPLES:
            break
    return samples


def save_trace(
    samples: list[dict[str, int]],
    *,
    expect: str,
    description: str = "",
    directory: Path | None = None,
) -> Path | None:
    """Write a trace in the format ``tools/record-trace.sh`` produces.

    ``expect`` is ``"no-fire"`` for a misfire the user disowned and ``"fire"``
    for one they confirmed, which is what lets the corpus guard both directions
    at once.  Returns the path, or ``None`` when nothing could be written —
    losing a trace must never break the capture that produced it.
    """
    if not samples:
        return None
    target = directory if directory is not None else TRACE_DIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    prefix = "misfire" if expect == "no-fire" else "shake"
    path = target / f"{prefix}-{stamp}.json"
    payload = {
        "expect": expect,
        "description": description or f"recorded on {stamp}",
        "samples": samples,
        "notes": [],
    }
    try:
        target.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    except OSError as exc:
        log.warning("could not save the trace to %s: %s", path, exc)
        return None
    log.info("saved a %d-sample trace to %s", len(samples), path)
    return path
