"""Turning a few real shakes into thresholds.

Six numbers is far too much to ask anyone to tune by hand, and the values that
work depend on the pointer, the screen and how briskly a particular person moves
their hand.  So instead of guessing: shake the way that feels natural, let the
KWin script measure it, and derive the thresholds from that with a margin.

Everything here is deliberately free of Qt and D-Bus so the arithmetic can be
tested on its own — it is the part that decides whether the gesture will work,
and it should not need a Plasma session to check.

The margins are one-sided on purpose.  A threshold that is slightly too loose
costs a stray overlay that Esc dismisses; one that is slightly too tight makes
the feature look broken, because nothing happens and there is nothing to see.
So the measured values are relaxed, never tightened.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from statistics import median

from .config import DetectionSettings
from .logging_setup import get_logger

log = get_logger("calibration")

#: Below this a "swing" is a twitch, not part of a gesture.
MIN_USEFUL_LENGTH = 40

#: How many swings make a usable sample.  A three-swing shake gives three, so
#: this is about four comfortable shakes.
TARGET_SAMPLES = 12
MINIMUM_SAMPLES = 6

#: Only a turn this sharp counts as "coming back along the same line"; gentler
#: ones are corners, and folding them into the statistic would loosen the
#: turn-back tolerance until corners started to qualify.
TURN_IS_A_REVERSAL = 110


@dataclass(frozen=True)
class Sample:
    """One swing, as measured by the KWin script."""

    length: int
    speed: int
    curvature_pct: int
    diagonal_deg: int
    #: Angle against the previous swing, or -1 when there was none.
    turn_deg: int
    duration_ms: int

    @property
    def is_reversal(self) -> bool:
        return self.turn_deg >= TURN_IS_A_REVERSAL


def _clamp(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, round(value))))


def _high_water(values: list[int], fraction: float = 0.8) -> float:
    """The value that ``fraction`` of the samples stay under.

    Not a real percentile — with a dozen samples that would be pretending to a
    precision we do not have.  It is "most of them, but do not let one wild
    swing set the threshold".
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return float(ordered[index])


def usable(samples: list[Sample]) -> list[Sample]:
    """Drop the twitches; keep what looks like an intended swing."""
    return [sample for sample in samples if sample.length >= MIN_USEFUL_LENGTH]


def suggest(samples: list[Sample], current: DetectionSettings) -> DetectionSettings:
    """Derive thresholds from measured swings.

    Returns ``current`` unchanged when there is not enough to go on, so a
    half-finished calibration can never make things worse.
    """
    kept = usable(samples)
    if len(kept) < MINIMUM_SAMPLES:
        log.info("only %d usable samples, keeping the current settings", len(kept))
        return current

    lengths = [sample.length for sample in kept]
    speeds = [sample.speed for sample in kept]
    curvatures = [sample.curvature_pct for sample in kept]
    diagonals = [sample.diagonal_deg for sample in kept]
    durations = [sample.duration_ms for sample in kept]
    turns = [180 - sample.turn_deg for sample in kept if sample.is_reversal]

    typical_length = median(lengths)
    typical_speed = median(speeds)
    typical_duration = median(durations)

    # A swing has to be clearly shorter and slower than the user's own to be
    # rejected, so both get a generous discount.
    amplitude = _clamp(typical_length * 0.6, 20, 1000)
    speed = _clamp(typical_speed * 0.55, 100, 5000)

    # Curvature and the angles go the other way: allow a bit more than what was
    # measured, because the next shake will not be identical to these.
    curvature = _clamp(_high_water(curvatures) * 1.2 + 10, 110, 400)
    angle = _clamp(_high_water(diagonals) + 10, 10, 44)
    turn_back = _clamp((_high_water(turns) if turns else 25) + 15, 15, 90)

    # The window has to hold the whole gesture: one swing per reversal, plus the
    # first one, plus room for a slower repeat.
    window = _clamp(typical_duration * (current.reversals + 1) * 1.8, 300, 3000)

    suggestion = replace(
        current,
        minAmplitudePx=amplitude,
        minSpeedPxPerSec=speed,
        maxCurvaturePct=curvature,
        angleTolerance=angle,
        reversalTolerance=turn_back,
        windowMs=window,
    )
    log.info(
        "calibrated from %d swings: median length %.0f px, speed %.0f px/s, "
        "duration %.0f ms → amplitude %d, speed %d, curvature %d%%, angle %d°, "
        "turn %d°, window %d ms",
        len(kept),
        typical_length,
        typical_speed,
        typical_duration,
        amplitude,
        speed,
        curvature,
        angle,
        turn_back,
        window,
    )
    return suggestion


def describe_changes(
    current: DetectionSettings, suggestion: DetectionSettings
) -> list[tuple[str, str, str]]:
    """``(key, before, after)`` for everything the calibration would change."""
    interesting = (
        "minAmplitudePx",
        "minSpeedPxPerSec",
        "maxCurvaturePct",
        "angleTolerance",
        "reversalTolerance",
        "windowMs",
    )
    rows: list[tuple[str, str, str]] = []
    for key in interesting:
        before = getattr(current, key)
        after = getattr(suggestion, key)
        if before != after:
            rows.append((key, str(before), str(after)))
    return rows
