"""Frozen v2 weighting policy and the single inference pipeline shared by the live session
and the benchmark. Lightweight on purpose: no simulator, evaluation or benchmark imports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .estimators import solve
from .schemas import AIMetadata, AIPrediction, Estimate

DEFAULT_GATE_CHI2 = 13.82


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def policy_hash(policy: dict) -> str:
    core = {key: value for key, value in policy.items() if key != "policy_sha256"}
    encoded = json.dumps(_jsonable(core), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_policy_file(path: Path | str) -> dict:
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = policy.get("policy_sha256", "")
    if expected.startswith("PENDING_REFRESH"):
        raise ValueError(
            "policy_sha256 is pending refresh; run: "
            "python -m backend.locus_x.policy_v2 --refresh-hash"
        )
    if expected != policy_hash(policy):
        raise ValueError("policy checksum mismatch")
    return policy


def candidate_from_policy(policy: dict) -> dict:
    scaling = policy.get("anchor_count_scaling") or {}
    return {
        "threshold": float(policy["threshold"]),
        "floor": float(policy["floor"]),
        "fallback": "min3",
        "mode": str(policy["mode"]),
        "anchor_scaling_reference": scaling.get("reference_anchors"),
    }


def policy_weights(
    probabilities_nlos: np.ndarray,
    available: int,
    candidate: dict,
) -> tuple[dict[str, float] | None, bool, bool]:
    """Return (weights, fallback, changed). ``probabilities_nlos`` is per anchor."""
    if available < 3 or len(probabilities_nlos) != available:
        return None, True, False
    probabilities_nlos = np.asarray(probabilities_nlos, dtype=float)
    if not np.isfinite(probabilities_nlos).all():
        return None, True, False
    p_los = np.clip(1.0 - probabilities_nlos, 0.0, 1.0)
    predicted_los = int(np.sum(p_los >= 0.5))
    minimum = 3
    if candidate["fallback"] == "min2_if4" and available >= 4:
        minimum = 2
    floor = float(candidate["floor"])
    if predicted_los < minimum:
        # Majority-NLOS frames stay active: predicted-NLOS anchors get the floor weight
        # instead of disarming the AI exactly where it is needed most.
        majority_weights = np.clip(np.where(p_los >= 0.5, 1.0, floor), 0.0, 1.0)
        changed = bool(np.any(majority_weights < 1.0 - 1e-12))
        return {str(i): float(v) for i, v in enumerate(majority_weights)}, False, changed
    if candidate["mode"] == "soft":
        weights = np.maximum(floor, p_los)
    else:
        threshold = float(candidate["threshold"])
        reference = candidate.get("anchor_scaling_reference")
        if reference:
            threshold = min(1.0, threshold * float(reference) / max(3, available))
        weights = np.where(
            p_los >= threshold,
            1.0,
            floor + (1.0 - floor) * (p_los / max(threshold, 1e-12)),
        )
    weights = np.clip(weights, 0.0, 1.0)
    changed = bool(np.any(weights < 1.0 - 1e-12))
    return {str(index): float(value) for index, value in enumerate(weights)}, False, changed


@dataclass
class V2Result:
    estimate: Estimate
    weights: dict[str, float] | None
    corrections: dict[str, float]
    fallback: bool
    changed: bool
    gate: float
    metadata: AIMetadata


def run_v2_policy(
    *,
    v2_model,
    policy: dict,
    candidate: dict,
    measurements,
    environment,
    stat_fix: Estimate,
    v2_history,
    tracker_state,
    tracker_timestamp,
    bias_by_anchor: dict[str, float] | None,
    v2_output=None,
) -> V2Result:
    """One v2 frame: classify, weight, range-correct, re-solve, pick the tracker gate.

    Inputs are observations, map geometry, the statistical fix and the statistical tracker
    state only. Range correction comes solely from the learned bias head.
    """
    # ``v2_output`` lets an ablation classify once and score several policies on it.
    output = v2_output or v2_model.predict(
        measurements, environment, stat_fix, v2_history, tracker_state, tracker_timestamp
    )
    predictions = output.metadata.predictions
    gates = policy.get("tracker_gate") or {}
    default_gate = float(gates.get("default_chi2", DEFAULT_GATE_CHI2))
    heavy_gate = float(gates.get("nlos_heavy_chi2", default_gate))  # no tightening unless set
    weights, fallback, changed = (None, True, False)
    named: dict[str, float] | None = None
    nlos = np.asarray([1.0 - p.p_los for p in predictions])
    if predictions:
        weights, fallback, changed = policy_weights(nlos, len(predictions), candidate)
        if weights is not None:
            named = {p.anchor_id: weights[str(i)] for i, p in enumerate(predictions)}
    corrections: dict[str, float] = {}
    correction = policy.get("range_correction") or {}
    if named is not None and correction.get("enabled") and bias_by_anchor:
        floor = float(correction.get("min_p_nlos", 0.25))
        scale = float(correction.get("scale", 1.0))
        for p, p_nlos in zip(predictions, nlos, strict=True):
            bias = bias_by_anchor.get(p.anchor_id)
            if bias is not None and p_nlos > floor:
                corrections[p.anchor_id] = scale * float(p_nlos) * float(bias)
    estimate = stat_fix
    gate = default_gate
    if named is not None:
        estimate = solve(
            measurements,
            robust=True,
            ai_weights=named,
            bias_corrections=corrections or None,
        )
        if len(predictions) and int(np.sum(nlos > 0.5)) > len(predictions) / 2:
            gate = heavy_gate
    applied = [
        AIPrediction(
            anchor_id=p.anchor_id,
            p_los=p.p_los,
            weight=float(named[p.anchor_id]) if named is not None else 1.0,
        )
        for p in predictions
    ]
    metadata = output.metadata.model_copy(
        update={
            "predictions": applied,
            "status": "active" if named is not None else "fallback",
            "reason": (
                "AI reliability active (policy weights applied)"
                if named is not None
                else "Policy fallback; statistical estimate used"
            ),
        }
    )
    return V2Result(estimate, named, corrections, fallback, changed, gate, metadata)
