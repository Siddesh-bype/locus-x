"""Range-only localization. No imports from the simulator or evaluation layers."""

from time import perf_counter

import numpy as np
from scipy.optimize import least_squares

from .schemas import Estimate, Measurement


def valid_observations(measurements: list[Measurement]) -> list[Measurement]:
    seen = set()
    valid = []
    for m in measurements:
        if m.anchor_id in seen:
            continue
        if (
            m.observed_range <= 0
            or not np.isfinite([*m.anchor_position, m.observed_range, m.timestamp]).all()
        ):
            continue
        seen.add(m.anchor_id)
        valid.append(m)
    return valid


def solve(
    measurements: list[Measurement],
    *,
    robust: bool = False,
    trust: dict[str, float] | None = None,
    ai_weights: dict[str, float] | None = None,
    bias_corrections: dict[str, float] | None = None,
) -> Estimate:
    start = perf_counter()

    def failure(status, reason):
        return Estimate(status=status, reason=reason, runtime_ms=(perf_counter() - start) * 1000)

    valid = valid_observations(measurements)
    if len(valid) < 3:
        return failure("insufficient", "At least three valid, distinct anchors are required")
    try:
        anchors = np.asarray([m.anchor_position for m in valid], dtype=float)
        corrections = bias_corrections or {}
        ranges = np.asarray([
            max(0.01, m.observed_range - corrections.get(m.anchor_id, 0.0))
            for m in valid
        ])
        singular = np.linalg.svd(anchors - anchors.mean(axis=0), compute_uv=False)
        if singular[0] < 1e-6 or singular[1] / singular[0] < 0.025:
            return failure("poor_geometry", "Anchor layout is collinear or nearly collinear")

        def residual(position):
            return np.linalg.norm(position - anchors, axis=1) - ranges

        # Algebraic initialization uses only anchor coordinates and observed ranges.
        matrix = 2 * (anchors[1:] - anchors[0])
        rhs = (
            ranges[0] ** 2
            - ranges[1:] ** 2
            + (anchors[1:] ** 2).sum(axis=1)
            - (anchors[0] ** 2).sum()
        )
        initial = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
        base = least_squares(residual, initial, max_nfev=100)
        result = base
        weights = np.ones(len(valid))
        if robust:
            raw = residual(base.x)
            scale = max(0.2, 1.4826 * float(np.median(np.abs(raw - np.median(raw)))))
            candidate = least_squares(residual, base.x, loss="huber", f_scale=scale, max_nfev=100)
            magnitude = np.abs(residual(candidate.x))
            instant = np.clip(1 / (1 + (magnitude / (2 * scale)) ** 2), 0.08, 1)
            # Reliability is relative consistency, not a probability of LOS or a learned classifier.
            weights = np.array(
                [
                    0.65 * (trust or {}).get(m.anchor_id, float(score)) + 0.35 * score
                    for m, score in zip(valid, instant, strict=True)
                ]
            )
            if ai_weights is not None:
                # P1: Additive coupling — AI weights replace stat weights directly.
                # Multiplicative coupling (old: weights *= ai_weights) had zero marginal
                # effect when the robust solver already downweighted an anchor, making AI
                # redundant on the measurements it should correct most.
                weights = np.array(
                    [np.clip(ai_weights.get(m.anchor_id, 1.0), 0.0, 1.0) for m in valid]
                )
            result = least_squares(
                lambda p: np.sqrt(weights) * residual(p),
                candidate.x,
                loss="huber",
                f_scale=scale,
                max_nfev=100,
            )
        if not result.success or not np.isfinite(result.x).all():
            return failure("solver_failed", "Least-squares solver did not converge")
        delta = result.x - anchors
        jacobian = delta / np.maximum(np.linalg.norm(delta, axis=1, keepdims=True), 1e-8)
        information = jacobian.T @ (weights[:, None] * jacobian)
        condition = float(np.linalg.cond(information))
        if not np.isfinite(condition) or condition > 1000:
            return failure("poor_geometry", "Range directions do not constrain both position axes")
        raw = residual(result.x)
        variance = max(0.04, float(np.sum(weights * raw**2) / max(1, len(valid) - 2)))
        covariance = np.linalg.inv(information) * variance
        reliability = (
            {m.anchor_id: float(w) for m, w in zip(valid, weights, strict=True)} if robust else {}
        )
        if robust and trust is not None:
            trust.update(reliability)
        return Estimate(
            position=tuple(result.x),
            status="valid",
            reason="Range update accepted",
            residuals={m.anchor_id: float(r) for m, r in zip(valid, raw, strict=True)},
            reliability=reliability,
            covariance=covariance.tolist(),
            condition_number=condition,
            runtime_ms=(perf_counter() - start) * 1000,
        )
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return failure("solver_failed", "Numerical failure while solving ranges")
