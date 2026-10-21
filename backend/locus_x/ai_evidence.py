"""Fresh-seed Phase 1 evidence for the frozen AI reliability model.

This module does not train, retune, or modify the model. It reuses the existing
simulation session so every method sees identical observations and records the
metrics that were missing from the earlier final-evaluation artifacts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import numpy as np

from .ai_evaluation import _settings, base_variants, mixed_variants
from .benchmark import ANCHOR_SELECTION
from .environment import load_environments, resolve_floor
from .reliability import ReliabilityModel
from .session import SimulationSession
from .tracking import CHI2_95_2D

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT / "models" / "ai-reliability-v1"
DEFAULT_SEEDS = ROOT / "configs" / "ai" / "final-test-seeds-v1.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "ai-reliability" / "evidence-v1-fresh"
METHODS = ("baseline", "proposed", "ai")
METHOD_FIELDS = (
    "error",
    "matched",
    "prediction",
    "valid",
    "updated",
    "coverage",
    "area",
    "latency",
)
METHOD_LABELS = {
    "baseline": "geometric baseline",
    "proposed": "LOCUS-X",
    "ai": "AI-assisted LOCUS-X",
}
BOOTSTRAP_SEED = 20261021


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
    path.write_text(json.dumps(_jsonable(payload), indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _checkpoint_path(directory: Path, layout: str, variant: str, count: int, seed: int) -> Path:
    return directory / f"{layout}__{variant}__{count}__{seed}.npz"


def _save_checkpoint(path: Path, run: dict) -> None:
    payload = {
        "meta__layout": np.asarray(run["layout"]),
        "meta__environment_id": np.asarray(run["environment_id"]),
        "meta__floor_id": np.asarray(run["floor_id"] if run["floor_id"] is not None else ""),
        "meta__role": np.asarray(run["role"]),
        "meta__scenario": np.asarray(run["scenario"]),
        "meta__variant": np.asarray(run["variant"]),
        "meta__count": np.asarray(run["count"]),
        "meta__seed": np.asarray(run["seed"]),
        "meta__duration": np.asarray(run["duration_seconds"]),
    }
    for method in METHODS:
        for field in METHOD_FIELDS:
            payload[f"{method}__{field}"] = run["methods"][method][field]
        payload[f"{method}__frame_error"] = run["frames"]["error"][method]
        payload[f"{method}__frame_prediction"] = run["frames"]["prediction"][method]
    payload["frame__available"] = run["frames"]["available"]
    payload["frame__nlos"] = run["frames"]["nlos"]
    if run["classifier"] is not None:
        payload["classifier__scores"] = run["classifier"]["scores"]
        payload["classifier__targets"] = run["classifier"]["targets"]
    np.savez_compressed(path, **payload)


def _load_checkpoint(path: Path) -> dict:
    data = np.load(path, allow_pickle=False)
    classifier = None
    if "classifier__scores" in data.files:
        classifier = {
            "scores": np.asarray(data["classifier__scores"], dtype=float),
            "targets": np.asarray(data["classifier__targets"], dtype=float),
        }
    floor_id = str(data["meta__floor_id"])
    return {
        "layout": str(data["meta__layout"]),
        "environment_id": str(data["meta__environment_id"]),
        "floor_id": floor_id or None,
        "role": str(data["meta__role"]),
        "scenario": str(data["meta__scenario"]),
        "variant": str(data["meta__variant"]),
        "count": int(data["meta__count"]),
        "seed": int(data["meta__seed"]),
        "duration_seconds": float(data["meta__duration"]),
        "methods": {
            method: {
                field: np.asarray(data[f"{method}__{field}"], dtype=float)
                if field not in ("valid", "updated", "coverage")
                else np.asarray(data[f"{method}__{field}"], dtype=bool)
                for field in METHOD_FIELDS
            }
            for method in METHODS
        },
        "classifier": classifier,
        "frames": {
            "available": np.asarray(data["frame__available"], dtype=int),
            "nlos": np.asarray(data["frame__nlos"], dtype=int),
            "error": {
                method: np.asarray(data[f"{method}__frame_error"], dtype=float)
                for method in METHODS
            },
            "prediction": {
                method: np.asarray(data[f"{method}__frame_prediction"], dtype=bool)
                for method in METHODS
            },
        },
    }


def _concatenate(arrays: list[np.ndarray]) -> np.ndarray:
    arrays = [array for array in arrays if array.size]
    return np.concatenate(arrays) if arrays else np.empty(0, dtype=float)


def _error_metrics(values: np.ndarray) -> dict:
    values = values[np.isfinite(values)]
    if not values.size:
        return {"n": 0, "rmse": None, "median": None, "p95": None, "mean": None}
    return {
        "n": int(values.size),
        "rmse": float(np.sqrt(np.mean(values**2))),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "mean": float(np.mean(values)),
    }


def _quantiles(values: np.ndarray) -> dict:
    values = values[np.isfinite(values)]
    if not values.size:
        return {"n": 0}
    points = np.percentile(values, [0, 5, 25, 50, 75, 95, 99, 100])
    return {
        "n": int(values.size),
        "mean": float(np.mean(values)),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "min": float(points[0]),
        "p5": float(points[1]),
        "p25": float(points[2]),
        "p75": float(points[4]),
        "p99": float(points[6]),
        "max": float(points[7]),
    }


def _covariance_area(covariance) -> float | None:
    if covariance is None:
        return None
    values = np.linalg.eigvalsh(np.asarray(covariance, dtype=float))
    radii = np.sqrt(np.maximum(values, 0.0) * CHI2_95_2D)
    return float(np.pi * radii[0] * radii[1])


def _ellipse_area(ellipse) -> float | None:
    if ellipse is None:
        return None
    return float(np.pi * ellipse.major * ellipse.minor)


def _estimate_coverage(truth, estimate) -> bool | None:
    if estimate.position is None or estimate.covariance is None:
        return None
    delta = np.asarray(estimate.position, dtype=float) - np.asarray(truth, dtype=float)
    covariance = np.asarray(estimate.covariance, dtype=float)
    try:
        distance = float(delta @ np.linalg.solve(covariance, delta))
    except np.linalg.LinAlgError:
        return None
    return distance <= CHI2_95_2D


def _positions(frame) -> dict[str, bool]:
    return {
        "baseline": frame.baseline.position is not None,
        "proposed": frame.tracking.position is not None,
        "ai": frame.ai_tracking.position is not None if frame.ai_tracking is not None else False,
    }


def _classifier_observations(frame) -> tuple[np.ndarray, np.ndarray]:
    labels = {label.anchor_id: label for label in frame.simulator_labels}
    probabilities = {
        prediction.anchor_id: 1.0 - prediction.p_los
        for prediction in (frame.ai_metadata.predictions if frame.ai_metadata is not None else [])
    }
    scores, targets = [], []
    for measurement in frame.measurements:
        label = labels.get(measurement.anchor_id)
        if label is None or label.kind not in ("los", "nlos"):
            continue
        score = probabilities.get(measurement.anchor_id)
        if score is None:
            continue
        scores.append(score)
        targets.append(1.0 if label.kind == "nlos" else 0.0)
    return np.asarray(scores, dtype=float), np.asarray(targets, dtype=float)


def _new_method() -> dict:
    return {
        "error": [],
        "matched": [],
        "prediction": [],
        "valid": [],
        "updated": [],
        "coverage": [],
        "area": [],
        "latency": [],
    }


def _run_session(
    environment,
    settings,
    model,
    steps: int,
    dt: float,
    collect_classifier: bool,
):
    session = SimulationSession(environment, settings)
    session.ai_model = model
    session.restart()
    methods = {method: _new_method() for method in METHODS}
    frame_available, frame_nlos = [], []
    frame_errors = {method: [] for method in METHODS}
    frame_prediction = {method: [] for method in METHODS}
    scores, targets = [], []
    for _ in range(steps):
        frame = session.advance(dt)
        truth = frame.evaluation.ground_truth
        available = _positions(frame)
        matched = all(available.values())
        errors = {
            "baseline": frame.evaluation.baseline_error,
            "proposed": frame.evaluation.proposed_error,
            "ai": frame.evaluation.ai_error,
        }
        n_available = len(frame.measurements)
        n_nlos = sum(label.kind == "nlos" for label in frame.simulator_labels)
        frame_available.append(n_available)
        frame_nlos.append(n_nlos)
        for method in METHODS:
            estimate = {
                "baseline": frame.baseline,
                "proposed": frame.robust,
                "ai": frame.ai,
            }[method]
            tracking = {
                "baseline": None,
                "proposed": frame.tracking,
                "ai": frame.ai_tracking,
            }[method]
            error = errors[method]
            prediction_only = (
                tracking is not None and tracking.position is not None and not tracking.updated
            )
            frame_errors[method].append(float(error) if error is not None else np.nan)
            frame_prediction[method].append(bool(prediction_only))
            if error is not None:
                methods[method]["error"].append(float(error))
                if matched:
                    methods[method]["matched"].append(float(error))
            if prediction_only and error is not None:
                methods[method]["prediction"].append(float(error))
            methods[method]["valid"].append(bool(available[method]))
            methods[method]["updated"].append(
                bool(available[method]) if method == "baseline" else bool(tracking.updated)
            )
            if method == "baseline":
                coverage = _estimate_coverage(truth, estimate)
                area = _covariance_area(estimate.covariance)
            else:
                coverage = (
                    frame.evaluation.covered_95
                    if method == "proposed"
                    else frame.evaluation.ai_covered_95
                )
                area = _ellipse_area(tracking.ellipse)
            if coverage is not None:
                methods[method]["coverage"].append(bool(coverage))
            if area is not None:
                methods[method]["area"].append(float(area))
            latency = (
                frame.baseline.runtime_ms
                if method == "baseline"
                else frame.robust.runtime_ms + frame.tracking.runtime_ms
                if method == "proposed"
                else frame.baseline.runtime_ms
                + (frame.ai_metadata.runtime_ms if frame.ai_metadata is not None else 0.0)
                + (frame.ai.runtime_ms if frame.ai is not None else 0.0)
                + (frame.ai_tracking.runtime_ms if frame.ai_tracking is not None else 0.0)
            )
            methods[method]["latency"].append(float(latency))
        if collect_classifier:
            frame_scores, frame_targets = _classifier_observations(frame)
            scores.extend(frame_scores.tolist())
            targets.extend(frame_targets.tolist())
    return {
        "methods": {
            method: {key: np.asarray(value) for key, value in data.items()}
            for method, data in methods.items()
        },
        "frames": {
            "available": np.asarray(frame_available, dtype=int),
            "nlos": np.asarray(frame_nlos, dtype=int),
            "error": {
                method: np.asarray(values, dtype=float)
                for method, values in frame_errors.items()
            },
            "prediction": {
                method: np.asarray(values, dtype=bool)
                for method, values in frame_prediction.items()
            },
        },
        "classifier": {
            "scores": np.asarray(scores, dtype=float),
            "targets": np.asarray(targets, dtype=float),
        }
        if collect_classifier
        else None,
    }


def _bootstrap_difference(
    runs: list[dict],
    method_a: str,
    method_b: str,
    field: str,
    resamples: int,
) -> dict:
    sums_a = np.asarray(
        [np.nansum(run["methods"][method_a][field] ** 2) for run in runs], dtype=float
    )
    counts_a = np.asarray(
        [np.isfinite(run["methods"][method_a][field]).sum() for run in runs], dtype=float
    )
    sums_b = np.asarray(
        [np.nansum(run["methods"][method_b][field] ** 2) for run in runs], dtype=float
    )
    counts_b = np.asarray(
        [np.isfinite(run["methods"][method_b][field]).sum() for run in runs], dtype=float
    )
    if not len(runs):
        return {"n_runs": 0, "mean": None, "low": None, "high": None}
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(runs), size=(resamples, len(runs)))
    with np.errstate(invalid="ignore", divide="ignore"):
        rmse_a = np.sqrt(sums_a[indices].sum(axis=1) / counts_a[indices].sum(axis=1))
        rmse_b = np.sqrt(sums_b[indices].sum(axis=1) / counts_b[indices].sum(axis=1))
        differences = rmse_a - rmse_b
    differences = differences[np.isfinite(differences)]
    if not differences.size:
        return {"n_runs": len(runs), "mean": None, "low": None, "high": None}
    return {
        "n_runs": len(runs),
        "mean": float(np.mean(differences)),
        "low": float(np.percentile(differences, 2.5)),
        "high": float(np.percentile(differences, 97.5)),
    }


def _decision(interval: dict) -> str:
    if interval["low"] is None or interval["high"] is None:
        return "undetermined"
    if interval["high"] < 0:
        return "improved"
    if interval["low"] > 0:
        return "regressed"
    return "unchanged"


def _method_summary(runs: list[dict], method: str) -> dict:
    data = [run["methods"][method] for run in runs]
    errors = _concatenate([item["error"] for item in data])
    matched = _concatenate([item["matched"] for item in data])
    prediction = _concatenate([item["prediction"] for item in data])
    latency = _concatenate([item["latency"] for item in data])
    coverage = _concatenate([item["coverage"].astype(float) for item in data])
    area = _concatenate([item["area"] for item in data])
    frames = sum(len(item["valid"]) for item in data)
    valid = sum(int(np.sum(item["valid"])) for item in data)
    updated = sum(int(np.sum(item["updated"])) for item in data)
    result = {
        **_error_metrics(errors),
        "valid_rate": valid / frames if frames else None,
        "update_rate": updated / frames if frames else None,
        "matched": _error_metrics(matched),
        "prediction_only": _error_metrics(prediction),
        "coverage_rate": float(np.mean(coverage)) if coverage.size else None,
        "coverage_n": int(coverage.size),
        "mean_uncertainty_area_m2": float(np.mean(area)) if area.size else None,
        "latency_ms": _quantiles(latency),
    }
    return result


def _row(
    scope: str,
    runs: list[dict],
    bootstrap_resamples: int,
    layout: str = "",
    scenario: str = "",
    anchors: str = "",
) -> list[dict]:
    rows = []
    summary = {method: _method_summary(runs, method) for method in METHODS}
    locus_rmse = summary["proposed"]["rmse"]
    interval = _bootstrap_difference(runs, "ai", "proposed", "error", bootstrap_resamples)
    matched_interval = _bootstrap_difference(runs, "ai", "proposed", "matched", bootstrap_resamples)
    ai_rmse = summary["ai"]["rmse"]
    for method in METHODS:
        item = summary[method]
        rows.append(
            {
                "scope": scope,
                "layout": layout,
                "scenario": scenario,
                "anchors": anchors,
                "method": method,
                "method_label": METHOD_LABELS[method],
                "frames": sum(len(run["methods"][method]["valid"]) for run in runs),
                "valid_n": item["n"],
                "valid_rate": item["valid_rate"],
                "update_rate": item["update_rate"],
                "rmse": item["rmse"],
                "median": item["median"],
                "p95": item["p95"],
                "mean_error": item["mean"],
                "matched_n": item["matched"]["n"],
                "matched_rmse": item["matched"]["rmse"],
                "prediction_only_n": item["prediction_only"]["n"],
                "prediction_only_rmse": item["prediction_only"]["rmse"],
                "coverage_rate": item["coverage_rate"],
                "coverage_n": item["coverage_n"],
                "mean_uncertainty_area_m2": item["mean_uncertainty_area_m2"],
                "latency_mean_ms": item["latency_ms"].get("mean"),
                "latency_median_ms": item["latency_ms"].get("median"),
                "latency_p95_ms": item["latency_ms"].get("p95"),
                "ai_minus_locus_rmse": interval["mean"] if method == "ai" else None,
                "ai_minus_locus_ci_low": interval["low"] if method == "ai" else None,
                "ai_minus_locus_ci_high": interval["high"] if method == "ai" else None,
                "decision": _decision(interval) if method == "ai" else None,
                "ai_minus_locus_matched_rmse": (
                    matched_interval["mean"] if method == "ai" else None
                ),
                "ai_minus_locus_matched_ci_low": (
                    matched_interval["low"] if method == "ai" else None
                ),
                "ai_minus_locus_matched_ci_high": (
                    matched_interval["high"] if method == "ai" else None
                ),
                "relative_improvement_pct": (
                    (locus_rmse - ai_rmse) / locus_rmse * 100
                    if method == "ai" and locus_rmse
                    else None
                ),
            }
        )
    return rows


def _build_groups(runs: list[dict]) -> dict:
    groups = {}
    for run in runs:
        for index, (available, nlos) in enumerate(
            zip(run["frames"]["available"], run["frames"]["nlos"], strict=True)
        ):
            key = (int(available), int(nlos))
            group = groups.setdefault(
                key,
                {
                    "frames": 0,
                    "solvable": int(available) >= 3,
                    "available_anchors": int(available),
                    "nlos_anchors": int(nlos),
                    "methods": {method: {"errors": [], "prediction": []} for method in METHODS},
                },
            )
            group["frames"] += 1
            for method in METHODS:
                error = run["frames"]["error"][method][index]
                if np.isfinite(error):
                    group["methods"][method]["errors"].append(float(error))
                    if run["frames"]["prediction"][method][index]:
                        group["methods"][method]["prediction"].append(float(error))
    return groups


def _group_rows(groups: dict, scope: str) -> list[dict]:
    rows = []
    for key in sorted(groups):
        group = groups[key]
        for method in METHODS:
            errors = np.asarray(group["methods"][method]["errors"])
            prediction = np.asarray(group["methods"][method]["prediction"])
            row = {
                "scope": scope,
                "available_anchors": group["available_anchors"],
                "nlos_anchors": group["nlos_anchors"],
                "solvable": group["solvable"],
                "frames": group["frames"],
                "method": method,
                "valid_n": int(errors.size),
                **_error_metrics(errors),
                "prediction_only_n": int(prediction.size),
                "prediction_only_rmse": _error_metrics(prediction)["rmse"],
            }
            rows.append(row)
    return rows


def _named_group(keys, groups: dict) -> dict:
    frames = sum(groups[key]["frames"] for key in keys)
    methods = {}
    for method in METHODS:
        errors = np.concatenate(
            [np.asarray(groups[key]["methods"][method]["errors"]) for key in keys]
        )
        prediction = np.concatenate(
            [np.asarray(groups[key]["methods"][method]["prediction"]) for key in keys]
        )
        methods[method] = {
            **_error_metrics(errors),
            "prediction_only": _error_metrics(prediction),
        }
    return {"frames": frames, "methods": methods}


def _roc_auc(scores: np.ndarray, targets: np.ndarray) -> float | None:
    positives = int(np.sum(targets == 1))
    negatives = int(np.sum(targets == 0))
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(scores, kind="mergesort")
    sorted_scores = scores[order]
    ranks = np.empty(len(scores), dtype=float)
    start = 0
    while start < len(sorted_scores):
        end = start + 1
        while end < len(sorted_scores) and sorted_scores[end] == sorted_scores[start]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    positive_rank_sum = float(np.sum(ranks[targets == 1]))
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (positives * negatives)


def _average_precision(scores: np.ndarray, targets: np.ndarray) -> float | None:
    positives = int(np.sum(targets == 1))
    if positives == 0:
        return None
    order = np.argsort(-scores, kind="mergesort")
    sorted_targets = targets[order]
    true_positive = np.cumsum(sorted_targets)
    predicted_positive = np.arange(1, len(scores) + 1)
    precision = true_positive / predicted_positive
    return float(np.sum(precision[sorted_targets == 1]) / positives)


def _threshold_metrics(scores: np.ndarray, targets: np.ndarray) -> dict:
    predicted = scores >= 0.5
    true_positive = int(np.sum(predicted & (targets == 1)))
    false_positive = int(np.sum(predicted & (targets == 0)))
    false_negative = int(np.sum(~predicted & (targets == 1)))
    true_negative = int(np.sum(~predicted & (targets == 0)))
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    recall = (
        true_positive / (true_positive + false_negative)
        if true_positive + false_negative
        else 0.0
    )
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (true_positive + true_negative) / len(scores) if len(scores) else None
    return {
        "threshold": 0.5,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "true_negative": true_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
    }


def _calibration(scores: np.ndarray, targets: np.ndarray) -> tuple[list[dict], float | None]:
    bins = []
    edges = np.linspace(0.0, 1.0, 11)
    ece = 0.0
    for index in range(10):
        low, high = edges[index], edges[index + 1]
        if index == 9:
            mask = (scores >= low) & (scores <= high)
        else:
            mask = (scores >= low) & (scores < high)
        count = int(np.sum(mask))
        if count:
            mean_score = float(np.mean(scores[mask]))
            observed = float(np.mean(targets[mask]))
            ece += count / len(scores) * abs(observed - mean_score)
        else:
            mean_score = observed = None
        bins.append(
            {
                "low": float(low),
                "high": float(high),
                "count": count,
                "mean_predicted_nlos": mean_score,
                "observed_nlos_rate": observed,
            }
        )
    return bins, ece


def _pr_points(scores: np.ndarray, targets: np.ndarray) -> list[dict]:
    positives = int(np.sum(targets == 1))
    if positives == 0:
        return []
    quantiles = np.unique(np.quantile(scores, np.linspace(0.0, 1.0, 201)))
    points = []
    for threshold in np.sort(quantiles)[::-1]:
        predicted = scores >= threshold
        true_positive = int(np.sum(predicted & (targets == 1)))
        false_positive = int(np.sum(predicted & (targets == 0)))
        points.append(
            {
                "threshold": float(threshold),
                "precision": (
                    true_positive / (true_positive + false_positive)
                    if true_positive + false_positive
                    else 0.0
                ),
                "recall": true_positive / positives,
            }
        )
    return points


def _classify(scores: np.ndarray, targets: np.ndarray) -> dict:
    threshold = _threshold_metrics(scores, targets)
    bins, ece = _calibration(scores, targets)
    return {
        "n": int(len(scores)),
        "positive_n": int(np.sum(targets == 1)),
        "negative_n": int(np.sum(targets == 0)),
        "roc_auc": _roc_auc(scores, targets),
        "average_precision": _average_precision(scores, targets),
        **threshold,
        "brier": float(np.mean((scores - targets) ** 2)) if len(scores) else None,
        "ece": ece,
        "reliability": bins,
    }


def _classifier_rows(runs: list[dict], bootstrap_resamples: int) -> list[dict]:
    grouped = defaultdict(list)
    for run in runs:
        if run["classifier"] is not None:
            grouped[(run["layout"], run["scenario"], run["variant"])].append(run["classifier"])
    rows = []
    for (layout, scenario, variant), items in sorted(grouped.items()):
        scores = _concatenate([item["scores"] for item in items])
        targets = _concatenate([item["targets"] for item in items])
        metrics = _classify(scores, targets)
        rows.append(
            {
                "scope": "layout_scenario",
                "layout": layout,
                "scenario": scenario,
                "variant": variant,
                "n": metrics["n"],
                "positive_n": metrics["positive_n"],
                "negative_n": metrics["negative_n"],
                "roc_auc": metrics["roc_auc"],
                "average_precision": metrics["average_precision"],
                "precision": metrics["precision"],
                "recall": metrics["recall"],
                "f1": metrics["f1"],
                "brier": metrics["brier"],
                "ece": metrics["ece"],
            }
        )
    return rows


def _classifier_aggregates(runs: list[dict], scope: str) -> list[dict]:
    grouped = defaultdict(list)
    for run in runs:
        if run["classifier"] is None:
            continue
        key = (run["layout"],) if scope == "layout" else (run["scenario"],)
        grouped[key].append(run["classifier"])
    rows = []
    for key, items in sorted(grouped.items()):
        scores = _concatenate([item["scores"] for item in items])
        targets = _concatenate([item["targets"] for item in items])
        metrics = _classify(scores, targets)
        row = {
            "scope": scope,
            "layout": key[0] if scope == "layout" else "",
            "scenario": key[0] if scope == "scenario" else "",
            **{field: metrics[field] for field in (
                "n", "positive_n", "negative_n", "roc_auc", "average_precision",
                "precision", "recall", "f1", "brier", "ece",
            )},
        }
        rows.append(row)
    return rows


def _pr_curves(runs: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for run in runs:
        if run["classifier"] is not None:
            grouped[("layout", run["layout"])].append(run["classifier"])
            grouped[("scenario", run["scenario"])].append(run["classifier"])
    curves = []
    for (scope, name), items in sorted(grouped.items()):
        scores = _concatenate([item["scores"] for item in items])
        targets = _concatenate([item["targets"] for item in items])
        curves.append(
            {
                "scope": scope,
                "name": name,
                "points": _pr_points(scores, targets),
            }
        )
    return curves


def _reliability_curves(runs: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for run in runs:
        if run["classifier"] is not None:
            grouped[("layout", run["layout"])].append(run["classifier"])
            grouped[("scenario", run["scenario"])].append(run["classifier"])
    curves = []
    for (scope, name), items in sorted(grouped.items()):
        scores = _concatenate([item["scores"] for item in items])
        targets = _concatenate([item["targets"] for item in items])
        bins, ece = _calibration(scores, targets)
        curves.append({"scope": scope, "name": name, "bins": bins, "ece": ece})
    return curves


def _latency_distributions(runs: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for run in runs:
        for method in METHODS:
            key = (run["layout"], run["scenario"], method)
            grouped[key].append(run["methods"][method]["latency"])
    rows = []
    for (layout, scenario, method), arrays in sorted(grouped.items()):
        values = _concatenate(arrays)
        rows.append(
            {
                "layout": layout,
                "scenario": scenario,
                "method": method,
                **_quantiles(values),
            }
        )
    return rows


def _run_records(runs: list[dict]) -> list[dict]:
    rows = []
    for run in runs:
        for method in METHODS:
            errors = run["methods"][method]["error"]
            rows.append(
                {
                    "layout": run["layout"],
                    "floor_id": run["floor_id"],
                    "scenario": run["scenario"],
                    "variant": run["variant"],
                    "anchors": run["count"],
                    "seed": run["seed"],
                    "method": method,
                    "frames": len(errors),
                    "rmse": _error_metrics(errors)["rmse"],
                    "sum_squared_error": float(np.nansum(errors**2)),
                    "duration_seconds": run["duration_seconds"],
                }
            )
    return rows


def evaluate(
    model: ReliabilityModel,
    seed_config: dict,
    output: Path,
    model_dir: Path,
    steps: int | None = None,
    dt: float | None = None,
) -> dict:
    started = perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    checkpoints = output / "checkpoints"
    checkpoints.mkdir(parents=True, exist_ok=True)
    seeds = tuple(seed_config["fresh_seeds"])
    protocol = seed_config["protocol"]
    steps = steps or int(protocol["steps"])
    dt = dt or float(protocol["dt"])
    counts = tuple(protocol["anchor_counts"])
    classifier_count = int(protocol["classifier_anchor_count"])
    resamples = int(protocol["bootstrap_resamples"])
    environments = load_environments()
    variants = base_variants() + mixed_variants()
    runs = []
    for layout in protocol["layouts"]:
        layout_key = layout["floor_id"] or layout["environment_id"]
        environment = resolve_floor(
            environments[layout["environment_id"]],
            layout["floor_id"],
        )
        for variant in variants:
            for count in counts:
                disabled = [
                    anchor.id
                    for anchor in environment.anchors
                    if anchor.id not in ANCHOR_SELECTION[count]
                ]
                for seed in seeds:
                    checkpoint = _checkpoint_path(
                        checkpoints, layout_key, variant["id"], count, seed
                    )
                    if checkpoint.exists():
                        runs.append(_load_checkpoint(checkpoint))
                        continue
                    settings = _settings(variant, seed, protocol["route"], disabled)
                    run_started = perf_counter()
                    result = _run_session(
                        environment,
                        settings,
                        model,
                        steps,
                        dt,
                        collect_classifier=count == classifier_count,
                    )
                    run = {
                        "layout": layout_key,
                        "environment_id": layout["environment_id"],
                        "floor_id": layout["floor_id"],
                        "role": layout["role"],
                        "scenario": variant["scenario"],
                        "variant": variant["id"],
                        "count": count,
                        "seed": seed,
                        **result,
                        "duration_seconds": perf_counter() - run_started,
                    }
                    _save_checkpoint(checkpoint, run)
                    runs.append(run)
                print(
                    f"{layout_key} {variant['id']} anchors={count} "
                    f"runs={len(runs)} elapsed={perf_counter() - started:.1f}s",
                    flush=True,
                )

    groups = _build_groups(runs)
    rows = []
    rows += _row("pooled", runs, resamples)
    for layout in protocol["layouts"]:
        layout_key = layout["floor_id"] or layout["environment_id"]
        selected = [run for run in runs if run["layout"] == layout_key]
        rows += _row("layout", selected, resamples, layout=layout_key)
    scenarios = sorted({run["scenario"] for run in runs})
    for scenario in scenarios:
        selected = [run for run in runs if run["scenario"] == scenario]
        rows += _row("scenario", selected, resamples, scenario=scenario)
    for layout in protocol["layouts"]:
        layout_key = layout["floor_id"] or layout["environment_id"]
        for scenario in scenarios:
            selected = [
                run
                for run in runs
                if run["layout"] == layout_key and run["scenario"] == scenario
            ]
            rows += _row(
                "layout_scenario",
                selected,
                resamples,
                layout=layout_key,
                scenario=scenario,
            )
    for layout in protocol["layouts"]:
        layout_key = layout["floor_id"] or layout["environment_id"]
        for count in counts:
            selected = [
                run for run in runs if run["layout"] == layout_key and run["count"] == count
            ]
            rows += _row(
                "layout_anchor",
                selected,
                resamples,
                layout=layout_key,
                anchors=str(count),
            )
    for scenario in scenarios:
        for count in counts:
            selected = [
                run
                for run in runs
                if run["scenario"] == scenario and run["count"] == count
            ]
            rows += _row(
                "scenario_anchor",
                selected,
                resamples,
                scenario=scenario,
                anchors=str(count),
            )

    named_keys = {
        "one_nlos_all": [key for key in groups if key[1] == 1],
        "one_nlos_solvable": [key for key in groups if key[1] == 1 and key[0] >= 3],
        "one_nlos_coasting": [key for key in groups if key[1] == 1 and key[0] < 3],
        "minority_solvable": [key for key in groups if key[0] >= 3 and 0 < key[1] * 2 < key[0]],
        "half_solvable": [key for key in groups if key[0] >= 3 and key[1] * 2 == key[0]],
        "majority_solvable": [key for key in groups if key[0] >= 3 and key[1] * 2 > key[0]],
        "all_nlos_solvable": [key for key in groups if key[0] >= 3 and key[1] == key[0]],
    }
    named = {
        name: _named_group(keys, groups) for name, keys in named_keys.items() if keys
    }
    group_rows = _group_rows(groups, "available_nlos")
    named_rows = []
    for name, payload in named.items():
        for method in METHODS:
            metrics = payload["methods"][method]
            prediction = metrics["prediction_only"]
            named_rows.append(
                {
                    "group": name,
                    "frames": payload["frames"],
                    "method": method,
                    "valid_n": metrics["n"],
                    "rmse": metrics["rmse"],
                    "median": metrics["median"],
                    "p95": metrics["p95"],
                    "mean_error": metrics["mean"],
                    "prediction_only_n": prediction["n"],
                    "prediction_only_rmse": prediction["rmse"],
                }
            )

    classifier_runs = [run for run in runs if run["classifier"] is not None]
    classifier_rows = _classifier_rows(classifier_runs, resamples)
    classifier_rows += _classifier_aggregates(classifier_runs, "layout")
    classifier_rows += _classifier_aggregates(classifier_runs, "scenario")

    samples_path = output / "classifier-samples.npz"
    scores = _concatenate([run["classifier"]["scores"] for run in classifier_runs])
    targets = _concatenate([run["classifier"]["targets"] for run in classifier_runs])
    sample_layouts = np.concatenate(
        [
            np.full(len(run["classifier"]["scores"]), run["layout"])
            for run in classifier_runs
        ]
    )
    sample_scenarios = np.concatenate(
        [
            np.full(len(run["classifier"]["scores"]), run["scenario"])
            for run in classifier_runs
        ]
    )
    np.savez_compressed(
        samples_path,
        scores=scores,
        targets=targets,
        layouts=sample_layouts,
        scenarios=sample_scenarios,
    )

    _write_json(output / "metrics.json", {"rows": rows})
    _write_csv(output / "metrics.csv", rows)
    _write_json(output / "bootstrap.json", [row for row in rows if row["method"] == "ai"])
    _write_csv(
        output / "bootstrap.csv",
        [row for row in rows if row["method"] == "ai"],
    )
    _write_csv(
        output / "sparse_anchors.csv",
        [row for row in rows if row["scope"] in ("layout_anchor", "scenario_anchor")],
    )
    _write_csv(output / "groups.csv", group_rows)
    _write_json(output / "groups.json", {"by_available_nlos": group_rows, "named": named})
    _write_csv(output / "named_groups.csv", named_rows)
    _write_csv(output / "classifier.csv", classifier_rows)
    _write_json(output / "pr-curves.json", _pr_curves(classifier_runs))
    _write_json(output / "reliability.json", _reliability_curves(classifier_runs))
    _write_json(output / "latency.json", _latency_distributions(runs))
    _write_csv(output / "runs.csv", _run_records(runs))

    manifest = {
        "created_at": _utc_now(),
        "method": "baseline, statistical LOCUS-X, AI-assisted LOCUS-X on identical observations",
        "model_version": model.version,
        "feature_version": model.feature_version,
        "model_dir": str(model_dir.relative_to(ROOT)),
        "model_sha256": {
            path.name: _sha256(path) for path in sorted(model_dir.iterdir()) if path.is_file()
        },
        "seed_config": str(DEFAULT_SEEDS.relative_to(ROOT)),
        "seed_config_sha256": _sha256(DEFAULT_SEEDS),
        "seed_config_status": seed_config.get("status"),
        "seeds": list(seeds),
        "previously_reported_seeds": seed_config.get("previously_reported_seeds"),
        "steps": steps,
        "dt": dt,
        "layouts": protocol["layouts"],
        "variants": [variant["id"] for variant in variants],
        "anchor_counts": list(counts),
        "runs": len(runs),
        "frames": sum(len(run["methods"]["baseline"]["valid"]) for run in runs),
        "bootstrap_resamples": resamples,
        "confidence_level": protocol["confidence_level"],
        "git_head": _git_head(),
        "elapsed_seconds": perf_counter() - started,
        "notes": [
            "All methods consume the same simulation observations within each frame.",
            "The baseline, LOCUS-X and AI paths share the same session; labels are never inputs.",
            "Classifier samples use only 8-anchor runs to avoid duplicating rows across counts.",
            "The AI latency path includes the preliminary solve, adapter, weighted solve, "
            "and tracker.",
        ],
    }
    _write_json(output / "manifest.json", manifest)
    return {"manifest": manifest, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--seeds", type=Path, default=DEFAULT_SEEDS)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--dt", type=float)
    args = parser.parse_args()
    if not args.evaluate:
        parser.error("Use --evaluate to run the fresh-seed evidence protocol.")
    seed_config = json.loads(args.seeds.read_text(encoding="utf-8"))
    if seed_config.get("status") == "preregistered_unrun":
        pass
    model = ReliabilityModel.load(args.model_dir)
    result = evaluate(
        model,
        seed_config,
        args.output,
        args.model_dir,
        steps=args.steps,
        dt=args.dt,
    )
    print(
        json.dumps(
            {
                "runs": result["manifest"]["runs"],
                "frames": result["manifest"]["frames"],
                "elapsed_seconds": result["manifest"]["elapsed_seconds"],
                "output": str(args.output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
