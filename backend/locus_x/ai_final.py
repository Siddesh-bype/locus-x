"""Frozen Phase 5 final test for LOCUS-X AI reliability.

The final test is intentionally separate from validation and policy tuning. It
uses only the reserved seeds, evaluates the geometric baseline, statistical
LOCUS-X, LOCUS-X + v1 and LOCUS-X + the frozen v2 policy on identical generated
observations, and writes one immutable result directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from functools import cache
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from .ai_ablation import BOOTSTRAP_SEED, metric_row, reliability_bins
from .ai_evaluation import _settings, base_variants, mixed_variants
from .ai_phase3 import _nlos_group
from .environment import load_environments, resolve_floor, route_position
from .estimators import solve
from .policy_v2 import load_policy, policy_hash, policy_weights
from .reliability import FeatureHistory, ReliabilityModel
from .reliability_v2 import FeatureHistoryV2, ReliabilityV2Model
from .simulation import generate_measurements
from .tracking import MotionTracker

ROOT = Path(__file__).resolve().parents[2]
FINAL_SEEDS = ROOT / "configs" / "ai" / "final-test-seeds-v2.json"
DATASET_CONFIG = ROOT / "configs" / "ai" / "reliability-dataset-v2.json"
V1_MODEL = ROOT / "models" / "ai-reliability-v1"
V2_MODEL = ROOT / "models" / "ai-reliability-v2"
POLICY = V2_MODEL / "policy-v2.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "ai-reliability" / "final-test"
DT = 0.5
STEPS = 120
COUNTS = (3, 4, 5, 6, 7, 8)
ROUTE = "held_out"
BOOTSTRAP = 2000
METHODS = ("baseline", "stat", "v1", "v2")
LAYOUTS = (
    ("hospital_floor_01", None, "hospital_floor_01"),
    ("hospital_v3", "ground", "ground"),
    ("hospital_v3", "first", "first"),
    ("hospital_v3", "second", "second"),
)
GROUP_NAMES = ("one", "minority", "half", "majority", "all")


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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), indent=2) + "\n", encoding="utf-8"
    )


def _write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_test_manifest() -> dict:
    return json.loads(FINAL_SEEDS.read_text(encoding="utf-8"))


def load_dataset_config() -> dict:
    return json.loads(DATASET_CONFIG.read_text(encoding="utf-8"))


def final_variants() -> list[dict]:
    return [*base_variants(), *mixed_variants()]


def build_specs() -> list[dict]:
    seeds = [int(seed) for seed in load_test_manifest()["fresh_seeds"]]
    subsets = load_dataset_config()["anchor_subsets"]
    variants = final_variants()
    specs = []
    run_index = 0
    for layout_index, (environment_id, floor_id, layout_name) in enumerate(LAYOUTS):
        for seed in seeds:
            for variant_index, variant in enumerate(variants):
                for count in COUNTS:
                    subset = subsets[str(count)][0]
                    specs.append(
                        {
                            "run_index": run_index,
                            "layout_index": layout_index,
                            "layout": layout_name,
                            "environment_id": environment_id,
                            "floor_id": floor_id,
                            "seed": seed,
                            "variant_index": variant_index,
                            "variant": variant,
                            "count": count,
                            "subset": subset,
                        }
                    )
                    run_index += 1
    return specs


@cache
def _models():
    v1 = ReliabilityModel.load(V1_MODEL)
    v2 = ReliabilityV2Model.load(V2_MODEL)
    if not v1.available:
        raise RuntimeError("v1 model is unavailable")
    if not v2.available:
        raise RuntimeError(f"v2 model is unavailable: {v2.error}")
    policy = load_policy(POLICY)
    candidate = {
        "threshold": float(policy["threshold"]),
        "floor": float(policy["floor"]),
        "fallback": "min3",
        "mode": str(policy["mode"]),
    }
    return v1, v2, policy, candidate


@cache
def _environment(environment_id: str, floor_id: str | None):
    return resolve_floor(load_environments()[environment_id], floor_id)


def _position_error(position, truth) -> float:
    if position is None:
        return float("nan")
    return float(np.linalg.norm(np.asarray(position) - np.asarray(truth)))


def _covered(truth, tracking) -> float:
    ellipse = tracking.ellipse
    if ellipse is None or tracking.position is None:
        return float("nan")
    dx = float(truth[0] - tracking.position[0])
    dy = float(truth[1] - tracking.position[1])
    cosine, sine = math.cos(-ellipse.angle), math.sin(-ellipse.angle)
    u = cosine * dx - sine * dy
    v = sine * dx + cosine * dy
    major = max(float(ellipse.major), 1e-12)
    minor = max(float(ellipse.minor), 1e-12)
    return float((u / major) ** 2 + (v / minor) ** 2 <= 1.0)


def _area(tracking) -> float:
    ellipse = tracking.ellipse
    if ellipse is None:
        return float("nan")
    return float(math.pi * ellipse.major * ellipse.minor)


def evaluate_run(spec: dict) -> dict:
    v1_model, v2_model, policy, candidate = _models()
    environment = _environment(spec["environment_id"], spec["floor_id"])
    settings = _settings(
        spec["variant"], spec["seed"], ROUTE, spec["subset"]
    )
    stat_tracker = MotionTracker()
    v1_tracker = MotionTracker()
    v2_tracker = MotionTracker()
    v1_history = FeatureHistory()
    v2_history = FeatureHistoryV2()

    output = defaultdict(list)
    classifier = defaultdict(list)
    for step in range(STEPS):
        timestamp = round(step * DT, 8)
        truth = route_position(environment.routes[ROUTE], timestamp, settings.speed)
        measurements, labels = generate_measurements(
            environment, settings, truth, timestamp, step
        )
        baseline = solve(measurements)
        stat_fix = solve(measurements, robust=True)
        previous_state = (
            None if stat_tracker.state is None else np.array(stat_tracker.state)
        )
        previous_timestamp = stat_tracker.timestamp
        stat_tracking = stat_tracker.update(stat_fix, timestamp)

        v1_output = v1_model.predict(measurements, environment, stat_fix, v1_history)
        v1_estimate = stat_fix
        v1_solve_ms = 0.0
        if v1_output.weights is not None:
            v1_estimate = solve(
                measurements, robust=True, ai_weights=v1_output.weights
            )
            v1_solve_ms = float(v1_estimate.runtime_ms)
        v1_tracking = v1_tracker.update(v1_estimate, timestamp)

        v2_output = v2_model.predict(
            measurements,
            environment,
            stat_fix,
            v2_history,
            previous_state,
            previous_timestamp,
        )
        predictions = v2_output.metadata.predictions
        v2_weights = None
        v2_fallback = True
        v2_changed = False
        if predictions:
            nlos = np.asarray([1.0 - prediction.p_los for prediction in predictions])
            weights, v2_fallback, v2_changed = policy_weights(
                nlos, len(predictions), candidate
            )
            if weights is not None:
                v2_weights = {
                    prediction.anchor_id: weights[str(index)]
                    for index, prediction in enumerate(predictions)
                }
        v2_estimate = stat_fix
        v2_solve_ms = 0.0
        if v2_weights is not None:
            v2_estimate = solve(
                measurements, robust=True, ai_weights=v2_weights
            )
            v2_solve_ms = float(v2_estimate.runtime_ms)
        v2_tracking = v2_tracker.update(v2_estimate, timestamp)

        n_available = len(measurements)
        n_nlos = sum(label.kind == "nlos" for label in labels)
        has_dropout = any(label.kind == "packet_loss" for label in labels)
        has_outage = any(label.kind == "outage" for label in labels)
        output["run_id"].append(spec["run_index"])
        output["layout_index"].append(spec["layout_index"])
        output["variant_index"].append(spec["variant_index"])
        output["count"].append(spec["count"])
        output["seed"].append(spec["seed"])
        output["step"].append(step)
        output["n_available"].append(n_available)
        output["n_nlos"].append(n_nlos)
        output["has_dropout"].append(int(has_dropout))
        output["has_outage"].append(int(has_outage))
        output["baseline_error"].append(
            _position_error(baseline.position, truth)
        )
        output["stat_error"].append(
            _position_error(stat_tracking.position, truth)
        )
        output["v1_error"].append(
            _position_error(v1_tracking.position, truth)
        )
        output["v2_error"].append(
            _position_error(v2_tracking.position, truth)
        )
        output["baseline_valid"].append(int(baseline.position is not None))
        output["stat_valid"].append(int(stat_tracking.position is not None))
        output["v1_valid"].append(int(v1_tracking.position is not None))
        output["v2_valid"].append(int(v2_tracking.position is not None))
        output["stat_updated"].append(int(stat_tracking.updated))
        output["v1_updated"].append(int(v1_tracking.updated))
        output["v2_updated"].append(int(v2_tracking.updated))
        output["stat_covered"].append(_covered(truth, stat_tracking))
        output["v1_covered"].append(_covered(truth, v1_tracking))
        output["v2_covered"].append(_covered(truth, v2_tracking))
        output["stat_area"].append(_area(stat_tracking))
        output["v1_area"].append(_area(v1_tracking))
        output["v2_area"].append(_area(v2_tracking))
        output["baseline_ms"].append(float(baseline.runtime_ms))
        output["stat_ms"].append(
            float(baseline.runtime_ms + stat_fix.runtime_ms + stat_tracking.runtime_ms)
        )
        output["v1_ms"].append(
            float(
                baseline.runtime_ms
                + stat_fix.runtime_ms
                + v1_output.metadata.runtime_ms
                + v1_solve_ms
                + v1_tracking.runtime_ms
            )
        )
        output["v2_ms"].append(
            float(
                baseline.runtime_ms
                + stat_fix.runtime_ms
                + v2_output.metadata.runtime_ms
                + v2_solve_ms
                + v2_tracking.runtime_ms
            )
        )
        output["v1_fallback"].append(int(v1_output.weights is None))
        output["v2_fallback"].append(int(v2_fallback))
        output["v2_changed"].append(int(v2_changed))
        output["v2_metadata_ms"].append(float(v2_output.metadata.runtime_ms))

        v1_probabilities = {
            prediction.anchor_id: prediction.p_los
            for prediction in v1_output.metadata.predictions
        }
        v2_probabilities = {
            prediction.anchor_id: prediction.p_los for prediction in predictions
        }
        for label in labels:
            if not label.available or label.kind not in ("los", "nlos"):
                continue
            p_v1 = v1_probabilities.get(label.anchor_id)
            p_v2 = v2_probabilities.get(label.anchor_id)
            if p_v1 is None or p_v2 is None:
                continue
            if not (np.isfinite(p_v1) and np.isfinite(p_v2)):
                continue
            classifier["run_id"].append(spec["run_index"])
            classifier["layout_index"].append(spec["layout_index"])
            classifier["variant_index"].append(spec["variant_index"])
            classifier["count"].append(spec["count"])
            classifier["seed"].append(spec["seed"])
            classifier["step"].append(step)
            classifier["y"].append(int(label.kind == "nlos"))
            classifier["p_v1"].append(float(p_v1))
            classifier["p_v2"].append(float(p_v2))
            classifier["condition"].append("packet_loss" if has_dropout else "no_loss")
    return {
        "run_index": spec["run_index"],
        "frames": {key: np.asarray(value) for key, value in output.items()},
        "classifier": {key: np.asarray(value) for key, value in classifier.items()},
    }


def _concat(results: list[dict]) -> tuple[dict, dict]:
    frame_keys = sorted({key for result in results for key in result["frames"]})
    sample_keys = sorted({key for result in results for key in result["classifier"]})
    frames = {
        key: np.concatenate([result["frames"][key] for result in results])
        for key in frame_keys
    }
    samples = {
        key: np.concatenate(
            [
                result["classifier"][key]
                for result in results
                if key in result["classifier"]
            ]
        )
        for key in sample_keys
    }
    return frames, samples


def _scope_masks(frames: dict, variant_names: list[str]) -> list[tuple[str, str, np.ndarray]]:
    n_available = frames["n_available"]
    n_nlos = frames["n_nlos"]
    dropout = frames["has_dropout"].astype(bool)
    outage = frames["has_outage"].astype(bool)
    masks = [
        ("all", "all", np.ones(len(n_available), dtype=bool)),
        ("solvable", "solvable", n_available >= 3),
        ("coasting", "coasting", n_available < 3),
    ]
    for index, (_, _, name) in enumerate(LAYOUTS):
        masks.append(("layout", name, frames["layout_index"] == index))
    for index, name in enumerate(variant_names):
        masks.append(("variant", name, frames["variant_index"] == index))
    for count in COUNTS:
        masks.append(("anchor_count", str(count), frames["count"] == count))
    masks.extend(
        [
            ("fault", "nlos", n_nlos > 0),
            ("fault", "all_nlos", (n_available >= 3) & (n_nlos == n_available)),
            (
                "fault",
                "majority_nlos",
                (n_available >= 3)
                & (n_nlos > n_available / 2)
                & (n_nlos < n_available),
            ),
            ("fault", "nlos_packet", (n_nlos > 0) & dropout),
            ("fault", "nlos_packet_outage", (n_nlos > 0) & dropout & outage),
        ]
    )
    return masks


def _rmse(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return None
    return float(np.sqrt(np.mean(finite**2)))


def _latency_summary(values: np.ndarray) -> dict:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return {"mean": None, "median": None, "p95": None}
    return {
        "mean": float(np.mean(finite)),
        "median": float(np.median(finite)),
        "p95": float(np.percentile(finite, 95)),
    }


def _metric_row(frames: dict, name: str, mask: np.ndarray, method: str) -> dict:
    error = frames[f"{method}_error"][mask]
    valid = frames[f"{method}_valid"][mask].astype(bool)
    updated = (
        frames[f"{method}_updated"][mask].astype(bool)
        if f"{method}_updated" in frames
        else valid
    )
    baseline_error = frames["stat_error"][mask]
    covered = (
        frames[f"{method}_covered"][mask]
        if f"{method}_covered" in frames
        else np.full(len(error), np.nan)
    )
    area = (
        frames[f"{method}_area"][mask]
        if f"{method}_area" in frames
        else np.full(len(error), np.nan)
    )
    matched = np.isfinite(error) & np.isfinite(baseline_error)
    prediction_only = valid & ~updated
    latency = frames[f"{method}_ms"][mask]
    finite_error = error[np.isfinite(error)]
    return {
        "scope": name,
        "method": method,
        "frames": int(len(error)),
        "valid_frames": int(np.sum(valid)),
        "rmse": _rmse(error),
        "median": (
            float(np.median(finite_error)) if finite_error.size else None
        ),
        "p95": (
            float(np.percentile(finite_error, 95))
            if finite_error.size
            else None
        ),
        "valid_rate": float(np.mean(valid)) if len(valid) else None,
        "update_rate": float(np.mean(updated)) if len(updated) else None,
        "prediction_only_frames": int(np.sum(prediction_only)),
        "prediction_only_rmse": _rmse(error[prediction_only]),
        "matched_frames": int(np.sum(matched)),
        "matched_rmse": _rmse(error[matched]),
        "coverage_95": float(np.nanmean(covered)) if np.isfinite(covered).any() else None,
        "mean_ellipse_area": float(np.nanmean(area)) if np.isfinite(area).any() else None,
        "latency_mean_ms": _latency_summary(latency)["mean"],
        "latency_p95_ms": _latency_summary(latency)["p95"],
    }


def _bootstrap_difference(
    x: np.ndarray,
    y: np.ndarray,
    run_ids: np.ndarray,
    *,
    resamples: int = BOOTSTRAP,
) -> dict:
    valid = np.isfinite(x) & np.isfinite(y)
    x = x[valid]
    y = y[valid]
    run_ids = run_ids[valid]
    if x.size == 0:
        return {
            "x_rmse": None,
            "y_rmse": None,
            "difference": None,
            "ci_low": None,
            "ci_high": None,
            "relative_percent": None,
            "relative_ci_low": None,
            "relative_ci_high": None,
            "runs": 0,
            "frames": 0,
        }
    unique, inverse = np.unique(run_ids, return_inverse=True)
    x_sq = np.bincount(inverse, weights=x**2, minlength=len(unique))
    y_sq = np.bincount(inverse, weights=y**2, minlength=len(unique))
    counts = np.bincount(inverse, minlength=len(unique))
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    chosen = rng.integers(0, len(unique), size=(resamples, len(unique)))
    x_rmse = np.sqrt(x_sq[chosen].sum(axis=1) / counts[chosen].sum(axis=1))
    y_rmse = np.sqrt(y_sq[chosen].sum(axis=1) / counts[chosen].sum(axis=1))
    differences = y_rmse - x_rmse
    observed_x = _rmse(x)
    observed_y = _rmse(y)
    observed = observed_y - observed_x
    return {
        "x_rmse": observed_x,
        "y_rmse": observed_y,
        "difference": observed,
        "ci_low": float(np.percentile(differences, 2.5)),
        "ci_high": float(np.percentile(differences, 97.5)),
        "relative_percent": (
            100.0 * observed / observed_x
            if observed_x not in (None, 0)
            else None
        ),
        "relative_ci_low": (
            100.0 * float(np.percentile(differences, 2.5)) / observed_x
            if observed_x not in (None, 0)
            else None
        ),
        "relative_ci_high": (
            100.0 * float(np.percentile(differences, 97.5)) / observed_x
            if observed_x not in (None, 0)
            else None
        ),
        "runs": int(len(unique)),
        "frames": int(len(x)),
    }


def _scope_label(row: dict) -> str:
    low = row["ci_low"]
    high = row["ci_high"]
    relative = row["relative_percent"]
    if high is not None and high < 0:
        return "improved"
    if low is not None and low > 0 and relative is not None and relative > 0.5:
        return "regressed"
    return "unchanged"


def build_reports(
    output: Path,
    frames: dict,
    samples: dict,
    specs: list[dict],
    policy: dict,
) -> dict:
    variant_names = [variant["id"] for variant in final_variants()]
    masks = _scope_masks(frames, variant_names)
    metrics = []
    for scope, name, mask in masks:
        if not np.any(mask):
            continue
        for method in METHODS:
            row = _metric_row(frames, name, mask, method)
            row["scope"] = scope
            row["name"] = name
            metrics.append(row)

    comparisons = (
        ("v2_vs_stat", "v2", "stat"),
        ("v2_vs_v1", "v2", "v1"),
        ("v1_vs_stat", "v1", "stat"),
    )
    bootstrap = []
    for scope, name, mask in masks:
        if not np.any(mask):
            continue
        for comparison, left, right in comparisons:
            row = _bootstrap_difference(
                frames[f"{right}_error"][mask],
                frames[f"{left}_error"][mask],
                frames["run_id"][mask],
            )
            bootstrap.append(
                {
                    "scope": scope,
                    "name": name,
                    "comparison": comparison,
                    **row,
                    "label": _scope_label(row),
                }
            )

    group = _nlos_group(frames["n_nlos"], frames["n_available"])
    groups = []
    for index, name in enumerate(GROUP_NAMES):
        mask = (group == index) & (frames["n_available"] >= 3)
        if not np.any(mask):
            continue
        for method in METHODS:
            row = _metric_row(frames, name, mask, method)
            row["scope"] = "nlos_group"
            groups.append(row)

    classifier_rows = []
    scopes = [
        ("all", np.ones(len(samples["y"]), dtype=bool)),
        ("condition", samples["condition"] == "no_loss"),
        ("condition", samples["condition"] == "packet_loss"),
    ]
    for index, (_, _, name) in enumerate(LAYOUTS):
        scopes.append((f"layout:{name}", samples["layout_index"] == index))
    for count in COUNTS:
        scopes.append((f"anchor_count:{count}", samples["count"] == count))
    for index, name in enumerate(variant_names):
        scopes.append((f"variant:{name}", samples["variant_index"] == index))
    for scope_name, mask in scopes:
        if not np.any(mask):
            continue
        for model, key in (("v1", "p_v1"), ("v2", "p_v2")):
            row = metric_row(samples["y"][mask], samples[key][mask])
            classifier_rows.append(
                {"scope": scope_name, "model": model, **row}
            )

    calibration = []
    for scope_name, mask in [
        ("all", np.ones(len(samples["y"]), dtype=bool)),
        *[(f"anchor_count:{count}", samples["count"] == count) for count in COUNTS],
        ("condition:no_loss", samples["condition"] == "no_loss"),
        ("condition:packet_loss", samples["condition"] == "packet_loss"),
    ]:
        if not np.any(mask):
            continue
        for model, key in (("v1", "p_v1"), ("v2", "p_v2")):
            for row in reliability_bins(samples["y"][mask], samples[key][mask]):
                calibration.append(
                    {"scope": scope_name, "model": model, **row}
                )

    latency = {
        method: _latency_summary(frames[f"{method}_ms"])
        for method in METHODS
    }
    latency["v2_adapter"] = _latency_summary(frames["v2_metadata_ms"])

    _write_csv(output / "metrics.csv", metrics)
    _write_csv(output / "bootstrap.csv", bootstrap)
    _write_csv(output / "nlos-groups.csv", groups)
    _write_csv(output / "classifier.csv", classifier_rows)
    _write_csv(output / "calibration.csv", calibration)
    _write_json(output / "latency.json", latency)
    _write_json(
        output / "summary.json",
        {
            "status": "complete",
            "scope": "fresh final-test seeds; no validation seeds",
            "methods": list(METHODS),
            "policy": {
                "policy_id": policy["policy_id"],
                "policy_sha256": policy_hash(policy),
                "selection": policy["selection_rule"],
            },
            "metrics": metrics,
            "bootstrap": bootstrap,
            "nlos_groups": groups,
            "classifier": classifier_rows,
            "latency": latency,
            "runs": len(specs),
            "frames": int(len(frames["run_id"])),
            "classifier_samples": int(len(samples["y"])),
        },
    )
    return {"metrics": metrics, "bootstrap": bootstrap, "classifier": classifier_rows}


def run(output: Path = DEFAULT_OUTPUT, workers: int = 18) -> None:
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite final-test output: {output}")
    specs = build_specs()
    output.mkdir(parents=True)
    _write_json(
        output / "run-catalog.json",
        [
            {
                key: value
                for key, value in spec.items()
                if key != "variant"
            }
            | {"variant": spec["variant"]}
            for spec in specs
        ],
    )
    with Pool(processes=workers) as pool:
        results = list(pool.imap_unordered(evaluate_run, specs, chunksize=1))
    results.sort(key=lambda item: item["run_index"])
    frames, samples = _concat(results)
    np.savez_compressed(output / "frames.npz", **frames)
    np.savez_compressed(output / "classifier-samples.npz", **samples)
    policy = load_policy(POLICY)
    reports = build_reports(output, frames, samples, specs, policy)
    _write_json(
        output / "manifest.json",
        {
            "created_at": datetime.now(UTC).isoformat(),
            "command": "python -m backend.locus_x.ai_final",
            "protocol": {
                "seeds": load_test_manifest()["fresh_seeds"],
                "route": ROUTE,
                "dt": DT,
                "steps": STEPS,
                "layouts": [layout[2] for layout in LAYOUTS],
                "variants": [variant["id"] for variant in final_variants()],
                "counts": list(COUNTS),
            },
            "runs": len(specs),
            "frames": int(len(frames["run_id"])),
            "classifier_samples": int(len(samples["y"])),
            "bootstrap_resamples": BOOTSTRAP,
            "policy_sha256": policy_hash(policy),
            "test_manifest_sha256": _sha256(FINAL_SEEDS),
            "model_sha256": {
                path.name: _sha256(path)
                for path in sorted(V1_MODEL.glob("*"))
                if path.is_file()
            }
            | {
                f"v2/{path.name}": _sha256(path)
                for path in sorted(V2_MODEL.glob("*"))
                if path.is_file()
            },
            "report_rows": {
                "metrics": len(reports["metrics"]),
                "bootstrap": len(reports["bootstrap"]),
                "classifier": len(reports["classifier"]),
            },
        },
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "runs": len(specs),
                "frames": int(len(frames["run_id"])),
                "classifier_samples": int(len(samples["y"])),
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=18)
    args = parser.parse_args()
    run(args.output, args.workers)


if __name__ == "__main__":
    main()
