"""A small, fixed set of range-only candidates for offline ablation.

No truth, scenario settings, wall geometry or fault labels enter these functions.
"""

import importlib.util
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.optimize import least_squares

from backend.locus_x.estimators import valid_observations
from backend.locus_x.schemas import Estimate


def reference_module(name):
    path = Path(__file__).resolve().parents[2] / "artifacts/nlos-audit/reference" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"backend.locus_x._original_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


original = reference_module("estimators")
OriginalTracker = reference_module("tracking").MotionTracker


def candidate_solve(measurements, method, trust=None):
    if method == "legacy":
        return original.solve(measurements, robust=True, trust=trust)
    start = perf_counter()
    base = original.solve(measurements)
    if base.position is None:
        return base
    valid = valid_observations(measurements)
    anchors = np.array([m.anchor_position for m in valid])
    ranges = np.array([m.observed_range for m in valid])
    n = len(valid)

    def residual(p):
        return np.linalg.norm(p - anchors, axis=1) - ranges

    raw = residual(base.position)
    scale = max(0.2, 1.4826 * float(np.median(np.abs(raw - np.median(raw)))))
    position = np.array(base.position)
    covariance = np.array(base.covariance)
    condition = base.condition_number
    weights = np.ones(n)
    reason = "Unweighted range fix; insufficient evidence for shared bias"
    bias = 0.0
    try:
        if method in ("huber_only", "asymmetric_huber"):
            def objective(p):
                r = residual(p)
                return np.where(r < 0, 0.5 * r, r) if method == "asymmetric_huber" else r

            fit = least_squares(objective, position, loss="huber", f_scale=scale, max_nfev=100)
            if not fit.success:
                return Estimate(status="solver_failed", reason="Candidate did not converge")
            position = fit.x
            weights = np.minimum(1, scale / np.maximum(np.abs(objective(position)), 1e-9))
            if method == "asymmetric_huber":
                weights *= np.where(residual(position) < 0, 0.25, 1)
            directions = position - anchors
            j = directions / np.maximum(np.linalg.norm(directions, axis=1, keepdims=True), 1e-8)
            information = j.T @ j
            condition = float(np.linalg.cond(information))
            covariance = np.linalg.inv(information) * max(0.04, np.sum(residual(position)**2)/(n-2))
            reason = "Single Huber solve" if method == "huber_only" else "Asymmetric Huber solve"
        elif method == "common_nonnegative_bias":
            # Two position coordinates plus one shared delay require redundancy.
            # Per-link free biases would leave n+2 unknowns for n observations.
            if n >= 4:
                fit = least_squares(
                    lambda q: residual(q[:2]) + q[2],
                    [*position, max(0, -np.mean(raw))],
                    bounds=([-np.inf, -np.inf, 0], [np.inf, np.inf, np.inf]),
                    max_nfev=100,
                )
                if fit.success and np.isfinite(fit.x).all():
                    delta = fit.x[:2] - anchors
                    directions = delta / np.maximum(np.linalg.norm(delta, axis=1, keepdims=True), 1e-8)
                    augmented = np.column_stack([directions, np.ones(n)])
                    information = augmented.T @ augmented
                    augmented_condition = float(np.linalg.cond(information))
                    if augmented_condition < 1000:
                        variance = max(0.04, np.sum(fit.fun**2) / (n - 3))
                        joint_covariance = np.linalg.inv(information) * variance
                        # Fixed one-sided evidence threshold; never selected using truth.
                        if fit.x[2] > 2 * np.sqrt(joint_covariance[2, 2]):
                            position = fit.x[:2]
                            bias = float(fit.x[2])
                            covariance = joint_covariance[:2, :2]
                            condition = augmented_condition
                            reason = "Position and nonnegative shared range bias fitted"
        else:
            raise ValueError(f"Unknown method: {method}")
        if condition > 1000 or not np.isfinite(covariance).all():
            return Estimate(status="poor_geometry", reason="Candidate geometry is ill-conditioned")
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return Estimate(status="solver_failed", reason="Candidate numerical failure")
    result = Estimate(
        position=tuple(position), status="valid", reason=reason,
        residuals={m.anchor_id: float(r) for m, r in zip(valid, residual(position), strict=True)},
        reliability={m.anchor_id: float(w) for m, w in zip(valid, weights, strict=True)},
        covariance=covariance.tolist(), condition_number=condition,
        runtime_ms=(perf_counter()-start)*1000,
    )
    # Research-only inferred value, kept separate from the API contract.
    return result
