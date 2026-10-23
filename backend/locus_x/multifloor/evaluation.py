"""Truth-based multi-floor metrics. No evaluation result reaches an estimator.

The metrics below are the only place where a route's truth and a tracker's output meet.
They are pure: :func:`evaluate_multifloor` reads a :class:`~.route.RouteState`, a
:class:`~.tracking.Tracking3D` and a :class:`~.positioning.FloorScores` and returns one
frame's numbers; nothing is written back into inference.

Honesty rules:

* A withheld position or elevation produces no error. A cold horizontal-only fix is not
  scored as a horizontal success with a invented vertical value.
* ``floor_correct`` compares the reported storey with the truth storey by identity. It is
  ``None`` where the question has no honest answer: mid-traverse (the route reports no
  storey) and in route-assisted mode, where the storey is the configured route rather than
  an inference. Route-assisted identity is never scored as a success.
* ``covered_horizontal_95`` is a horizontal-only chi-square test: the two-dimensional
  horizontal error against the 2x2 horizontal covariance block. The legacy ``covered_95``
  compares a three-dimensional position with a two-dimensional covariance and is not
  reused here.

All numbers are synthetic. The slab and NLOS terms are simulator assumptions, not a
calibrated radio model.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from math import hypot, isfinite
from typing import Literal

import numpy as np

from ..tracking import CHI2_95_2D
from .positioning import FloorScores
from .route import Phase, RouteState
from .tracking import Tracking3D

HORIZONTAL_GATE_95 = CHI2_95_2D
"""95% chi-square bound for two degrees of freedom (5.991...)."""

FloorSource = Literal["configured", "route_assisted", "inferred"]
"""Where the reported storey came from."""


@dataclass(frozen=True, slots=True)
class MultifloorEvaluation:
    """One frame's truth-versus-tracking comparison."""

    timestamp: float
    phase: Phase
    horizontal_error: float | None
    vertical_error: float | None
    floor_correct: bool | None
    covered_horizontal_95: bool | None
    updated: bool
    ambiguous: bool
    floor_id: str | None
    floor_source: FloorSource
    hypothesis_floor: str | None
    hypothesis_ambiguous: bool | None


@dataclass(frozen=True, slots=True)
class FrameRecord:
    """The minimum a sampled frame must expose to score transitions and recovery."""

    timestamp: float
    floor_id: str | None
    updated: bool
    ambiguous: bool


def _horizontal_error(truth: RouteState, tracking: Tracking3D) -> float | None:
    if tracking.position is None:
        return None
    return float(hypot(tracking.position[0] - truth.x, tracking.position[1] - truth.y))


def _covered_horizontal_95(truth: RouteState, tracking: Tracking3D) -> bool | None:
    """Horizontal Mahalanobis test using only the 2x2 covariance block."""
    if tracking.position is None or tracking.covariance is None:
        return None
    covariance = np.asarray(tracking.covariance, dtype=float)[:2, :2]
    delta = np.asarray(
        (tracking.position[0] - truth.x, tracking.position[1] - truth.y), dtype=float
    )
    try:
        distance = float(delta @ np.linalg.solve(covariance, delta))
    except np.linalg.LinAlgError:
        return None
    if not np.isfinite(distance):
        return None
    return bool(distance <= HORIZONTAL_GATE_95)


def _floor_correct(truth: RouteState, tracking: Tracking3D) -> bool | None:
    if tracking.floor_state.source == "route_assisted":
        return None
    if truth.floor_id is None:
        return None
    return tracking.floor_state.floor_id == truth.floor_id


def evaluate_multifloor(
    truth: RouteState,
    tracking: Tracking3D,
    scores: FloorScores | None,
    vertical_truth: float,
) -> MultifloorEvaluation:
    """Compare one tracker frame with the route truth that produced its observations.

    ``vertical_truth`` is the device's true elevation for the frame -- the benchmark
    passes ``truth.z``. It is explicit rather than read from ``truth`` so a caller can
    evaluate against a floor datum instead, and so the vertical error cannot silently
    start comparing two different reference points.

    ``floor_correct`` is identity equality between the reported storey and the truth
    storey; a withheld storey counts as a miss, while ``ambiguous`` reports trust
    separately. It is ``None`` mid-traverse and in route-assisted mode (see the module
    docstring), never a manufactured success.

    ``scores`` is optional and only echoed for auditing (the leading hypothesis and its
    ambiguity flag). It never changes an error, the floor verdict or the tracker state.
    """
    return MultifloorEvaluation(
        timestamp=truth.timestamp,
        phase=truth.phase,
        horizontal_error=_horizontal_error(truth, tracking),
        vertical_error=None if tracking.z is None else float(abs(tracking.z - vertical_truth)),
        floor_correct=_floor_correct(truth, tracking),
        covered_horizontal_95=_covered_horizontal_95(truth, tracking),
        updated=tracking.updated,
        ambiguous=tracking.floor_state.ambiguous,
        floor_id=tracking.floor_state.floor_id,
        floor_source=tracking.floor_state.source,
        hypothesis_floor=None if scores is None else scores.best_floor,
        hypothesis_ambiguous=None if scores is None else scores.ambiguous,
    )


def summarize_errors(values: Iterable[float | None]) -> dict[str, float | int | None]:
    """RMSE, median and P95 over the finite values in ``values``.

    Missing (withheld) values are skipped rather than imputed, so ``count`` is the number
    of frames that actually contributed. An empty stream yields ``None`` statistics; no
    ``inf`` or ``nan`` ever leaves this function, so results serialise with
    ``allow_nan=False``.
    """
    finite = [float(value) for value in values if value is not None and isfinite(float(value))]
    if not finite:
        return {"count": 0, "rmse": None, "median": None, "p95": None}
    errors = np.asarray(finite, dtype=float)
    return {
        "count": len(finite),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "median": float(np.median(errors)),
        "p95": float(np.percentile(errors, 95)),
    }


def transition_resolutions(
    frames: Sequence[FrameRecord],
    transitions: Sequence[tuple[float, str]],
    *,
    tolerance: float = 5.0,
) -> list[float | None]:
    """Seconds from each transition end to the first fresh, unambiguous target-floor frame.

    ``transitions`` holds ``(end_timestamp, target_floor_id)`` in the order to report, so
    the caller can zip the result with its own event list. A frame counts only when it is
    inside ``[end, end + tolerance]``, reports the target floor, was accepted by the filter
    (``updated``) and is not flagged ambiguous: a held or stale storey is not a successful
    transition. ``None`` means no such frame occurred, and the caller decides how to
    aggregate an unresolved transition.
    """
    resolutions: list[float | None] = []
    for end, target in transitions:
        hit = next(
            (
                frame.timestamp
                for frame in frames
                if end <= frame.timestamp <= end + tolerance
                and frame.floor_id == target
                and frame.updated
                and not frame.ambiguous
            ),
            None,
        )
        resolutions.append(None if hit is None else float(hit - end))
    return resolutions


def recovery_delays(
    frames: Sequence[FrameRecord], outage_ends: Sequence[float]
) -> list[float | None]:
    """Seconds from each outage end to the first accepted update at or after it.

    Recovery is the return of a fresh measurement update, not of a particular floor: the
    timed scenario outage removes four perimeter anchors per floor, so the filter may never
    stop updating and the honest delay is then zero. ``outage_ends`` holds the timestamp of
    the first frame after each outage (the run of outage-labelled frames is what defines
    it). ``None`` means the sampled stream ended before an accepted update.
    """
    delays: list[float | None] = []
    for end in outage_ends:
        hit = next(
            (frame.timestamp for frame in frames if frame.timestamp >= end and frame.updated),
            None,
        )
        delays.append(None if hit is None else float(hit - end))
    return delays


__all__ = [
    "HORIZONTAL_GATE_95",
    "FloorSource",
    "FrameRecord",
    "MultifloorEvaluation",
    "evaluate_multifloor",
    "recovery_delays",
    "summarize_errors",
    "transition_resolutions",
]
