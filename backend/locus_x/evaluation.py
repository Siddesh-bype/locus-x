"""Independent truth-based evaluation. No evaluation result is fed back into inference."""

import numpy as np

from .schemas import Estimate, Evaluation, FaultLabel, Point, Tracking
from .tracking import CHI2_95_2D


def _coverage(truth: Point, tracking: Tracking | None) -> bool | None:
    if tracking is None or tracking.position is None or tracking.covariance is None:
        return None
    delta = np.array(tracking.position) - truth
    return bool(delta @ np.linalg.solve(tracking.covariance, delta) <= CHI2_95_2D)


def evaluate(
    truth: Point,
    baseline: Estimate,
    tracking: Tracking,
    labels: list[FaultLabel],
    ai: Estimate | None = None,
    ai_tracking: Tracking | None = None,
) -> Evaluation:
    def error(position):
        return None if position is None else float(np.linalg.norm(np.array(position) - truth))

    return Evaluation(
        ground_truth=truth,
        baseline_error=error(baseline.position),
        proposed_error=error(tracking.position),
        covered_95=_coverage(truth, tracking),
        ai_error=error(ai_tracking.position) if ai_tracking is not None else None,
        ai_covered_95=_coverage(truth, ai_tracking),
        has_nlos=any(label.kind == "nlos" for label in labels),
        has_dropout=any(label.kind in ("outage", "packet_loss") for label in labels),
    )
