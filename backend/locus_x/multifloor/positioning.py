"""Range-only 3D positioning and floor hypotheses.

Pure estimation: observations in, estimates out. Nothing here can see the
simulator's truth floor, the route schedule or a fault label -- the inputs are
measurements keyed ``"<floor_id>:<anchor_id>"`` plus the validated configuration.
Route assistance is a separate input consumed by the tracker, never by
:func:`floor_scores`.

Two facts shape this module:

* **A 3D fix is only honest when the visible anchors span three dimensions.** With
  anchors on a single floor the range set is mirror-symmetric about the anchor
  plane: two elevations fit equally well, so ``z`` is not identifiable. The module
  reports that as ``observable=False`` and returns the horizontal projection plus
  the mirror offset, never an invented storey.
* **A large floor-hypothesis margin does not prove a floor.** A target halfway up
  a stairwell produces a margin of 60-95 between hypotheses because the ``(x, y)``
  refit absorbs the vertical mismatch; only the absolute fit quality exposes it.
  Both gates are therefore mandatory, and the fit ratio is judged against the noise
  scale measured by the unconstrained 3D solve rather than one derived from the
  hypothesis residuals it is meant to test.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import sqrt
from time import perf_counter
from typing import Literal

import numpy as np
from scipy.optimize import least_squares

from ..building import Building
from ..schemas import Measurement
from .coordinates import Anchor3D
from .simulation import MAX_SLABS, count_slab_hops

MIN_ANCHORS_3D = 4
"""Three unknowns plus one degree of freedom; fewer cannot produce a 3D fix."""

CONDITION_LIMIT = 1000.0
"""Same bound as the 2D solver: above this the directions do not constrain the point."""

COPLANAR_RATIO = 1e-6
"""Singular-value ratio below which the anchor cloud is treated as coplanar."""

FIT_RATIO_LIMIT = 5.0
"""Reject a floor hypothesis whose fit is this many noise variances worse than plausible."""

MARGIN_LIMIT = 3.0
"""Minimum SSE margin between the best and second-best floor hypothesis, in m^2."""

PROBABILITY_LIMIT = 0.8
"""Minimum softmax share of the best floor."""

NOISE_SIGMA_FLOOR = 0.01
"""Smallest noise scale (metres) the fit-ratio gate will divide by.

A noiseless solve reports a near-zero residual scale; without a floor the ratio would
be judged against an arbitrarily small number and a numerically perfect fit could look
like a global misfit. One centimetre is far below any physically meaningful range noise.
"""

# The softmax over -sse / 2 is a likelihood score over hypotheses, not a calibrated
# posterior: the hypotheses are not exhaustive (the target may be between floors) and
# neither the noise model nor the slab model is calibrated against real radios.


@dataclass(frozen=True, slots=True)
class Estimate3D:
    """One 3D fix, or an explicit statement of why 3D is not observable."""

    position: tuple[float, float, float] | None
    horizontal: tuple[float, float] | None
    status: str
    reason: str
    observable: bool
    covariance: list[list[float]] | None = None
    condition_number: float | None = None
    vertical_sigma: float | None = None
    mirror_offset: float | None = None
    residuals: dict[str, float] = field(default_factory=dict)
    reliability: dict[str, float] = field(default_factory=dict)
    noise_sigma: float | None = None
    runtime_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class FloorScores:
    """Per-floor hypothesis scores for one observation set."""

    sse: dict[str, float]
    probability: dict[str, float]
    best_floor: str | None
    margin: float
    fit_ratio: float | None
    sigma_hat: float | None
    ambiguous: bool
    reason: str
    observations: int


@dataclass(frozen=True, slots=True)
class FloorDecision:
    """A floor the evidence supports, or an explicit refusal to name one."""

    floor_id: str | None
    ambiguous: bool
    traversing: bool
    reason: str
    source: Literal["configured", "route_assisted", "inferred"] = "inferred"
    confidence: float | None = None
    candidates: tuple[str, ...] = ()


def _failure(status: str, reason: str, elapsed: float) -> Estimate3D:
    return Estimate3D(
        position=None,
        horizontal=None,
        status=status,
        reason=reason,
        observable=False,
        runtime_ms=(perf_counter() - elapsed) * 1000,
    )


def _valid(
    observations: Mapping[str, Measurement], anchors: Mapping[str, Anchor3D]
) -> list[tuple[str, Measurement, Anchor3D]]:
    """Drop unusable observations and reject keys the building does not install."""
    unknown = sorted(set(observations) - set(anchors))
    if unknown:
        raise ValueError(
            f"Observations reference anchors that are not installed: {', '.join(unknown)}"
        )
    valid: list[tuple[str, Measurement, Anchor3D]] = []
    for key, measurement in observations.items():
        values = (*measurement.anchor_position, measurement.observed_range, measurement.timestamp)
        if measurement.observed_range <= 0 or not np.isfinite(values).all():
            continue
        valid.append((key, measurement, anchors[key]))
    return valid


def _mad_sigma(residuals: np.ndarray) -> float:
    if residuals.size == 0:
        return 0.0
    return 1.4826 * float(np.median(np.abs(residuals - np.median(residuals))))


def _coplanar_fix(
    xyz: np.ndarray, ranges: np.ndarray, keys: list[str], elapsed: float
) -> Estimate3D:
    """Horizontal projection plus the mirror offset, for anchors on one plane.

    Differencing the range equations cancels the unknown elevation, so ``(x, y)`` is
    still recoverable; what is not recoverable is which side of the anchor plane the
    target is on. Both elevations are reported through ``mirror_offset``.
    """
    planar = 2 * (xyz[1:, :2] - xyz[0, :2])
    rhs = ranges[0] ** 2 - ranges[1:] ** 2 + (xyz[1:] ** 2).sum(axis=1) - (xyz[0] ** 2).sum()
    if np.linalg.matrix_rank(planar) < 2:
        return _failure(
            "poor_geometry",
            "Anchor layout is collinear in plan; neither horizontal axis is constrained",
            elapsed,
        )
    position = np.linalg.lstsq(planar, rhs, rcond=None)[0]
    offset_squared = float(np.mean(ranges**2 - np.sum((position - xyz[:, :2]) ** 2, axis=1)))
    offset = sqrt(max(offset_squared, 0.0))
    return Estimate3D(
        position=None,
        horizontal=(float(position[0]), float(position[1])),
        status="vertical_unobservable",
        reason=(
            f"vertical_unobservable: anchors are coplanar; z is mirror-ambiguous about "
            f"the anchor plane (+/- {offset:.2f} m)"
        ),
        observable=False,
        mirror_offset=offset,
        residuals={},
        runtime_ms=(perf_counter() - elapsed) * 1000,
    )


def solve_3d(
    observations: Mapping[str, Measurement],
    anchors: Mapping[str, Anchor3D],
    *,
    robust: bool = False,
    trust: dict[str, float] | None = None,
    slab_bias_per_slab: float = 0.0,
    datums: Sequence[float] | None = None,
) -> Estimate3D:
    """Solve one 3D fix from ranges keyed by ``"<floor_id>:<anchor_id>"``.

    ``observations`` is keyed rather than a plain list because anchor IDs repeat on
    every storey: the key is the only thing that identifies which floor's anchor a
    range belongs to, and the measurement itself deliberately does not carry it.

    ``slab_bias_per_slab`` and ``datums`` must match the generator's obstruction model
    (``building.slab_bias_per_slab`` and the floor elevations). Without the slab term
    the cross-floor ranges are systematically long and pull the fix metres off: with
    the hospital's 1.5 m/slab the ground-floor error was 3.3 m, which is larger than a
    storey. A caller that omits them is explicitly assuming a free-space model.

    ``robust`` reuses the 2D solver's Huber/MAD scheme and its reliability blend
    (``0.65 * trust + 0.35 * instant``). ``trust`` is mutated in place, keyed by the
    same observation keys.

    Never fabricates a vertical answer: with coplanar anchors the result is
    ``observable=False``, ``position=None``, and ``horizontal`` plus ``mirror_offset``
    describe exactly what the ranges do constrain.
    """
    start = perf_counter()
    valid = _valid(observations, anchors)
    if len(valid) < MIN_ANCHORS_3D:
        return _failure(
            "insufficient",
            f"At least {MIN_ANCHORS_3D} valid, distinct anchors are required for a 3D fix",
            start,
        )
    try:
        keys = [key for key, _, _ in valid]
        xyz = np.asarray([(a.x, a.y, a.z) for _, _, a in valid], dtype=float)
        ranges = np.asarray([m.observed_range for _, m, _ in valid], dtype=float)
        singular = np.linalg.svd(xyz - xyz.mean(axis=0), compute_uv=False)
        if singular[0] < 1e-9:
            return _failure("poor_geometry", "All visible anchors share one position", start)
        if singular[2] <= singular[0] * COPLANAR_RATIO:
            return _coplanar_fix(xyz, ranges, keys, start)

        def predicted(position: np.ndarray) -> np.ndarray:
            values = np.linalg.norm(position - xyz, axis=1)
            if slab_bias_per_slab > 0 and datums:
                hops = np.array(
                    [
                        count_slab_hops(float(position[2]), float(anchor_z), datums)
                        for anchor_z in xyz[:, 2]
                    ]
                )
                values = values + slab_bias_per_slab * np.minimum(hops, MAX_SLABS)
            return values

        def residual(position: np.ndarray) -> np.ndarray:
            return predicted(position) - ranges

        matrix = 2 * (xyz[1:] - xyz[0])
        rhs = ranges[0] ** 2 - ranges[1:] ** 2 + (xyz[1:] ** 2).sum(axis=1) - (xyz[0] ** 2).sum()
        initial = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
        base = least_squares(residual, initial, max_nfev=100)
        result = base
        weights = np.ones(len(valid))
        if robust:
            raw = residual(base.x)
            scale = max(0.2, _mad_sigma(raw))
            candidate = least_squares(residual, base.x, loss="huber", f_scale=scale, max_nfev=100)
            magnitude = np.abs(residual(candidate.x))
            instant = np.clip(1 / (1 + (magnitude / (2 * scale)) ** 2), 0.08, 1)
            weights = np.array(
                [
                    0.65 * (trust or {}).get(key, float(score)) + 0.35 * score
                    for key, score in zip(keys, instant, strict=True)
                ]
            )
            result = least_squares(
                lambda p: np.sqrt(weights) * residual(p),
                candidate.x,
                loss="huber",
                f_scale=scale,
                max_nfev=100,
            )
        if not result.success or not np.isfinite(result.x).all():
            return _failure("solver_failed", "Least-squares solver did not converge", start)
        delta = result.x - xyz
        jacobian = delta / np.maximum(np.linalg.norm(delta, axis=1, keepdims=True), 1e-8)
        information = jacobian.T @ (weights[:, None] * jacobian)
        condition = float(np.linalg.cond(information))
        if not np.isfinite(condition) or condition > CONDITION_LIMIT:
            return _failure(
                "poor_geometry",
                "Range directions do not constrain all three position axes",
                start,
            )
        raw = residual(result.x)
        variance = max(0.04, float(np.sum(weights * raw**2) / max(1, len(valid) - 3)))
        covariance = np.linalg.inv(information) * variance
        reliability = (
            {key: float(w) for key, w in zip(keys, weights, strict=True)} if robust else {}
        )
        if robust and trust is not None:
            trust.update(reliability)
        return Estimate3D(
            position=(float(result.x[0]), float(result.x[1]), float(result.x[2])),
            horizontal=(float(result.x[0]), float(result.x[1])),
            status="valid",
            reason="3D range update accepted",
            observable=True,
            covariance=covariance.tolist(),
            condition_number=condition,
            vertical_sigma=float(sqrt(max(covariance[2, 2], 0.0))),
            residuals={key: float(r) for key, r in zip(keys, raw, strict=True)},
            reliability=reliability,
            noise_sigma=_mad_sigma(raw),
            runtime_ms=(perf_counter() - start) * 1000,
        )
    except (ValueError, np.linalg.LinAlgError, FloatingPointError):
        return _failure("solver_failed", "Numerical failure while solving ranges", start)


def _hypothesis_fit(
    xyz: np.ndarray,
    ranges: np.ndarray,
    weights: np.ndarray,
    target_z: float,
    slab_bias_per_slab: float = 0.0,
    datums: Sequence[float] | None = None,
) -> tuple[float, np.ndarray]:
    """Best horizontal fit for a fixed target elevation, and its SSE."""

    hops = (
        np.array([count_slab_hops(target_z, float(az), datums) for az in xyz[:, 2]])
        if slab_bias_per_slab > 0 and datums
        else np.zeros(len(xyz))
    )
    slab = slab_bias_per_slab * np.minimum(hops, MAX_SLABS)

    def residual(point: np.ndarray) -> np.ndarray:
        spread = np.hypot(point[0] - xyz[:, 0], point[1] - xyz[:, 1])
        return np.sqrt(spread**2 + (target_z - xyz[:, 2]) ** 2) + slab - ranges

    weighted = np.median(xyz[:, :2], axis=0) if len(xyz) else np.zeros(2)
    fit = least_squares(lambda p: np.sqrt(weights) * residual(p), weighted, max_nfev=100)
    raw = residual(fit.x)
    return float(np.sum(weights * raw**2)), raw


def floor_scores(
    observations: Mapping[str, Measurement],
    building: Building,
    anchors: Mapping[str, Anchor3D],
    *,
    device_height: float | None = None,
    candidate_floors: Sequence[str] | None = None,
    trust: dict[str, float] | None = None,
    noise_sigma: float | None = None,
) -> FloorScores:
    """Score every candidate floor hypothesis by its best-fitting residual.

    For floor ``f`` the target is assumed at ``elevation(f) + device_height``; the
    horizontal position is refit under that assumption, so cross-floor anchors
    contribute their true 3D anchor elevation. ``probability`` is a softmax over
    ``-sse / 2`` -- a likelihood score over hypotheses, **not** a calibrated
    posterior, because the hypotheses are neither exhaustive nor calibrated.

    ``ambiguous`` is true when the margin between the best and second-best floor is
    below :data:`MARGIN_LIMIT`, when the best share is below
    :data:`PROBABILITY_LIMIT`, or when the fit ratio exceeds :data:`FIT_RATIO_LIMIT`.
    The last gate is the one that catches a target between floors: its margin is huge
    because the refit absorbs the vertical error, but its absolute fit is several
    noise variances off.

    ``noise_sigma`` must be the scale measured by the **unconstrained** 3D solve
    (:attr:`Estimate3D.noise_sigma`). It matters: a scale estimated from the
    hypothesis residuals themselves always tracks those residuals, so the ratio
    collapses to ~1 whatever the fit and the gate never fires -- measured 21-51 with
    the 3D scale versus 1.0-1.4 with the residual scale on the same mid-transition
    data. The residual fallback therefore cannot detect a global misfit; it exists
    only for callers that have no 3D solve at all (coplanar or failed fixes). Both
    scales are floored at :data:`NOISE_SIGMA_FLOOR` for numerical hygiene.
    """
    height = building.target_device_height if device_height is None else device_height
    if height <= 0:
        raise ValueError(f"Device height must be positive, got {height!r}")
    floors = {floor.id: floor for floor in building.floors}
    requested = list(candidate_floors) if candidate_floors is not None else list(floors)
    unknown = [floor_id for floor_id in requested if floor_id not in floors]
    if unknown:
        raise ValueError(f"Unknown candidate floors: {', '.join(unknown)}")
    valid = _valid(observations, anchors)
    if not valid:
        return FloorScores(
            sse={},
            probability={},
            best_floor=None,
            margin=0.0,
            fit_ratio=None,
            sigma_hat=None,
            ambiguous=True,
            reason="No valid observations",
            observations=0,
        )
    xyz = np.asarray([(a.x, a.y, a.z) for _, _, a in valid], dtype=float)
    ranges = np.asarray([m.observed_range for _, m, _ in valid], dtype=float)
    weights = np.array(
        [0.65 * (trust or {}).get(key, 1.0) + 0.35 for key, _, _ in valid], dtype=float
    )

    sse: dict[str, float] = {}
    residuals: dict[str, np.ndarray] = {}
    datums = [floor.elevation for floor in building.floors]
    for floor_id in requested:
        target = floors[floor_id].elevation + height
        try:
            value, raw = _hypothesis_fit(
                xyz, ranges, weights, target, building.slab_bias_per_slab, datums
            )
        except (ValueError, np.linalg.LinAlgError):
            value, raw = float("inf"), np.zeros(len(valid))
        sse[floor_id] = value
        residuals[floor_id] = raw

    finite = {floor_id: value for floor_id, value in sse.items() if np.isfinite(value)}
    if not finite:
        return FloorScores(
            sse=sse,
            probability={floor_id: 1 / len(sse) for floor_id in sse},
            best_floor=None,
            margin=0.0,
            fit_ratio=None,
            sigma_hat=None,
            ambiguous=True,
            reason="Every floor hypothesis failed to fit",
            observations=len(valid),
        )
    best_floor = min(finite, key=lambda floor_id: finite[floor_id])
    low = min(finite.values())
    weights_exp = {floor_id: np.exp(-(value - low) / 2) for floor_id, value in finite.items()}
    total = sum(weights_exp.values())
    probability = {floor_id: float(value / total) for floor_id, value in weights_exp.items()}
    ordered = sorted(finite.values())
    margin = float(ordered[1] - ordered[0]) if len(ordered) > 1 else float("inf")
    sigma_hat = (
        max(float(noise_sigma), NOISE_SIGMA_FLOOR)
        if noise_sigma is not None
        else max(_mad_sigma(residuals[best_floor]), NOISE_SIGMA_FLOOR)
    )
    fit_ratio = float(finite[best_floor] / (len(valid) * sigma_hat**2))

    reasons = []
    if margin < MARGIN_LIMIT:
        reasons.append(f"margin {margin:.2f} m^2 below {MARGIN_LIMIT}")
    if probability[best_floor] < PROBABILITY_LIMIT:
        reasons.append(f"best likelihood {probability[best_floor]:.2f} below {PROBABILITY_LIMIT}")
    if fit_ratio > FIT_RATIO_LIMIT:
        reasons.append(
            f"fit ratio {fit_ratio:.1f} exceeds {FIT_RATIO_LIMIT}: no floor hypothesis "
            "fits at the estimated noise level (target between floors?)"
        )
    return FloorScores(
        sse=sse,
        probability=probability,
        best_floor=best_floor,
        margin=margin,
        fit_ratio=fit_ratio,
        sigma_hat=sigma_hat,
        ambiguous=bool(reasons),
        reason="; ".join(reasons) if reasons else f"Hypothesis {best_floor} fits",
        observations=len(valid),
    )


def floor_cell_half_gap(
    building: Building, floor_id: str, device_height: float | None = None
) -> float:
    """Distance from a floor's device elevation to the nearest neighbouring midpoint.

    This is the radius of the floor's Voronoi cell on the vertical axis: the largest
    deviation a confident floor identity may have. For the hospital's 4.2 m storeys
    it is 2.1 m.
    """
    height = building.target_device_height if device_height is None else device_height
    target = next(
        (floor.elevation + height for floor in building.floors if floor.id == floor_id),
        None,
    )
    if target is None:
        raise ValueError(f"Unknown floor {floor_id!r} in building {building.id!r}")
    distances = [
        abs(floor.elevation + height - target) / 2
        for floor in building.floors
        if floor.id != floor_id
    ]
    return min(distances) if distances else float("inf")


def decide_floor(
    estimate: Estimate3D,
    scores: FloorScores | None,
    building: Building,
    *,
    device_height: float | None = None,
    candidates: Sequence[str] | None = None,
    previous_floor: str | None = None,
) -> FloorDecision:
    """Name a floor only when the uncertainty interval fits inside its vertical cell.

    The primary cue is the free 3D solve: accept floor ``f`` when
    ``|z_est - z_f| + 3 * sigma_z < half_gap(f)`` **and** ``|z_est - z_f| <= 0.5 m``.
    The first clause is a Voronoi-cell condition -- the uncertainty interval must sit
    inside the floor's own cell. A flat band such as ``max(3 * sigma_z, 0.35 m)`` would
    widen with noise and confidently report a floor for a target halfway up the stairs.

    The hypothesis test is a fallback for geometry where ``z`` is unobservable for a
    reason other than the coplanar mirror (where it cannot decide either), and then
    both its margin and fit-ratio gates apply. ``previous_floor`` is accepted for
    continuity but never overrides the evidence.
    """
    height = building.target_device_height if device_height is None else device_height
    allowed = list(candidates) if candidates is not None else [f.id for f in building.floors]
    floors = {floor.id: floor for floor in building.floors}
    targets = {floor_id: floors[floor_id].elevation + height for floor_id in allowed}
    if estimate.observable and estimate.position is not None:
        z = estimate.position[2]
        sigma = estimate.vertical_sigma or 0.0
        threshold = 0.5
        accepted = [
            floor_id
            for floor_id, target in targets.items()
            if abs(z - target) + 3 * sigma < floor_cell_half_gap(building, floor_id, height)
            and abs(z - target) <= threshold
        ]
        fit_ok = scores is None or scores.fit_ratio is None or scores.fit_ratio <= FIT_RATIO_LIMIT
        if len(accepted) == 1 and fit_ok:
            floor_id = accepted[0]
            return FloorDecision(
                floor_id=floor_id,
                ambiguous=False,
                traversing=False,
                reason=(
                    f"3D fix {abs(z - targets[floor_id]):.2f} m from floor {floor_id} "
                    f"(+/- {3 * sigma:.2f} m inside a "
                    f"{floor_cell_half_gap(building, floor_id, height):.2f} m cell)"
                ),
                confidence=scores.probability.get(floor_id) if scores else None,
                candidates=tuple(allowed),
            )
        if not fit_ok and scores is not None:
            reason = f"fit-ratio gate: {scores.reason}"
        else:
            reason = (
                "Vertical position does not fit inside any single floor cell; "
                "the target is between floors or the vertical uncertainty is too large"
            )
        # Traversing only when the estimate is bracketed by two candidate floors.
        traversing = any(
            low < z < high for low in targets.values() for high in targets.values() if low < high
        )
        return FloorDecision(
            floor_id=None,
            ambiguous=True,
            traversing=traversing,
            reason=reason,
            confidence=None,
            candidates=tuple(allowed),
        )

    if estimate.mirror_offset is not None:
        return FloorDecision(
            floor_id=None,
            ambiguous=True,
            traversing=False,
            reason=(
                "vertical_unobservable: coplanar anchors leave z mirror-ambiguous "
                f"(+/- {estimate.mirror_offset:.2f} m), so no storey is claimed"
            ),
            candidates=tuple(allowed),
        )
    if (
        scores is not None
        and not scores.ambiguous
        and scores.best_floor in targets
        and scores.observations >= MIN_ANCHORS_3D
    ):
        return FloorDecision(
            floor_id=scores.best_floor,
            ambiguous=False,
            traversing=False,
            reason=f"Hypothesis fallback: {scores.reason}",
            confidence=scores.probability.get(scores.best_floor),
            candidates=tuple(allowed),
        )
    if scores is not None and scores.observations < MIN_ANCHORS_3D:
        reason = (
            f"Only {scores.observations} usable observations; fewer than "
            f"{MIN_ANCHORS_3D} cannot separate floors"
        )
    elif scores is not None:
        reason = f"No floor accepted: {scores.reason}"
    else:
        reason = "No floor evidence available"
    return FloorDecision(
        floor_id=None,
        ambiguous=True,
        traversing=False,
        reason=reason,
        candidates=tuple(allowed),
    )
