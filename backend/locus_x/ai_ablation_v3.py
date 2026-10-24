"""Ablation of the three v2 policy additions on VALIDATION seeds only.

``rc`` is the frozen policy (weights + range correction); ``all3`` adds scaling and the gate.

Variants share one classification per frame and differ only in the policy applied:
weights-only, +range correction, +anchor-count threshold scaling, +tight NLOS gate, and the
full frozen policy. Never reads the reserved final-test seeds.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from copy import deepcopy
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from .ai_final import (
    COUNTS,
    DT,
    LAYOUTS,
    ROUTE,
    STEPS,
    _bootstrap_difference,
    _environment,
    _models,
    _position_error,
    _rmse,
    _scope_label,
    _write_json,
    disabled_anchors,
    final_variants,
    load_dataset_config,
)
from .ai_evaluation import _settings
from .ai_policy import candidate_from_policy, run_v2_policy
from .environment import route_position
from .estimators import solve
from .reliability import FeatureHistory
from .reliability_v2 import FeatureHistoryV2
from .simulation import generate_measurements
from .tracking import MotionTracker

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts" / "ai-reliability" / "ablation-v3"
AI_VARIANTS = ("weights", "rc", "scale", "gate", "all3")
METHODS = ("baseline", "stat", *AI_VARIANTS)


def variant_policies(policy: dict) -> dict[str, tuple[dict, dict]]:
    """(policy, candidate) per variant, derived from the frozen policy by switching parts off."""

    def derive(scale: bool, correction: bool, gate: bool):
        derived = deepcopy(policy)
        derived.pop("anchor_count_scaling", None)
        derived.pop("tracker_gate", None)
        if scale:
            derived["anchor_count_scaling"] = {"reference_anchors": 8}
        derived["range_correction"] = {**policy["range_correction"], "enabled": correction}
        if gate:
            derived["tracker_gate"] = {"default_chi2": 13.82, "nlos_heavy_chi2": 5.99}
        return derived, candidate_from_policy(derived)

    return {
        "weights": derive(False, False, False),
        "rc": derive(False, True, False),
        "scale": derive(True, False, False),
        "gate": derive(False, False, True),
        "all3": derive(True, True, True),
    }


def build_specs(seed_count: int, layouts: list[str]) -> list[dict]:
    seeds = load_dataset_config()["splits"]["validation"]["seeds"][:seed_count]
    subsets = load_dataset_config()["anchor_subsets"]
    specs = []
    for layout_index, (environment_id, floor_id, name) in enumerate(LAYOUTS):
        if name not in layouts:
            continue
        for seed in seeds:
            for variant_index, variant in enumerate(final_variants()):
                for count in COUNTS:
                    specs.append(
                        {
                            "run_index": len(specs),
                            "layout_index": layout_index,
                            "environment_id": environment_id,
                            "floor_id": floor_id,
                            "seed": int(seed),
                            "variant_index": variant_index,
                            "variant": variant,
                            "count": count,
                            "subset": subsets[str(count)][0],
                        }
                    )
    return specs


def evaluate_run(spec: dict) -> dict:
    v1_model, v2_model, policy, _ = _models()
    policies = variant_policies(policy)
    environment = _environment(spec["environment_id"], spec["floor_id"])
    settings = _settings(
        spec["variant"], spec["seed"], ROUTE, disabled_anchors(environment, spec["subset"])
    )
    stat_tracker = MotionTracker()
    trackers = {name: MotionTracker() for name in AI_VARIANTS}
    v1_history, v2_history = FeatureHistory(), FeatureHistoryV2()
    rows = defaultdict(list)
    for step in range(STEPS):
        timestamp = round(step * DT, 8)
        truth = route_position(environment.routes[ROUTE], timestamp, settings.speed)
        measurements, labels = generate_measurements(
            environment, settings, truth, timestamp, step
        )
        baseline = solve(measurements)
        stat_fix = solve(measurements, robust=True)
        state = None if stat_tracker.state is None else np.array(stat_tracker.state)
        state_time = stat_tracker.timestamp
        stat_tracking = stat_tracker.update(stat_fix, timestamp)
        _, bias = v1_model.predict_full(measurements, environment, stat_fix, v1_history)
        shared = v2_model.predict(
            measurements, environment, stat_fix, v2_history, state, state_time
        )
        errors = {
            "baseline": _position_error(baseline.position, truth),
            "stat": _position_error(stat_tracking.position, truth),
        }
        for name in AI_VARIANTS:
            variant_policy, candidate = policies[name]
            result = run_v2_policy(
                v2_model=v2_model,
                policy=variant_policy,
                candidate=candidate,
                measurements=measurements,
                environment=environment,
                stat_fix=stat_fix,
                v2_history=v2_history,
                tracker_state=state,
                tracker_timestamp=state_time,
                bias_by_anchor=bias,
                v2_output=shared,
            )
            tracking = trackers[name].update(result.estimate, timestamp, gate=result.gate)
            errors[name] = _position_error(tracking.position, truth)
        for name, value in errors.items():
            rows[f"{name}_error"].append(value)
        rows["run_id"].append(spec["run_index"])
        rows["count"].append(spec["count"])
        rows["layout_index"].append(spec["layout_index"])
        rows["n_available"].append(len(measurements))
        rows["n_nlos"].append(sum(label.kind == "nlos" for label in labels))
    return {key: np.asarray(value) for key, value in rows.items()}


def _scopes(frames: dict) -> dict[str, np.ndarray]:
    n, k = frames["n_available"], frames["n_nlos"]
    scopes = {
        "all": np.ones(len(n), dtype=bool),
        "solvable": n >= 3,
        "any_nlos": k > 0,
        "majority_nlos": (n >= 3) & (k > n / 2) & (k < n),
        "all_nlos": (n >= 3) & (k == n),
    }
    for count in COUNTS:
        scopes[f"anchors={count}"] = frames["count"] == count
    return scopes


COMPARISONS = (
    ("rc", "stat"),
    ("rc", "baseline"),
    ("weights", "stat"),
    ("rc", "weights"),
    ("scale", "weights"),
    ("gate", "weights"),
    ("all3", "rc"),
)


def summarize(frames: dict) -> dict:
    scopes = _scopes(frames)
    rmse = {
        scope: {m: _rmse(frames[f"{m}_error"][mask]) for m in METHODS}
        for scope, mask in scopes.items()
    }
    comparisons = []
    for scope, mask in scopes.items():
        for candidate, reference in COMPARISONS:
            row = _bootstrap_difference(
                frames[f"{reference}_error"][mask],
                frames[f"{candidate}_error"][mask],
                frames["run_id"][mask],
            )
            comparisons.append(
                {
                    "scope": scope,
                    "candidate": candidate,
                    "reference": reference,
                    **row,
                    "label": _scope_label(row),
                }
            )
    return {"rmse": rmse, "comparisons": comparisons}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--layouts", nargs="+", default=["hospital_floor_01", "second"])
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    specs = build_specs(args.seeds, args.layouts)
    print(f"{len(specs)} runs on validation seeds, {args.workers} workers", flush=True)
    with Pool(args.workers) as pool:
        results = list(pool.imap_unordered(evaluate_run, specs, chunksize=1))
    keys = results[0].keys()
    frames = {key: np.concatenate([r[key] for r in results]) for key in keys}
    summary = summarize(frames)
    summary["protocol"] = {
        "seed_source": "validation",
        "seeds": load_dataset_config()["splits"]["validation"]["seeds"][: args.seeds],
        "layouts": args.layouts,
        "runs": len(specs),
        "frames": int(len(frames["run_id"])),
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _write_json(OUTPUT / "summary.json", summary)
    print(json.dumps(summary["rmse"]["all"], indent=2))


if __name__ == "__main__":
    main()
