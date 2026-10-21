"""Phase 2 ablations for the frozen LOCUS-X reliability model.

This module is training/evaluation code. It is never imported by inference. It
reuses the v1 feature extractor and model architecture, but all data, model
arrays and metrics are written under a new Phase 2 artifact directory.

DIAGNOSTIC-ONLY labels are used to fit and score classifiers. They are never
returned to the estimator and are not added to the feature schema.
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
from scipy.stats import rankdata

from .ai_evaluation import base_variants, mixed_variants
from .ai_training import (
    _forward,
    _standardize,
    fit_mlp,
    fit_platt,
    load_split,
)
from .environment import load_environments, resolve_floor
from .estimators import solve, valid_observations
from .reliability import (
    FEATURE_GROUPS,
    FEATURE_NAMES,
    FeatureHistory,
    ReliabilityModel,
    extract_features,
)
from .schemas import PRESETS, AIMetadata, AIPrediction, Settings
from .session import SimulationSession
from .simulation import generate_measurements

ROOT = Path(__file__).resolve().parents[2]
DATASET_CONFIG = ROOT / "configs" / "ai" / "reliability-dataset-v1.json"
V1_DATASET_DIR = ROOT / "artifacts" / "ai-reliability" / "dataset-v1"
V1_MODEL_DIR = ROOT / "models" / "ai-reliability-v1"
OUTPUT = ROOT / "artifacts" / "ai-reliability" / "ablation-v1"
DATASET_DIR = OUTPUT / "dataset"
MODEL_OUTPUT = OUTPUT / "models"
TRAINING_SEEDS = (20261021, 20261022, 20261023, 20261024, 20261025)
CALIBRATION_FRACTION = 0.20
SPLIT_SEED = 20261021
BOOTSTRAP_SEED = 20261021
BOOTSTRAP_RESAMPLES = 1000
POLICY_FLOOR = 0.05
POLICY_THRESHOLD = 0.05
VALIDATION_SEEDS = (11, 22)
STEPS = 50
DT = 0.5
SCENARIOS = tuple(PRESETS)
LAYOUTS = (
    ("hospital_floor_01", "hospital_floor_01", None),
    ("ground", "hospital_v3", "ground"),
    ("first", "hospital_v3", "first"),
    ("second", "hospital_v3", "second"),
)

VARIANTS = (
    {"name": "full", "groups": ["residual", "geometry", "map", "temporal"]},
    {"name": "drop_map", "groups": ["residual", "geometry", "temporal"]},
    {"name": "drop_temporal", "groups": ["residual", "geometry", "map"]},
    {"name": "drop_residual", "groups": ["geometry", "map", "temporal"]},
    {"name": "drop_geometry", "groups": ["residual", "map", "temporal"]},
    {"name": "residual_only", "groups": ["residual"]},
    {"name": "geometry_only", "groups": ["geometry"]},
    {"name": "map_only", "groups": ["map"]},
    {"name": "temporal_only", "groups": ["temporal"]},
    {"name": "wall_count_only", "names": ["wall_count_scaled"]},
    {"name": "majority_class", "special": "majority"},
    {"name": "residual_threshold", "special": "threshold"},
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


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


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def feature_indices(groups: list[str] | None = None, names: list[str] | None = None) -> np.ndarray:
    """Return sorted v1 feature indices for a group or explicit feature set."""
    if names is not None:
        selected = set(names)
    elif groups is not None:
        selected = {name for group in groups for name in FEATURE_GROUPS[group]}
    else:
        selected = set(FEATURE_NAMES)
    unknown = selected - set(FEATURE_NAMES)
    if unknown:
        raise ValueError(f"Unknown features: {sorted(unknown)}")
    return np.asarray(
        [index for index, name in enumerate(FEATURE_NAMES) if name in selected], dtype=int
    )


def variant_indices(variant: dict) -> np.ndarray | None:
    if "special" in variant:
        return None
    if "names" in variant:
        return feature_indices(names=variant["names"])
    return feature_indices(groups=variant["groups"])


def feature_mask(matrix: np.ndarray, indices: np.ndarray) -> np.ndarray:
    return np.asarray(matrix)[:, indices]


def grouped_calibration_split(
    run_ids: np.ndarray, fraction: float = CALIBRATION_FRACTION
) -> np.ndarray:
    """Return a boolean mask selecting whole runs for calibration."""
    unique = np.unique(run_ids)
    count = max(1, int(round(len(unique) * fraction)))
    rng = np.random.default_rng(SPLIT_SEED)
    calibration_runs = rng.choice(unique, size=count, replace=False)
    return np.isin(run_ids, calibration_runs)


def _settings_for_variant(variant: dict, seed: int, route: str, disabled: list[str]) -> Settings:
    if "noise_std" in variant:
        return Settings(
            scenario=variant["scenario"],
            seed=seed,
            route=route,
            disabled_anchors=disabled,
            noise_std=variant["noise_std"],
            nlos_bias=variant["nlos_bias"],
            dropout=variant["dropout"],
            walls_enabled=variant["walls_enabled"],
        )
    scenario = variant["scenario"]
    values = {**PRESETS[scenario], **variant.get("overrides", {})}
    return Settings(
        scenario=scenario,
        seed=seed,
        route=route,
        disabled_anchors=disabled,
        **values,
    )


def _iter_generation_runs(
    seed_set: list[int],
    variants: list[dict],
    routes: list[str],
    layouts: tuple = LAYOUTS,
):
    config = json.loads(DATASET_CONFIG.read_text(encoding="utf-8"))
    for layout_name, environment_id, floor_id in layouts:
        for route in routes:
            for seed in seed_set:
                for variant in variants:
                    for count in (3, 4, 6, 8):
                        for subset in config["anchor_subsets"][str(count)]:
                            yield {
                                "layout": layout_name,
                                "environment_id": environment_id,
                                "floor_id": floor_id,
                                "route": route,
                                "seed": seed,
                                "variant": variant,
                                "count": count,
                                "subset": subset,
                            }


def _generate_split(
    name: str,
    seed_set: list[int],
    variants: list[dict],
    routes: list[str],
    output: Path,
) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    matrices, labels, run_ids, steps, anchor_indices = [], [], [], [], []
    layout_indices, scenario_indices, variant_indices_out = [], [], []
    counts, seeds, available_counts, gaps, seen, dropout_flags = [], [], [], [], [], []
    catalog = []
    variant_names = [variant["id"] for variant in variants]
    scenario_names = [variant["scenario"] for variant in variants]
    layout_names = [layout[0] for layout in LAYOUTS]
    started = perf_counter()
    run_id = 0
    for run in _iter_generation_runs(seed_set, variants, routes):
        environment = resolve_floor(
            load_environments()[run["environment_id"]], run["floor_id"]
        )
        disabled = [
            anchor.id for anchor in environment.anchors if anchor.id not in run["subset"]
        ]
        settings = _settings_for_variant(run["variant"], run["seed"], run["route"], disabled)
        history = FeatureHistory()
        last_seen: dict[str, int] = {}
        run_samples = 0
        run_nlos = 0
        run_dropout = 0
        for step in range(STEPS):
            timestamp = round(step * DT, 8)
            truth = _route_position(environment, settings.route, timestamp, settings.speed)
            measurements, fault_labels = generate_measurements(
                environment, settings, truth, timestamp, step
            )
            baseline = solve(measurements)
            batch = extract_features(measurements, environment, baseline, history)
            has_dropout = any(label.kind == "packet_loss" for label in fault_labels)
            available = len(valid_observations(measurements))
            if batch is not None:
                label_by_anchor = {
                    label.anchor_id: label
                    for label in fault_labels
                    if label.available and label.kind in ("los", "nlos")
                }
                for anchor_id, row in zip(batch.anchors, batch.matrix, strict=True):
                    label = label_by_anchor.get(anchor_id)
                    if label is None:
                        continue
                    previous = last_seen.get(anchor_id)
                    gap = 0 if previous is None else step - previous
                    matrices.append(row)
                    labels.append(1 if label.kind == "nlos" else 0)
                    run_ids.append(run_id)
                    steps.append(step)
                    anchor_indices.append(int(anchor_id[1:]) - 1)
                    layout_indices.append(layout_names.index(run["layout"]))
                    scenario_indices.append(scenario_names.index(run["variant"]["scenario"]))
                    variant_indices_out.append(variant_names.index(run["variant"]["id"]))
                    counts.append(run["count"])
                    seeds.append(run["seed"])
                    available_counts.append(available)
                    gaps.append(min(int(gap), 999))
                    seen.append(0 if previous is None else 1)
                    dropout_flags.append(1 if has_dropout else 0)
                    run_samples += 1
                    run_nlos += int(label.kind == "nlos")
            for measurement in valid_observations(measurements):
                last_seen[measurement.anchor_id] = step
            run_dropout += int(has_dropout)
        catalog.append(
            {
                "run_id": run_id,
                "layout": run["layout"],
                "environment_id": run["environment_id"],
                "floor_id": run["floor_id"],
                "route": run["route"],
                "seed": run["seed"],
                "variant": run["variant"]["id"],
                "scenario": run["variant"]["scenario"],
                "count": run["count"],
                "subset": run["subset"],
                "samples": run_samples,
                "nlos_samples": run_nlos,
                "dropout_frames": run_dropout,
            }
        )
        run_id += 1
    X = np.asarray(matrices, dtype=np.float32)
    arrays = {
        "X": X,
        "y": np.asarray(labels, dtype=np.int8),
        "run_id": np.asarray(run_ids, dtype=np.int32),
        "step": np.asarray(steps, dtype=np.int16),
        "anchor_index": np.asarray(anchor_indices, dtype=np.int8),
        "layout_index": np.asarray(layout_indices, dtype=np.int8),
        "scenario_index": np.asarray(scenario_indices, dtype=np.int8),
        "variant_index": np.asarray(variant_indices_out, dtype=np.int8),
        "count": np.asarray(counts, dtype=np.int8),
        "seed": np.asarray(seeds, dtype=np.int32),
        "available_count": np.asarray(available_counts, dtype=np.int8),
        "frames_since_gap": np.asarray(gaps, dtype=np.int16),
        "seen_before": np.asarray(seen, dtype=np.int8),
        "has_dropout": np.asarray(dropout_flags, dtype=np.int8),
    }
    path = output / f"{name}.npz"
    np.savez_compressed(path, **arrays)
    manifest = {
        "split": name,
        "generated_at": _utc_now(),
        "samples": int(len(labels)),
        "runs": int(run_id),
        "nlos_samples": int(np.sum(np.asarray(labels) == 1)),
        "los_samples": int(np.sum(np.asarray(labels) == 0)),
        "file": str(path.relative_to(ROOT)),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "feature_names": FEATURE_NAMES,
        "layouts": layout_names,
        "variants": variant_names,
        "scenarios": scenario_names,
        "seeds": seed_set,
        "steps": STEPS,
        "dt": DT,
        "run_catalog": catalog,
        "elapsed_seconds": perf_counter() - started,
    }
    write_json(output / f"{name}-manifest.json", manifest)
    return manifest


def _route_position(environment, route: str, timestamp: float, speed: float):
    from .environment import route_position

    return route_position(environment.routes[route], timestamp, speed)


def generate_phase2_dataset() -> dict:
    config = json.loads(DATASET_CONFIG.read_text(encoding="utf-8"))
    train_variants = list(config["splits"]["train"]["variants"])
    validation_variants = base_variants() + mixed_variants()
    train = _generate_split(
        "train",
        list(config["splits"]["train"]["seeds"]),
        train_variants,
        list(config["splits"]["train"]["routes"]),
        DATASET_DIR,
    )
    validation = _generate_split(
        "validation",
        list(VALIDATION_SEEDS),
        validation_variants,
        ["held_out"],
        DATASET_DIR,
    )
    summary = {
        "version": "phase2-ablation-v1",
        "train": {key: train[key] for key in ("samples", "runs", "nlos_samples", "file")},
        "validation": {
            key: validation[key] for key in ("samples", "runs", "nlos_samples", "file")
        },
        "note": (
            "Training seeds 1-4, validation seeds 11/22. "
            "Reserved v2 test seeds were not touched."
        ),
    }
    write_json(DATASET_DIR / "summary.json", summary)
    return summary


def load_phase2_split(split: str) -> dict:
    data = np.load(DATASET_DIR / f"{split}.npz")
    return {name: data[name] for name in data.files}


def load_phase2_manifest(split: str) -> dict:
    return json.loads((DATASET_DIR / f"{split}-manifest.json").read_text(encoding="utf-8"))


def load_v1_train() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return load_split("train", V1_DATASET_DIR)


def load_v1_validation() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return load_split("validation", V1_DATASET_DIR)


# ---------------------------------------------------------------------------
# Metrics and model fitting
# ---------------------------------------------------------------------------


def roc_auc(y: np.ndarray, probabilities: np.ndarray) -> float | None:
    positives = int(np.sum(y == 1))
    negatives = int(np.sum(y == 0))
    if positives == 0 or negatives == 0:
        return None
    ranks = rankdata(probabilities, method="average")
    return float(
        (ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives)
    )


def average_precision(y: np.ndarray, probabilities: np.ndarray) -> float | None:
    positives = int(np.sum(y == 1))
    if positives == 0:
        return None
    order = np.argsort(-probabilities, kind="mergesort")
    sorted_y = y[order]
    tp = np.cumsum(sorted_y)
    fp = np.cumsum(1 - sorted_y)
    precision = tp / np.maximum(tp + fp, 1)
    recall = tp / positives
    # Step-wise average precision: sum over increases in recall.
    delta_recall = np.diff(np.concatenate([[0.0], recall]))
    return float(np.sum(delta_recall * precision))


def expected_calibration_error(y: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        selected = (probabilities >= lower) & (
            probabilities < upper if upper < 1 else probabilities <= upper
        )
        if not np.any(selected):
            continue
        total += float(np.sum(selected)) / len(y) * abs(
            float(np.mean(probabilities[selected])) - float(np.mean(y[selected]))
        )
    return total


def metric_row(y: np.ndarray, probabilities: np.ndarray, threshold: float = 0.5) -> dict:
    y = np.asarray(y, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    predicted = probabilities >= threshold
    positive = y == 1
    tp = int(np.sum(predicted & positive))
    fp = int(np.sum(predicted & ~positive))
    fn = int(np.sum(~predicted & positive))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "samples": int(len(y)),
        "positives": int(np.sum(positive)),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "roc_auc": roc_auc(y, probabilities),
        "average_precision": average_precision(y, probabilities),
        "brier": float(np.mean((probabilities - y) ** 2)),
        "ece": expected_calibration_error(y, probabilities),
        "mean_predicted_nlos": float(np.mean(probabilities)),
        "true_nlos_rate": float(np.mean(positive)),
    }


def reliability_bins(y: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> list[dict]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    rows = []
    for lower, upper in zip(edges[:-1], edges[1:], strict=True):
        selected = (probabilities >= lower) & (
            probabilities < upper if upper < 1 else probabilities <= upper
        )
        if not np.any(selected):
            rows.append(
                {
                    "lower": float(lower),
                    "upper": float(upper),
                    "count": 0,
                    "confidence": None,
                    "frequency": None,
                }
            )
            continue
        rows.append(
            {
                "lower": float(lower),
                "upper": float(upper),
                "count": int(np.sum(selected)),
                "confidence": float(np.mean(probabilities[selected])),
                "frequency": float(np.mean(y[selected])),
            }
        )
    return rows


def _aggregate(values: list[float | None]) -> tuple[float | None, float | None]:
    finite = [float(value) for value in values if value is not None and np.isfinite(value)]
    if not finite:
        return None, None
    return float(np.mean(finite)), float(np.std(finite, ddof=1)) if len(finite) > 1 else 0.0


def summarize_metric_rows(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row[key] for key in keys)].append(row)
    summary = []
    metric_names = (
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "average_precision",
        "brier",
        "ece",
    )
    for group_key, items in sorted(grouped.items()):
        output = {key: value for key, value in zip(keys, group_key, strict=True)}
        output["training_runs"] = len(items)
        for metric in metric_names:
            mean, sd = _aggregate([item.get(metric) for item in items])
            output[f"{metric}_mean"] = mean
            output[f"{metric}_sd"] = sd
        output["samples"] = items[0]["samples"]
        output["positives"] = items[0]["positives"]
        summary.append(output)
    return summary


def _threshold_rule(probabilities: np.ndarray) -> np.ndarray:
    return probabilities


def _fit_variant_model(
    X_train: np.ndarray,
    y_train: np.ndarray,
    run_ids: np.ndarray,
    X_validation: np.ndarray,
    variant: dict,
    seed: int,
    *,
    fit_mask: np.ndarray | None = None,
    calibration_mask: np.ndarray | None = None,
) -> dict:
    if fit_mask is None or calibration_mask is None:
        calibration_mask = grouped_calibration_split(run_ids)
        fit_mask = ~calibration_mask
    X_fit_all = X_train[fit_mask]
    y_fit = y_train[fit_mask]
    X_cal_all = X_train[calibration_mask]
    y_cal = y_train[calibration_mask]
    if "special" in variant:
        return {"special": variant["special"], "positive_rate": float(np.mean(y_fit))}
    indices = variant_indices(variant)
    X_fit, X_cal, X_val, mean, scale = _standardize(
        X_fit_all[:, indices], X_cal_all[:, indices], X_validation[:, indices]
    )
    weights, training = fit_mlp(
        X_fit,
        y_fit,
        X_cal,
        y_cal,
        epochs=35,
        batch_size=512,
        learning_rate=1.5e-3,
        l2=1e-4,
        seed=seed,
    )
    calibration_slope, calibration_intercept, calibration = fit_platt(
        _forward(weights, X_cal)[0], y_cal
    )
    validation_logits = _forward(weights, X_val)[0]
    probabilities = 1.0 / (1.0 + np.exp(-np.clip(
        calibration_slope * validation_logits + calibration_intercept, -60, 60
    )))
    return {
        "indices": indices.tolist(),
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "weights": {
            "W1": weights.W1.tolist(),
            "b1": weights.b1.tolist(),
            "W2": weights.W2.tolist(),
            "b2": weights.b2.tolist(),
            "W3": weights.W3.tolist(),
            "b3": weights.b3.tolist(),
        },
        "calibration": {
            "slope": calibration_slope,
            "intercept": calibration_intercept,
        },
        "training": {
            "best_epoch": training["best_epoch"],
            "epochs_completed": training["epochs_completed"],
            "best_validation_log_loss": training["best_validation_log_loss"],
        },
        "validation_probabilities": probabilities,
        "seed": seed,
    }


def _model_probabilities(model: dict, matrix: np.ndarray) -> np.ndarray:
    if "special" in model:
        if model["special"] == "majority":
            return np.full(len(matrix), float(np.clip(model["positive_rate"], 0.01, 0.99)))
        if model["special"] == "threshold":
            residual = matrix[:, FEATURE_NAMES.index("abs_residual_norm")]
            return np.where(residual >= float(model["threshold"]), 0.99, 0.01)
        raise ValueError(f"Unsupported special model: {model['special']}")
    indices = np.asarray(model["indices"], dtype=int)
    normalized = (matrix[:, indices] - np.asarray(model["mean"])) / np.asarray(model["scale"])
    weights = {key: np.asarray(value) for key, value in model["weights"].items()}
    logits = _forward(
        type("Weights", (), weights)(),
        normalized,
    )[0]
    calibration = model["calibration"]
    return 1.0 / (1.0 + np.exp(-np.clip(
        calibration["slope"] * logits + calibration["intercept"], -60, 60
    )))


def _save_model(path: Path, model: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {key: value for key, value in model.items() if key != "validation_probabilities"}
    meta["special"] = model.get("special", "")
    np.savez_compressed(
        path,
        meta=np.asarray(json.dumps(_jsonable(meta))),
        validation_probabilities=np.asarray(
            model.get("validation_probabilities", []), dtype=float
        ),
    )


def _load_model(path: Path) -> dict:
    data = np.load(path, allow_pickle=False)
    model = json.loads(str(data["meta"]))
    if model.get("special"):
        model["special"] = model["special"]
    else:
        model.pop("special", None)
    if data["validation_probabilities"].size:
        model["validation_probabilities"] = np.asarray(
            data["validation_probabilities"], dtype=float
        )
    return model


# ---------------------------------------------------------------------------
# Part A: feature-group ablations
# ---------------------------------------------------------------------------


def _choose_residual_threshold(y: np.ndarray, X: np.ndarray) -> float:
    residual = np.abs(X[:, FEATURE_NAMES.index("abs_residual_norm")])
    candidates = np.unique(np.quantile(residual, np.linspace(0.0, 1.0, 201)))
    best_threshold = float(candidates[0])
    best_f1 = -1.0
    for threshold in candidates:
        predicted = residual >= threshold
        tp = int(np.sum(predicted & (y == 1)))
        fp = int(np.sum(predicted & (y == 0)))
        fn = int(np.sum(~predicted & (y == 1)))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = float(threshold)
    return best_threshold


def _metric_vectors(
    y: np.ndarray,
    reference_probabilities: np.ndarray,
    variant_probabilities: np.ndarray,
    run_ids: np.ndarray,
    metric_names: tuple[str, ...],
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> list[dict]:
    run_to_indices = {
        int(run): np.flatnonzero(run_ids == run) for run in np.unique(run_ids)
    }
    runs = np.asarray(sorted(run_to_indices), dtype=int)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    reference_metrics = metric_row(y, reference_probabilities)
    variant_metrics = metric_row(y, variant_probabilities)
    observed = {
        metric: (
            None
            if reference_metrics[metric] is None or variant_metrics[metric] is None
            else float(variant_metrics[metric] - reference_metrics[metric])
        )
        for metric in metric_names
    }
    samples = {metric: [] for metric in metric_names}
    for _ in range(resamples):
        chosen = rng.choice(runs, size=len(runs), replace=True)
        indices = np.concatenate([run_to_indices[int(run)] for run in chosen])
        y_sample = y[indices]
        reference_sample = metric_row(
            y_sample, reference_probabilities[indices]
        )
        variant_sample = metric_row(
            y_sample, variant_probabilities[indices]
        )
        for metric in metric_names:
            if (
                reference_sample[metric] is not None
                and variant_sample[metric] is not None
            ):
                samples[metric].append(
                    float(variant_sample[metric] - reference_sample[metric])
                )
    rows = []
    for metric in metric_names:
        values = np.asarray(samples[metric], dtype=float)
        rows.append(
            {
                "metric": metric,
                "observed_difference": observed[metric],
                "ci_low": float(np.percentile(values, 2.5)) if values.size else None,
                "ci_high": float(np.percentile(values, 97.5)) if values.size else None,
                "resamples": int(values.size),
                "direction": "higher_is_better"
                if metric in ("precision", "recall", "f1", "roc_auc", "average_precision")
                else "lower_is_better",
            }
        )
    return rows


def run_classifier_ablations() -> dict:
    started = perf_counter()
    X_train, y_train, train_run_ids = load_v1_train()
    val = load_phase2_split("validation")
    X_val = val["X"].astype(np.float64)
    y_val = val["y"].astype(int)
    val_run_ids = val["run_id"].astype(int)
    layout_index = val["layout_index"].astype(int)
    scenario_index = val["scenario_index"].astype(int)
    manifest = load_phase2_manifest("validation")
    layout_names = manifest["layouts"]
    scenario_names = manifest["scenarios"]
    calibration_mask = grouped_calibration_split(train_run_ids)
    fit_mask = ~calibration_mask

    rows = []
    ensembles: dict[str, np.ndarray] = {}
    models_by_variant: dict[str, list[dict]] = {}
    training_info = []
    for variant in VARIANTS:
        name = variant["name"]
        indices = variant_indices(variant)
        if indices is not None:
            assert len(indices) > 0
        variant_models = []
        if variant.get("special") == "threshold":
            threshold = _choose_residual_threshold(y_val, X_val)
            model = {"special": "threshold", "threshold": threshold}
            variant_models = [model]
            _save_model(MODEL_OUTPUT / name / "model.npz", model)
            probabilities = _model_probabilities(model, X_val)
            ensembles[name] = probabilities
            for scope, names, mask_array in (
                ("overall", ["overall"], None),
                ("layout", layout_names, layout_index),
                ("scenario", scenario_names, scenario_index),
            ):
                scopes = [(None, np.ones(len(y_val), dtype=bool))] if mask_array is None else [
                    (value, mask_array == index) for index, value in enumerate(names)
                ]
                for value, mask in scopes:
                    if not np.any(mask):
                        continue
                    row = {
                        "variant": name,
                        "seed": 0,
                        "scope": scope,
                        "name": "overall" if value is None else value,
                        **metric_row(y_val[mask], probabilities[mask]),
                        "threshold": threshold,
                    }
                    rows.append(row)
            training_info.append(
                {
                    "variant": name,
                    "feature_count": 1,
                    "threshold": threshold,
                    "note": "threshold chosen on validation by F1; diagnostic baseline only",
                }
            )
            continue
        if variant.get("special") == "majority":
            model = _fit_variant_model(
                X_train,
                y_train,
                train_run_ids,
                X_val,
                variant,
                TRAINING_SEEDS[0],
                fit_mask=fit_mask,
                calibration_mask=calibration_mask,
            )
            variant_models = [model]
            _save_model(MODEL_OUTPUT / name / "model.npz", model)
            probabilities = _model_probabilities(model, X_val)
        else:
            probabilities_list = []
            for seed in TRAINING_SEEDS:
                model = _fit_variant_model(
                    X_train,
                    y_train,
                    train_run_ids,
                    X_val,
                    variant,
                    seed,
                    fit_mask=fit_mask,
                    calibration_mask=calibration_mask,
                )
                variant_models.append(model)
                _save_model(MODEL_OUTPUT / name / f"seed_{seed}.npz", model)
                probabilities_list.append(model["validation_probabilities"])
            probabilities = np.mean(np.asarray(probabilities_list), axis=0)
        models_by_variant[name] = variant_models
        ensembles[name] = probabilities
        for seed, seed_probabilities in (
            [(0, probabilities)]
            if variant.get("special")
            else list(zip(TRAINING_SEEDS, probabilities_list, strict=True))
        ):
            for scope, names, mask_array in (
                ("overall", ["overall"], None),
                ("layout", layout_names, layout_index),
                ("scenario", scenario_names, scenario_index),
            ):
                scopes = [(None, np.ones(len(y_val), dtype=bool))] if mask_array is None else [
                    (value, mask_array == index) for index, value in enumerate(names)
                ]
                for value, mask in scopes:
                    if not np.any(mask):
                        continue
                    rows.append(
                        {
                            "variant": name,
                            "seed": seed,
                            "scope": scope,
                            "name": "overall" if value is None else value,
                            **metric_row(y_val[mask], seed_probabilities[mask]),
                        }
                    )
        training_info.append(
            {
                "variant": name,
                "feature_count": int(len(indices)) if indices is not None else 0,
                "features": (
                    [FEATURE_NAMES[index] for index in indices]
                    if indices is not None
                    else []
                ),
                "seeds": list(TRAINING_SEEDS) if "special" not in variant else [],
                "training_runs": int(len(np.unique(train_run_ids[fit_mask]))),
                "calibration_runs": int(len(np.unique(train_run_ids[calibration_mask]))),
            }
        )

    # Frozen v1 reference on the same validation rows.
    v1_model = ReliabilityModel.load(V1_MODEL_DIR)
    if v1_model.available:
        normalized = (X_val - v1_model.mean) / v1_model.scale
        logits = v1_model._forward(normalized)
        v1_probabilities = 1.0 / (1.0 + np.exp(-np.clip(
            v1_model.calibration[0] * logits + v1_model.calibration[1], -60, 60
        )))
        v1_metrics = metric_row(y_val, v1_probabilities)
        write_json(OUTPUT / "classifier" / "v1-frozen-reference.json", {
            "metrics": v1_metrics,
            "note": (
                "Same phase2 validation rows; v1 calibration was fitted on v1 "
                "validation during training."
            ),
        })
    else:
        v1_metrics = None

    write_csv(OUTPUT / "classifier" / "metrics-by-seed.csv", rows)
    summary = summarize_metric_rows(rows, ("variant", "scope", "name"))
    write_csv(OUTPUT / "classifier" / "summary.csv", summary)
    bootstrap_rows = []
    reference = ensembles["full"]
    for name, probabilities in ensembles.items():
        if name == "full":
            continue
        for row in _metric_vectors(
            y_val,
            reference,
            probabilities,
            val_run_ids,
            ("roc_auc", "average_precision", "f1", "brier", "ece"),
        ):
            bootstrap_rows.append({"variant": name, **row})
    write_csv(OUTPUT / "classifier" / "bootstrap-differences.csv", bootstrap_rows)
    write_json(
        OUTPUT / "classifier" / "summary.json",
        {
            "scope": "validation seeds 11,22; phase2 validation layouts/variants",
            "training_data": "v1 train split; 80/20 grouped calibration split",
            "seeds": list(TRAINING_SEEDS),
            "variants": training_info,
            "v1_frozen_reference": v1_metrics,
            "elapsed_seconds": perf_counter() - started,
        },
    )
    return {"summary": summary, "bootstrap": bootstrap_rows, "models": models_by_variant}


# ---------------------------------------------------------------------------
# Part B: leave-one-layout-out
# ---------------------------------------------------------------------------


def _fit_masked(
    X: np.ndarray,
    y: np.ndarray,
    run_ids: np.ndarray,
    X_validation: np.ndarray,
    variant: dict,
    seed: int,
    training_mask: np.ndarray,
) -> dict:
    calibration_local = grouped_calibration_split(run_ids[training_mask])
    calibration_mask = np.zeros(len(y), dtype=bool)
    calibration_mask[np.flatnonzero(training_mask)[calibration_local]] = True
    fit_mask = training_mask & ~calibration_mask
    return _fit_variant_model(
        X,
        y,
        run_ids,
        X_validation,
        variant,
        seed,
        fit_mask=fit_mask,
        calibration_mask=calibration_mask,
    )


def run_leave_one_layout_out() -> dict:
    started = perf_counter()
    train = load_phase2_split("train")
    val = load_phase2_split("validation")
    X_train = train["X"].astype(np.float64)
    y_train = train["y"].astype(int)
    train_runs = train["run_id"].astype(int)
    train_layouts = train["layout_index"].astype(int)
    X_val = val["X"].astype(np.float64)
    y_val = val["y"].astype(int)
    val_runs = val["run_id"].astype(int)
    val_layouts = val["layout_index"].astype(int)
    manifest = load_phase2_manifest("validation")
    layout_names = manifest["layouts"]
    variants = {
        "full": next(variant for variant in VARIANTS if variant["name"] == "full"),
        "drop_map": next(variant for variant in VARIANTS if variant["name"] == "drop_map"),
    }
    rows = []
    ensembles = defaultdict(dict)
    for model_name, variant in variants.items():
        # In-distribution reference: train on all four layouts.
        reference_models = []
        for seed in TRAINING_SEEDS:
            model = _fit_masked(
                X_train,
                y_train,
                train_runs,
                X_val,
                variant,
                seed,
                np.ones(len(y_train), dtype=bool),
            )
            reference_models.append(model["validation_probabilities"])
        reference_probabilities = np.mean(reference_models, axis=0)
        ensembles[(model_name, "reference")] = reference_probabilities
        for layout_value, layout_name in enumerate(layout_names):
            mask = val_layouts == layout_value
            for seed, probabilities in zip(
                TRAINING_SEEDS, reference_models, strict=True
            ):
                rows.append(
                    {
                        "model": model_name,
                        "training": "all_layout_reference",
                        "held_out_layout": layout_name,
                        "seed": seed,
                        **metric_row(y_val[mask], probabilities[mask]),
                    }
                )
        for layout_value, layout_name in enumerate(layout_names):
            training_mask = train_layouts != layout_value
            models = []
            for seed in TRAINING_SEEDS:
                model = _fit_masked(
                    X_train,
                    y_train,
                    train_runs,
                    X_val,
                    variant,
                    seed,
                    training_mask,
                )
                models.append(model["validation_probabilities"])
            probabilities = np.mean(models, axis=0)
            ensembles[(model_name, layout_name)] = probabilities
            mask = val_layouts == layout_value
            for seed, seed_probabilities in zip(TRAINING_SEEDS, models, strict=True):
                rows.append(
                    {
                        "model": model_name,
                        "training": "leave_one_layout_out",
                        "held_out_layout": layout_name,
                        "seed": seed,
                        **metric_row(y_val[mask], seed_probabilities[mask]),
                    }
                )
    write_csv(OUTPUT / "loo" / "metrics-by-seed.csv", rows)
    summary = summarize_metric_rows(
        rows, ("model", "training", "held_out_layout")
    )
    write_csv(OUTPUT / "loo" / "summary.csv", summary)
    by_key = {
        (row["model"], row["training"], row["held_out_layout"]): row for row in summary
    }
    drop_rows = []
    for model_name in variants:
        for layout_name in layout_names:
            reference = by_key[(model_name, "all_layout_reference", layout_name)]
            loo = by_key[(model_name, "leave_one_layout_out", layout_name)]
            mask = val_layouts == layout_names.index(layout_name)
            for metric in ("roc_auc", "average_precision", "f1", "ece"):
                reference_probabilities = ensembles[(model_name, "reference")]
                loo_probabilities = ensembles[(model_name, layout_name)]
                interval = _metric_vectors(
                    y_val[mask],
                    reference_probabilities[mask],
                    loo_probabilities[mask],
                    val_runs[mask],
                    (metric,),
                    resamples=BOOTSTRAP_RESAMPLES,
                )[0]
                drop_rows.append(
                    {
                        "model": model_name,
                        "held_out_layout": layout_name,
                        "metric": metric,
                        "reference_mean": reference[f"{metric}_mean"],
                        "loo_mean": loo[f"{metric}_mean"],
                        "drop_loo_minus_reference": (
                            None
                            if loo[f"{metric}_mean"] is None or reference[f"{metric}_mean"] is None
                            else loo[f"{metric}_mean"] - reference[f"{metric}_mean"]
                        ),
                        "ci_low": interval["ci_low"],
                        "ci_high": interval["ci_high"],
                    }
                )
    write_csv(OUTPUT / "loo" / "drops.csv", drop_rows)
    write_json(
        OUTPUT / "loo" / "summary.json",
        {
            "scope": (
                "phase2 train seeds 1-4; validation seeds 11,22; "
                "all validation variants and counts"
            ),
            "reference": "model trained on all four layouts, evaluated on each layout",
            "loo": "model trained on the other three layouts, evaluated on held-out layout",
            "seeds": list(TRAINING_SEEDS),
            "elapsed_seconds": perf_counter() - started,
        },
    )
    return {"summary": summary, "drops": drop_rows}


# ---------------------------------------------------------------------------
# Part C: end-to-end validation with the unchanged v1 policy
# ---------------------------------------------------------------------------


class AblationEnsembleModel:
    """Inference adapter for Phase 2 model arrays. Uses the unchanged v1 policy."""

    def __init__(self, models: list[dict], name: str):
        self.models = models
        self.name = name
        self.version = f"ablation-{name}"
        self.feature_version = "1.0.0"
        self.available = True

    def _probabilities(self, matrix: np.ndarray) -> np.ndarray:
        values = []
        for model in self.models:
            if "special" in model:
                values.append(_model_probabilities(model, matrix))
            else:
                indices = np.asarray(model["indices"], dtype=int)
                normalized = (
                    matrix[:, indices] - np.asarray(model["mean"])
                ) / np.asarray(model["scale"])
                weights = {key: np.asarray(value) for key, value in model["weights"].items()}
                logits = _forward(type("Weights", (), weights)(), normalized)[0]
                calibration = model["calibration"]
                values.append(
                    1.0
                    / (
                        1.0
                        + np.exp(
                            -np.clip(
                                calibration["slope"] * logits + calibration["intercept"],
                                -60,
                                60,
                            )
                        )
                    )
                )
        return np.mean(np.asarray(values), axis=0)

    def predict(self, measurements, environment, estimate, history):
        start = perf_counter()
        batch = extract_features(measurements, environment, estimate, history)
        if batch is None:
            return ReliabilityOutputFallback(self.version, self.feature_version)
        nlos_probabilities = self._probabilities(batch.matrix)
        predictions = []
        for anchor, nlos_probability in zip(batch.anchors, nlos_probabilities, strict=True):
            p_los = float(np.clip(1.0 - nlos_probability, 0.0, 1.0))
            if p_los >= POLICY_THRESHOLD:
                weight = 1.0
            else:
                weight = POLICY_FLOOR + (1.0 - POLICY_FLOOR) * (p_los / POLICY_THRESHOLD)
            predictions.append(
                AIPrediction(anchor_id=anchor, p_los=p_los, weight=float(weight))
            )
        predicted_los = sum(prediction.p_los >= 0.5 for prediction in predictions)
        ambiguous = predicted_los < 3
        return ReliabilityOutputFallback(
            self.version,
            self.feature_version,
            weights=None
            if ambiguous
            else {prediction.anchor_id: prediction.weight for prediction in predictions},
            metadata=AIMetadata(
                status="fallback" if ambiguous else "active",
                reason=(
                    "Fewer than three anchors predicted LOS; ambiguous consensus, "
                    "statistical fallback"
                    if ambiguous
                    else "Ablation reliability active"
                ),
                model_version=self.version,
                feature_version=self.feature_version,
                ambiguous=ambiguous,
                n_predicted_los=predicted_los,
                n_predicted_nlos=len(predictions) - predicted_los,
                predictions=predictions,
                runtime_ms=(perf_counter() - start) * 1000,
            ),
        )


class ReliabilityOutputFallback:
    def __init__(self, version: str, feature_version: str, weights=None, metadata=None):
        self.weights = weights
        self.metadata = metadata or AIMetadata(
            status="fallback",
            reason="Ablation features unavailable; statistical fallback",
            model_version=version,
            feature_version=feature_version,
            ambiguous=False,
            n_predicted_los=0,
            n_predicted_nlos=0,
            predictions=[],
            runtime_ms=0.0,
        )


def load_ensemble(name: str) -> AblationEnsembleModel:
    directory = MODEL_OUTPUT / name
    if (directory / "model.npz").exists():
        return AblationEnsembleModel([_load_model(directory / "model.npz")], name)
    paths = sorted(directory.glob("seed_*.npz"))
    if not paths:
        raise FileNotFoundError(f"No ablation models for {name} in {directory}")
    return AblationEnsembleModel([_load_model(path) for path in paths], name)


def _validation_runs() -> list[dict]:
    return list(
        _iter_generation_runs(
            list(VALIDATION_SEEDS),
            base_variants() + mixed_variants(),
            ["held_out"],
        )
    )


def _run_frames(model: AblationEnsembleModel, runs: list[dict]) -> dict:
    arrays = defaultdict(list)
    for run_index, run in enumerate(runs):
        environment = resolve_floor(
            load_environments()[run["environment_id"]], run["floor_id"]
        )
        disabled = [
            anchor.id for anchor in environment.anchors if anchor.id not in run["subset"]
        ]
        settings = _settings_for_variant(run["variant"], run["seed"], run["route"], disabled)
        session = SimulationSession(environment, settings)
        session.ai_model = model
        session.restart()
        for step in range(STEPS):
            frame = session.advance(DT)
            arrays["run_index"].append(run_index)
            arrays["step"].append(step)
            arrays["available"].append(len(frame.measurements))
            arrays["proposed_error"].append(
                frame.evaluation.proposed_error
                if frame.evaluation.proposed_error is not None
                else np.nan
            )
            arrays["ai_error"].append(
                frame.evaluation.ai_error
                if frame.evaluation.ai_error is not None
                else np.nan
            )
            arrays["proposed_updated"].append(int(frame.tracking.updated))
            arrays["ai_updated"].append(
                int(frame.ai_tracking.updated if frame.ai_tracking is not None else False)
            )
            arrays["ai_ambiguous"].append(
                int(frame.ai_metadata.ambiguous if frame.ai_metadata is not None else True)
            )
            arrays["ai_predicted_nlos"].append(
                int(frame.ai_metadata.n_predicted_nlos if frame.ai_metadata is not None else 0)
            )
    return {key: np.asarray(value) for key, value in arrays.items()}


def _rmse(values: np.ndarray) -> float | None:
    values = values[np.isfinite(values)]
    return float(np.sqrt(np.mean(values**2))) if values.size else None


def _bootstrap_rmse_difference(
    proposed: np.ndarray,
    ai: np.ndarray,
    run_index: np.ndarray,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> dict:
    valid = np.isfinite(proposed) & np.isfinite(ai)
    proposed = proposed[valid]
    ai = ai[valid]
    runs = run_index[valid]
    run_to_indices = {int(run): np.flatnonzero(runs == run) for run in np.unique(runs)}
    unique_runs = np.asarray(sorted(run_to_indices), dtype=int)
    observed = _rmse(ai) - _rmse(proposed)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples = []
    for _ in range(resamples):
        chosen = rng.choice(unique_runs, size=len(unique_runs), replace=True)
        indices = np.concatenate([run_to_indices[int(run)] for run in chosen])
        difference = _rmse(ai[indices]) - _rmse(proposed[indices])
        samples.append(difference)
    values = np.asarray(samples, dtype=float)
    return {
        "proposed_rmse": _rmse(proposed),
        "ai_rmse": _rmse(ai),
        "difference": observed,
        "ci_low": float(np.percentile(values, 2.5)),
        "ci_high": float(np.percentile(values, 97.5)),
        "resamples": int(values.size),
        "frames": int(len(proposed)),
    }


def _e2e_rows(
    model_name: str,
    frames: dict,
    runs: list[dict],
) -> tuple[list[dict], list[dict]]:
    condition_variant = np.asarray([run["variant"]["id"] for run in runs])[
        frames["run_index"]
    ]
    scenario = np.asarray([run["variant"]["scenario"] for run in runs])[
        frames["run_index"]
    ]
    layout = np.asarray([run["layout"] for run in runs])[frames["run_index"]]
    count = np.asarray([run["count"] for run in runs])[frames["run_index"]]
    category = np.where(frames["available"] >= 3, "solvable", "coasting")
    rows = []
    bootstrap = []
    for group_name, mask in (
        ("overall", np.ones(len(category), dtype=bool)),
        ("solvable", category == "solvable"),
        ("coasting", category == "coasting"),
    ):
        row = {
            "model": model_name,
            "scope": "category",
            "condition": group_name,
            "anchors": "",
            "frames": int(np.sum(mask)),
        }
        row.update(_bootstrap_rmse_difference(
            frames["proposed_error"][mask], frames["ai_error"][mask], frames["run_index"][mask]
        ))
        row["proposed_update_rate"] = float(np.mean(frames["proposed_updated"][mask]))
        row["ai_update_rate"] = float(np.mean(frames["ai_updated"][mask]))
        rows.append(row)
        bootstrap.append({"model": model_name, "scope": "category", "condition": group_name, **row})
    for group_name, values in (
        ("variant", condition_variant),
        ("scenario", scenario),
        ("layout", layout),
        ("anchors", count),
    ):
        for value in sorted(set(values.tolist())):
            mask = values == value
            row = {
                "model": model_name,
                "scope": group_name,
                "condition": str(value),
                "anchors": str(value) if group_name == "anchors" else "",
                "frames": int(np.sum(mask)),
            }
            row.update(_bootstrap_rmse_difference(
                frames["proposed_error"][mask], frames["ai_error"][mask], frames["run_index"][mask]
            ))
            row["proposed_update_rate"] = float(np.mean(frames["proposed_updated"][mask]))
            row["ai_update_rate"] = float(np.mean(frames["ai_updated"][mask]))
            rows.append(row)
            bootstrap.append(
                {
                    "model": model_name,
                    "scope": group_name,
                    "condition": str(value),
                    **row,
                }
            )
    for value in sorted(set(condition_variant.tolist())):
        for anchor_count in (3, 4, 6, 8):
            mask = (condition_variant == value) & (count == anchor_count)
            if not np.any(mask):
                continue
            row = {
                "model": model_name,
                "scope": "variant_anchors",
                "condition": value,
                "anchors": str(anchor_count),
                "frames": int(np.sum(mask)),
            }
            row.update(_bootstrap_rmse_difference(
                frames["proposed_error"][mask], frames["ai_error"][mask], frames["run_index"][mask]
            ))
            row["proposed_update_rate"] = float(np.mean(frames["proposed_updated"][mask]))
            row["ai_update_rate"] = float(np.mean(frames["ai_updated"][mask]))
            rows.append(row)
            if value in ("nlos_packet", "nlos_packet_outage", "noise_nlos_packet", "all_four"):
                bootstrap.append(
                    {
                        "model": model_name,
                        "scope": "variant_anchors",
                        "condition": value,
                        "anchors": str(anchor_count),
                        **row,
                    }
                )
    return rows, bootstrap


def run_end_to_end() -> dict:
    started = perf_counter()
    runs = _validation_runs()
    catalog = [
        {
            "run_index": index,
            "layout": run["layout"],
            "environment_id": run["environment_id"],
            "floor_id": run["floor_id"],
            "route": run["route"],
            "seed": run["seed"],
            "variant": run["variant"]["id"],
            "scenario": run["variant"]["scenario"],
            "count": run["count"],
            "subset": run["subset"],
        }
        for index, run in enumerate(runs)
    ]
    write_json(OUTPUT / "end-to-end" / "run-catalog.json", {"runs": catalog})
    frame_dir = OUTPUT / "end-to-end" / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    bootstrap_rows = []
    for variant in VARIANTS:
        name = variant["name"]
        path = frame_dir / f"{name}.npz"
        if path.exists():
            data = np.load(path)
            frames = {key: data[key] for key in data.files}
        else:
            model = load_ensemble(name)
            frames = _run_frames(model, runs)
            np.savez_compressed(path, **frames)
        variant_rows, variant_bootstrap = _e2e_rows(name, frames, runs)
        rows.extend(variant_rows)
        bootstrap_rows.extend(variant_bootstrap)
        print(f"end-to-end {name}: {len(frames['run_index'])} frames", flush=True)
    write_csv(OUTPUT / "end-to-end" / "metrics.csv", rows)
    write_csv(OUTPUT / "end-to-end" / "bootstrap.csv", bootstrap_rows)
    write_json(
        OUTPUT / "end-to-end" / "summary.json",
        {
            "scope": (
                "validation seeds 11,22; all 18 fault variants; "
                "anchor counts 3,4,6,8; 50 frames"
            ),
            "policy": {
                "floor": POLICY_FLOOR,
                "threshold": POLICY_THRESHOLD,
                "ambiguous": "predicted_los < 3",
            },
            "comparison": (
                "AI-assisted LOCUS-X versus statistical LOCUS-X on the same tracked frames"
            ),
            "solvable": "at least three valid measurements in the frame",
            "coasting": "fewer than three valid measurements in the frame",
            "elapsed_seconds": perf_counter() - started,
        },
    )
    return {"rows": rows, "bootstrap": bootstrap_rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--classifier", action="store_true")
    parser.add_argument("--loo", action="store_true")
    parser.add_argument("--end-to-end", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.all or args.generate:
        print(json.dumps(generate_phase2_dataset(), indent=2))
    if args.all or args.classifier:
        result = run_classifier_ablations()
        print(json.dumps({"classifier_rows": len(result["summary"])}, indent=2))
    if args.all or args.loo:
        result = run_leave_one_layout_out()
        print(json.dumps({"loo_rows": len(result["summary"])}, indent=2))
    if args.all or args.end_to_end:
        result = run_end_to_end()
        print(json.dumps({"end_to_end_rows": len(result["rows"])}, indent=2))


if __name__ == "__main__":
    main()
