"""Oracle experiments for LOCUS-X AI reliability — P0.1 diagnostic.

Two experiments expose whether the additive-coupling + updated policy fixes are
sufficient, or whether the MLP classifier itself is the remaining bottleneck:

J-1 (oracle classifier): Replace the MLP with ground-truth FaultLabel NLOS labels
    (p_nlos = 1.0 for NLOS anchors, 0.0 for LOS), apply the current v2 policy.
    If J-1 improves RMSE substantially over stat, the bottleneck is the classifier.
    If J-1 does not improve RMSE, the policy / coupling / geometry is the bottleneck.

J-4 (MLP + additive coupling, already in production): Run the real MLP classifier
    with additive coupling (the P1 fix already applied in estimators.py) for
    comparison against both J-1 and the baseline stat solver.

Usage:
    python -m backend.locus_x.oracle_experiments [--output <dir>] [--workers <n>]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from functools import cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from .ai_evaluation import _settings, base_variants, mixed_variants
from .environment import load_environments, resolve_floor, route_position
from .estimators import solve
from .policy_v2 import load_policy, policy_weights
from .reliability_v2 import FeatureHistoryV2, ReliabilityV2Model
from .simulation import generate_measurements
from .tracking import MotionTracker

ROOT = Path(__file__).resolve().parents[2]
FINAL_SEEDS = ROOT / "configs" / "ai" / "final-test-seeds-v2.json"
V2_MODEL = ROOT / "models" / "ai-reliability-v2"
POLICY = V2_MODEL / "policy-v2.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "ai-reliability" / "oracle-experiments"
DT = 0.5
STEPS = 120
ROUTE = "held_out"
COUNTS = (3, 4, 5, 6, 7, 8)
LAYOUTS = (
    ("hospital_floor_01", None, "hospital_floor_01"),
    ("hospital_v3", "ground", "ground"),
    ("hospital_v3", "first", "first"),
    ("hospital_v3", "second", "second"),
)


@cache
def _models():
    v2_model = ReliabilityV2Model.load(V2_MODEL)
    policy = load_policy(POLICY)
    candidate = {
        "threshold": float(policy["threshold"]),
        "floor": float(policy["floor"]),
        "fallback": "min3",
        "mode": str(policy["mode"]),
    }
    return v2_model, policy, candidate


@cache
def _environment(environment_id: str, floor_id: str | None):
    environments = load_environments()
    return resolve_floor(environments[environment_id], floor_id)


def _position_error(position, truth) -> float:
    if position is None:
        return float("nan")
    return float(np.linalg.norm(np.array(position) - np.array(truth)))


def _load_specs() -> list[dict]:
    seeds_data = json.loads(FINAL_SEEDS.read_text(encoding="utf-8"))
    seeds = seeds_data.get("fresh_seeds", seeds_data.get("seeds", seeds_data)) if isinstance(seeds_data, dict) else seeds_data
    variants = base_variants() + mixed_variants()
    specs = []
    run_index = 0
    for layout_index, (env_id, floor_id, _) in enumerate(LAYOUTS):
        for count in COUNTS:
            environment = _environment(env_id, floor_id)
            all_anchors = [a.id for a in environment.anchors]
            available = [a for a in all_anchors if not a.startswith("disabled")]
            if len(available) < count:
                continue
            subset = available[:count]
            for variant_index, variant in enumerate(variants):
                for seed in seeds:
                    specs.append({
                        "run_index": run_index,
                        "layout_index": layout_index,
                        "environment_id": env_id,
                        "floor_id": floor_id,
                        "count": count,
                        "subset": subset,
                        "variant": variant,
                        "variant_index": variant_index,
                        "seed": seed,
                    })
                    run_index += 1
    return specs


def _anchor_subset_from_spec(spec: dict, environment):
    all_anchors = [a.id for a in environment.anchors]
    disabled = [a for a in all_anchors if a not in spec["subset"]]
    return disabled


def evaluate_oracle_run(spec: dict) -> dict:
    """Evaluate one run under both J-1 (oracle labels) and J-4 (real MLP)."""
    v2_model, _policy, candidate = _models()
    environment = _environment(spec["environment_id"], spec["floor_id"])
    disabled = _anchor_subset_from_spec(spec, environment)
    settings = _settings(spec["variant"], spec["seed"], ROUTE, disabled)

    stat_tracker = MotionTracker()
    oracle_tracker = MotionTracker()
    mlp_tracker = MotionTracker()
    v2_history_oracle = FeatureHistoryV2()
    v2_history_mlp = FeatureHistoryV2()

    output = defaultdict(list)
    previous_state_oracle = None
    previous_timestamp_oracle = None
    previous_state_mlp = None
    previous_timestamp_mlp = None

    for step in range(STEPS):
        timestamp = round(step * DT, 8)
        truth = route_position(environment.routes[ROUTE], timestamp, settings.speed)
        measurements, labels = generate_measurements(
            environment, settings, truth, timestamp, step
        )
        baseline = solve(measurements)
        stat_fix = solve(measurements, robust=True)
        stat_tracker.update(stat_fix, timestamp)

        # J-1: oracle classifier — use ground-truth NLOS labels as p_nlos.
        label_by_anchor = {
            label.anchor_id: label
            for label in labels
            if label.available and label.kind in ("los", "nlos")
        }
        oracle_p_nlos = []
        oracle_anchor_ids = []
        for m in measurements:
            label = label_by_anchor.get(m.anchor_id)
            if label is not None:
                oracle_p_nlos.append(1.0 if label.kind == "nlos" else 0.0)
                oracle_anchor_ids.append(m.anchor_id)
        oracle_weights_dict = None
        oracle_fallback = True
        if oracle_p_nlos:
            nlos_arr = np.asarray(oracle_p_nlos)
            w, oracle_fallback, _ = policy_weights(nlos_arr, len(oracle_p_nlos), candidate)
            if w is not None:
                oracle_weights_dict = {
                    anchor_id: w[str(i)]
                    for i, anchor_id in enumerate(oracle_anchor_ids)
                }
        oracle_estimate = stat_fix
        if oracle_weights_dict is not None:
            oracle_estimate = solve(measurements, robust=True, ai_weights=oracle_weights_dict)
        oracle_tracking = oracle_tracker.update(oracle_estimate, timestamp)

        # J-4: real MLP with additive coupling (production path).
        v2_output = v2_model.predict(
            measurements,
            environment,
            stat_fix,
            v2_history_mlp,
            previous_state_mlp,
            previous_timestamp_mlp,
        )
        previous_state_mlp = (
            None if mlp_tracker.state is None else np.array(mlp_tracker.state)
        )
        previous_timestamp_mlp = mlp_tracker.timestamp
        predictions = v2_output.metadata.predictions
        mlp_weights_dict = None
        mlp_fallback = True
        if predictions:
            nlos_arr = np.asarray([1.0 - p.p_los for p in predictions])
            w, mlp_fallback, _ = policy_weights(nlos_arr, len(predictions), candidate)
            if w is not None:
                mlp_weights_dict = {
                    p.anchor_id: w[str(i)]
                    for i, p in enumerate(predictions)
                }
        mlp_estimate = stat_fix
        if mlp_weights_dict is not None:
            mlp_estimate = solve(measurements, robust=True, ai_weights=mlp_weights_dict)
        mlp_tracking = mlp_tracker.update(mlp_estimate, timestamp)

        n_nlos = sum(1 for label in labels if label.available and label.kind == "nlos")
        n_available = sum(1 for label in labels if label.available)

        output["run_id"].append(spec["run_index"])
        output["count"].append(spec["count"])
        output["n_nlos"].append(n_nlos)
        output["n_available"].append(n_available)
        output["stat_error"].append(_position_error(stat_tracker.state[:2] if stat_tracker.state is not None else None, truth))
        output["oracle_error"].append(_position_error(oracle_tracking.position, truth))
        output["mlp_error"].append(_position_error(mlp_tracking.position, truth))
        output["oracle_fallback"].append(int(oracle_fallback))
        output["mlp_fallback"].append(int(mlp_fallback))

    return dict(output)


def _rmse(errors: np.ndarray) -> float:
    finite = errors[np.isfinite(errors)]
    if len(finite) == 0:
        return float("nan")
    return float(np.sqrt(np.mean(finite**2)))


def _aggregate(all_outputs: list[dict]) -> dict:
    combined: dict = defaultdict(list)
    for out in all_outputs:
        for key, vals in out.items():
            combined[key].extend(vals)
    stat_err = np.asarray(combined["stat_error"])
    oracle_err = np.asarray(combined["oracle_error"])
    mlp_err = np.asarray(combined["mlp_error"])
    n_nlos = np.asarray(combined["n_nlos"])
    n_avail = np.asarray(combined["n_available"])

    def rmse_slice(errors, mask=None):
        e = errors if mask is None else errors[mask]
        return _rmse(e)

    majority_nlos = n_nlos > (n_avail / 2)

    return {
        "stat_rmse_m": rmse_slice(stat_err),
        "j1_oracle_rmse_m": rmse_slice(oracle_err),
        "j4_mlp_rmse_m": rmse_slice(mlp_err),
        "j1_vs_stat_improvement_m": rmse_slice(stat_err) - rmse_slice(oracle_err),
        "j4_vs_stat_improvement_m": rmse_slice(stat_err) - rmse_slice(mlp_err),
        "majority_nlos": {
            "frames": int(majority_nlos.sum()),
            "stat_rmse_m": rmse_slice(stat_err, majority_nlos),
            "j1_oracle_rmse_m": rmse_slice(oracle_err, majority_nlos),
            "j4_mlp_rmse_m": rmse_slice(mlp_err, majority_nlos),
        },
        "oracle_fallback_rate": float(np.mean(combined["oracle_fallback"])),
        "mlp_fallback_rate": float(np.mean(combined["mlp_fallback"])),
        "total_frames": len(stat_err),
        "interpretation": (
            "If j1_oracle_rmse_m < stat_rmse_m: policy/coupling is not the bottleneck — "
            "gain is theoretically available. "
            "If j4_mlp_rmse_m ≈ j1_oracle_rmse_m: MLP classifier quality is sufficient. "
            "If j4_mlp_rmse_m ≈ stat_rmse_m but j1 improves: MLP prediction is the bottleneck."
        ),
    }


def run(output: Path = DEFAULT_OUTPUT, workers: int = 4) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    specs = _load_specs()
    if workers > 1:
        with Pool(workers) as pool:
            results = pool.map(evaluate_oracle_run, specs)
    else:
        results = [evaluate_oracle_run(spec) for spec in specs]
    summary = _aggregate(results)
    (output / "oracle-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    summary = run(args.output, args.workers)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
