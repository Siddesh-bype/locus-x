"""Reproducible dataset generation and compact NumPy MLP training for LOCUS-X AI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from .environment import load_environments, resolve_floor, route_position
from .estimators import solve
from .reliability import (
    FEATURE_GROUPS,
    FEATURE_NAMES,
    FeatureHistory,
    ReliabilityModel,
    extract_features,
)
from .schemas import PRESETS, Settings
from .simulation import generate_measurements

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET_CONFIG = ROOT / "configs" / "ai" / "reliability-dataset-v1.json"
DEFAULT_DATASET_DIR = ROOT / "artifacts" / "ai-reliability" / "dataset-v1"
DEFAULT_MODEL_DIR = ROOT / "models" / "ai-reliability-v1"
RANDOM_SEED = 20261021
LABEL_RULE = (
    "y=1 (NLOS) iff an available observation has simulator kind='nlos' "
    "(positive wall-induced bias with walls enabled); y=0 iff kind='los'. "
    "Disabled, outage, and packet-loss observations are excluded from supervision."
)


@dataclass
class RunSpec:
    split: str
    environment_id: str
    floor_id: str | None
    route: str
    seed: int
    count: int
    subset: list[str]
    variant: dict


def load_dataset_config(path: Path = DEFAULT_DATASET_CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def config_sha256(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _resolve_environment(environment_id: str, floor_id: str | None):
    environments = load_environments()
    return resolve_floor(environments[environment_id], floor_id)


def iter_run_specs(config: dict, split: str):
    split_config = config["splits"][split]
    for environment in split_config["environments"]:
        for route in split_config["routes"]:
            for seed in split_config["seeds"]:
                for variant in split_config["variants"]:
                    for count in (3, 4, 6, 8):
                        for subset in config["anchor_subsets"][str(count)]:
                            yield RunSpec(
                                split=split,
                                environment_id=environment["environment_id"],
                                floor_id=environment["floor_id"],
                                route=route,
                                seed=seed,
                                count=count,
                                subset=subset,
                                variant=variant,
                            )


def _make_settings(config: dict, spec: RunSpec, anchors: list[str]) -> Settings:
    scenario = spec.variant["scenario"]
    values = {
        "scenario": scenario,
        "seed": spec.seed,
        "route": spec.route,
        "speed": float(config.get("speed", 1.2)),
        "disabled_anchors": [anchor for anchor in anchors if anchor not in spec.subset],
        **PRESETS[scenario],
        **spec.variant.get("overrides", {}),
    }
    return Settings(**values)


def generate_split(config: dict, split: str, dataset_dir: Path = DEFAULT_DATASET_DIR) -> dict:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    dt = float(config["dt"])
    steps = int(config["steps"])
    matrices, labels, run_ids, step_ids, anchor_ids = [], [], [], [], []
    runs = []
    for run_id, spec in enumerate(iter_run_specs(config, split)):
        environment = _resolve_environment(spec.environment_id, spec.floor_id)
        settings = _make_settings(config, spec, [anchor.id for anchor in environment.anchors])
        history = FeatureHistory()
        run_rows = 0
        run_positive = 0
        for step in range(steps):
            timestamp = round(step * dt, 8)
            truth = route_position(environment.routes[settings.route], timestamp, settings.speed)
            measurements, fault_labels = generate_measurements(
                environment, settings, truth, timestamp, step
            )
            baseline = solve(measurements)
            batch = extract_features(measurements, environment, baseline, history)
            if batch is None:
                continue
            label_by_anchor = {
                label.anchor_id: label
                for label in fault_labels
                if label.available and label.kind in ("los", "nlos")
            }
            for anchor_id, row in zip(batch.anchors, batch.matrix, strict=True):
                label = label_by_anchor.get(anchor_id)
                if label is None:
                    continue
                matrices.append(row)
                labels.append(1 if label.kind == "nlos" else 0)
                run_ids.append(run_id)
                step_ids.append(step)
                anchor_ids.append(int(anchor_id[1:]) - 1)
                run_rows += 1
                run_positive += int(label.kind == "nlos")
        runs.append(
            {
                "run_id": run_id,
                "environment_id": spec.environment_id,
                "floor_id": spec.floor_id or environment.id,
                "route": spec.route,
                "seed": spec.seed,
                "count": spec.count,
                "subset": spec.subset,
                "variant": spec.variant["id"],
                "scenario": spec.variant["scenario"],
                "frames": steps,
                "samples": run_rows,
                "nlos_samples": run_positive,
            }
        )
    if not matrices:
        raise RuntimeError(f"No usable samples generated for split {split}")
    X = np.asarray(matrices, dtype=np.float32)
    y = np.asarray(labels, dtype=np.int8)
    np.savez_compressed(
        dataset_dir / f"{split}.npz",
        X=X,
        y=y,
        run_id=np.asarray(run_ids, dtype=np.int32),
        step=np.asarray(step_ids, dtype=np.int16),
        anchor_index=np.asarray(anchor_ids, dtype=np.int8),
    )
    dataset_file = dataset_dir / f"{split}.npz"
    manifest = {
        "split": split,
        "dataset_version": config["dataset_version"],
        "label_rule": LABEL_RULE,
        "feature_names": FEATURE_NAMES,
        "config_sha256": config_sha256(config),
        "file": str(dataset_file.relative_to(ROOT)),
        "file_sha256": hashlib.sha256(dataset_file.read_bytes()).hexdigest(),
        "samples": int(len(y)),
        "los_samples": int(np.sum(y == 0)),
        "nlos_samples": int(np.sum(y == 1)),
        "runs": len(runs),
        "run_catalog": runs,
    }
    (dataset_dir / f"{split}-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def generate_dataset(
    config_path: Path = DEFAULT_DATASET_CONFIG, dataset_dir: Path = DEFAULT_DATASET_DIR
) -> dict:
    config = load_dataset_config(config_path)
    manifests = {
        split: generate_split(config, split, dataset_dir)
        for split in ("train", "validation", "test")
    }
    summary = {
        "dataset_version": config["dataset_version"],
        "config_path": str(config_path.relative_to(ROOT)),
        "config_sha256": config_sha256(config),
        "splits": {
            split: {
                key: manifest[key]
                for key in (
                    "samples",
                    "los_samples",
                    "nlos_samples",
                    "runs",
                    "file",
                    "file_sha256",
                )
            }
            for split, manifest in manifests.items()
        },
    }
    (dataset_dir / "dataset-summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return summary


def load_split(split: str, dataset_dir: Path = DEFAULT_DATASET_DIR):
    data = np.load(dataset_dir / f"{split}.npz")
    return (
        data["X"].astype(np.float64),
        data["y"].astype(np.int64),
        data["run_id"],
    )


def _roc_auc(y: np.ndarray, probabilities: np.ndarray) -> float | None:
    positives = int(np.sum(y == 1))
    negatives = int(np.sum(y == 0))
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(probabilities)
    ranks = np.empty(len(probabilities), dtype=float)
    sorted_probabilities = probabilities[order]
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and sorted_probabilities[end] == sorted_probabilities[start]:
            end += 1
        ranks[order[start:end]] = (start + end + 1) / 2
        start = end
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives))


def classification_metrics(
    y: np.ndarray, probabilities: np.ndarray, threshold: float = 0.5
) -> dict:
    probabilities = np.asarray(probabilities, dtype=float)
    predicted = probabilities >= threshold
    positive = y == 1
    tp = int(np.sum(predicted & positive))
    fp = int(np.sum(predicted & ~positive))
    fn = int(np.sum(~predicted & positive))
    tn = int(np.sum(~predicted & ~positive))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / max(1, len(y))
    bins = np.linspace(0, 1, 11)
    expected_calibration_error = 0.0
    calibration = []
    for lower, upper in zip(bins[:-1], bins[1:], strict=True):
        selected = (probabilities >= lower) & (
            probabilities < upper if upper < 1 else probabilities <= upper
        )
        if not np.any(selected):
            continue
        confidence = float(np.mean(probabilities[selected]))
        frequency = float(np.mean(y[selected]))
        expected_calibration_error += float(np.sum(selected)) / len(y) * abs(confidence - frequency)
        calibration.append(
            {
                "lower": float(lower),
                "upper": float(upper),
                "count": int(np.sum(selected)),
                "confidence": confidence,
                "frequency": frequency,
            }
        )
    return {
        "samples": int(len(y)),
        "positives": int(np.sum(positive)),
        "negatives": int(np.sum(~positive)),
        "positive_rate": float(np.mean(positive)),
        "threshold": threshold,
        "accuracy": accuracy,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "roc_auc": _roc_auc(y, probabilities),
        "brier": float(np.mean((probabilities - y) ** 2)),
        "expected_calibration_error": expected_calibration_error,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "classwise": {
            "los": {
                "precision": tn / (tn + fn) if tn + fn else 0.0,
                "recall": tn / (tn + fp) if tn + fp else 0.0,
            },
            "nlos": {
                "precision": precision,
                "recall": recall,
            },
        },
        "calibration_bins": calibration,
    }


def _binary_nll(y: np.ndarray, probabilities: np.ndarray) -> float:
    clipped = np.clip(probabilities, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1e-3) -> tuple[np.ndarray, float]:
    design = np.column_stack([X, np.ones(len(X))])
    positives = max(1, int(np.sum(y == 1)))
    negatives = max(1, int(np.sum(y == 0)))
    sample_weight = np.where(y == 1, negatives / positives, 1.0)
    sample_weight /= np.mean(sample_weight)

    def objective(theta):
        logits = design @ theta
        loss = np.mean(sample_weight * np.logaddexp(0, logits) - sample_weight * y * logits)
        loss += 0.5 * l2 * float(theta[:-1] @ theta[:-1])
        error = sample_weight * (expit(logits) - y)
        gradient = design.T @ error / len(y)
        gradient[:-1] += l2 * theta[:-1]
        return float(loss), gradient

    result = minimize(
        objective,
        np.zeros(design.shape[1]),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": 500},
    )
    return result.x[:-1], float(result.x[-1])


@dataclass
class MLPWeights:
    W1: np.ndarray
    b1: np.ndarray
    W2: np.ndarray
    b2: np.ndarray
    W3: np.ndarray
    b3: np.ndarray


def _init_mlp(rng: np.random.Generator, n_features: int, hidden: tuple[int, int]) -> MLPWeights:
    first, second = hidden

    def initialized(rows, columns):
        return rng.normal(0, np.sqrt(2 / rows), size=(rows, columns))

    return MLPWeights(
        W1=initialized(n_features, first),
        b1=np.zeros(first),
        W2=initialized(first, second),
        b2=np.zeros(second),
        W3=initialized(second, 1),
        b3=np.zeros(1),
    )


def _forward(weights: MLPWeights, X: np.ndarray) -> tuple[np.ndarray, tuple]:
    z1 = X @ weights.W1 + weights.b1
    a1 = np.maximum(0.0, z1)
    z2 = a1 @ weights.W2 + weights.b2
    a2 = np.maximum(0.0, z2)
    z3 = a2 @ weights.W3 + weights.b3
    return z3.reshape(-1), (X, z1, a1, z2, a2, z3)


def _predict_mlp(weights: MLPWeights, X: np.ndarray) -> np.ndarray:
    return expit(_forward(weights, X)[0])


def fit_mlp(
    X: np.ndarray,
    y: np.ndarray,
    X_validation: np.ndarray,
    y_validation: np.ndarray,
    *,
    hidden: tuple[int, int] = (32, 16),
    epochs: int = 35,
    batch_size: int = 512,
    learning_rate: float = 1.5e-3,
    l2: float = 1e-4,
    seed: int = RANDOM_SEED,
) -> tuple[MLPWeights, dict]:
    rng = np.random.default_rng(seed)
    weights = _init_mlp(rng, X.shape[1], hidden)
    parameters = {
        "W1": weights.W1,
        "b1": weights.b1,
        "W2": weights.W2,
        "b2": weights.b2,
        "W3": weights.W3,
        "b3": weights.b3,
    }
    first_moment = {key: np.zeros_like(value) for key, value in parameters.items()}
    second_moment = {key: np.zeros_like(value) for key, value in parameters.items()}
    step = 0
    beta1, beta2 = 0.9, 0.999
    epsilon = 1e-8
    positives = max(1, int(np.sum(y == 1)))
    negatives = max(1, int(np.sum(y == 0)))
    positive_weight = negatives / positives
    sample_weight = np.where(y == 1, positive_weight, 1.0)
    best_loss = float("inf")
    best_weights = None
    best_epoch = 0
    stale = 0
    history = []
    for epoch in range(epochs):
        permutation = rng.permutation(len(X))
        epoch_loss = 0.0
        for start in range(0, len(X), batch_size):
            indices = permutation[start : start + batch_size]
            X_batch = X[indices]
            y_batch = y[indices]
            batch_weight = sample_weight[indices]
            logits, cache = _forward(weights, X_batch)
            probabilities = expit(logits)
            loss = float(
                np.mean(batch_weight * np.logaddexp(0, logits) - batch_weight * y_batch * logits)
            )
            loss += (
                0.5
                * l2
                * sum(float(np.sum(value**2)) for value in (weights.W1, weights.W2, weights.W3))
            )
            epoch_loss += loss * len(indices)
            X_batch_cached, z1, a1, z2, a2, z3 = cache
            dz3 = ((probabilities - y_batch) * batch_weight / len(indices)).reshape(-1, 1)
            dW3 = a2.T @ dz3 + l2 * weights.W3
            db3 = np.sum(dz3, axis=0)
            da2 = dz3 @ weights.W3.T
            dz2 = da2 * (z2 > 0)
            dW2 = a1.T @ dz2 + l2 * weights.W2
            db2 = np.sum(dz2, axis=0)
            da1 = dz2 @ weights.W2.T
            dz1 = da1 * (z1 > 0)
            dW1 = X_batch_cached.T @ dz1 + l2 * weights.W1
            db1 = np.sum(dz1, axis=0)
            gradients = {"W1": dW1, "b1": db1, "W2": dW2, "b2": db2, "W3": dW3, "b3": db3}
            step += 1
            for key, gradient in gradients.items():
                first_moment[key] = beta1 * first_moment[key] + (1 - beta1) * gradient
                second_moment[key] = beta2 * second_moment[key] + (1 - beta2) * gradient**2
                corrected_first = first_moment[key] / (1 - beta1**step)
                corrected_second = second_moment[key] / (1 - beta2**step)
                parameters[key] -= (
                    learning_rate * corrected_first / (np.sqrt(corrected_second) + epsilon)
                )
        validation_probabilities = _predict_mlp(weights, X_validation)
        validation_loss = _binary_nll(y_validation, validation_probabilities)
        history.append(
            {
                "epoch": epoch + 1,
                "train_loss": epoch_loss / len(X),
                "validation_log_loss": validation_loss,
            }
        )
        if validation_loss < best_loss - 1e-5:
            best_loss = validation_loss
            best_weights = MLPWeights(**{key: value.copy() for key, value in parameters.items()})
            best_epoch = epoch + 1
            stale = 0
        else:
            stale += 1
            if stale >= 6:
                break
    if best_weights is None:
        raise RuntimeError("MLP training did not produce a finite model")
    weights = best_weights
    return weights, {
        "best_epoch": best_epoch,
        "best_validation_log_loss": best_loss,
        "epochs_completed": len(history),
        "history": history,
        "hidden": list(hidden),
        "positive_class_weight": positive_weight,
    }


def fit_platt(logits: np.ndarray, y: np.ndarray) -> tuple[float, float, dict]:
    def objective(theta):
        slope, intercept = theta
        probabilities = expit(slope * logits + intercept)
        clipped = np.clip(probabilities, 1e-9, 1 - 1e-9)
        nll = -np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))
        return float(nll)

    result = minimize(
        objective,
        np.array([1.0, 0.0]),
        method="L-BFGS-B",
        bounds=[(0.01, 10.0), (-10.0, 10.0)],
    )
    slope, intercept = (float(result.x[0]), float(result.x[1]))
    return (
        slope,
        intercept,
        {
            "slope": slope,
            "intercept": intercept,
            "validation_log_loss": float(result.fun),
            "converged": bool(result.success),
        },
    )


def _feature_indices(groups: list[str] | None) -> np.ndarray:
    if groups is None:
        return np.arange(len(FEATURE_NAMES))
    selected = []
    for group in groups:
        selected.extend(FEATURE_GROUPS[group])
    return np.asarray([FEATURE_NAMES.index(name) for name in selected], dtype=int)


def _standardize(X_train, X_validation, X_test):
    mean = np.mean(X_train, axis=0)
    scale = np.std(X_train, axis=0)
    scale = np.where(scale < 1e-9, 1.0, scale)
    return (
        (X_train - mean) / scale,
        (X_validation - mean) / scale,
        (X_test - mean) / scale,
        mean,
        scale,
    )


def train_and_save(
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    model_dir: Path = DEFAULT_MODEL_DIR,
    epochs: int = 35,
) -> dict:
    model_dir.mkdir(parents=True, exist_ok=True)
    X_train, y_train, _ = load_split("train", dataset_dir)
    X_validation, y_validation, _ = load_split("validation", dataset_dir)
    X_test, y_test, _ = load_split("test", dataset_dir)
    X_train, X_validation, X_test, mean, scale = _standardize(X_train, X_validation, X_test)
    started = perf_counter()
    heuristic_probability = 1 - np.clip(X_test[:, FEATURE_NAMES.index("instant_reliability")], 0, 1)
    heuristic = classification_metrics(y_test, heuristic_probability)
    logistic_weights, logistic_bias = fit_logistic(X_train, y_train)
    logistic_validation_logits = X_validation @ logistic_weights + logistic_bias
    logistic_test_probabilities = expit(X_test @ logistic_weights + logistic_bias)
    logistic = classification_metrics(y_test, logistic_test_probabilities)
    logistic_validation = classification_metrics(y_validation, expit(logistic_validation_logits))
    mlp_weights, training = fit_mlp(
        X_train,
        y_train,
        X_validation,
        y_validation,
        epochs=epochs,
    )
    validation_logits = _forward(mlp_weights, X_validation)[0]
    calibration_slope, calibration_intercept, calibration = fit_platt(
        validation_logits, y_validation
    )
    test_logits = _forward(mlp_weights, X_test)[0]
    calibrated_test_probabilities = expit(calibration_slope * test_logits + calibration_intercept)
    mlp = classification_metrics(y_test, calibrated_test_probabilities)
    calibrated_validation_probabilities = expit(
        calibration_slope * validation_logits + calibration_intercept
    )
    mlp_validation = classification_metrics(y_validation, calibrated_validation_probabilities)
    ablation = {}
    for name, groups in (
        ("residual_geometry", ["residual", "geometry"]),
        ("residual_geometry_map", ["residual", "geometry", "map"]),
        ("all_features", ["residual", "geometry", "map", "temporal"]),
    ):
        indices = _feature_indices(groups)
        subset_weights, subset_training = fit_mlp(
            X_train[:, indices],
            y_train,
            X_validation[:, indices],
            y_validation,
            epochs=max(12, epochs // 2),
            seed=RANDOM_SEED + len(indices),
        )
        subset_validation_logits = _forward(subset_weights, X_validation[:, indices])[0]
        subset_slope, subset_intercept, _ = fit_platt(subset_validation_logits, y_validation)
        subset_test_probabilities = expit(
            subset_slope * _forward(subset_weights, X_test[:, indices])[0] + subset_intercept
        )
        ablation[name] = {
            "feature_count": len(indices),
            "validation": classification_metrics(
                y_validation,
                expit(subset_slope * subset_validation_logits + subset_intercept),
            ),
            "test": classification_metrics(y_test, subset_test_probabilities),
            "training": {
                "best_epoch": subset_training["best_epoch"],
                "best_validation_log_loss": subset_training["best_validation_log_loss"],
            },
        }
    np.savez_compressed(
        model_dir / "weights.npz",
        W1=mlp_weights.W1,
        b1=mlp_weights.b1,
        W2=mlp_weights.W2,
        b2=mlp_weights.b2,
        W3=mlp_weights.W3,
        b3=mlp_weights.b3,
    )
    model_config = {
        "model_version": "ai-reliability-v1",
        "feature_version": "1.0.0",
        "dataset_version": "1.0.0",
        "feature_names": FEATURE_NAMES,
        "feature_mean": mean.tolist(),
        "feature_scale": scale.tolist(),
        "architecture": {"layers": [len(FEATURE_NAMES), 32, 16, 1], "activation": "relu"},
        "calibration": {
            "method": "platt",
            "slope": calibration_slope,
            "intercept": calibration_intercept,
        },
        "weight_policy": {
            "floor": 0.08,
            "threshold": 0.25,
            "formula": (
                "1 when p_los >= threshold; otherwise floor + (1-floor) * p_los / threshold"
            ),
            "ambiguous_rule": "predicted_los < 3",
        },
        "training_seed": RANDOM_SEED,
        "label_rule": LABEL_RULE,
        "dataset_config": "configs/ai/reliability-dataset-v1.json",
    }
    (model_dir / "config.json").write_text(json.dumps(model_config, indent=2), encoding="utf-8")
    dataset_summary = json.loads((dataset_dir / "dataset-summary.json").read_text(encoding="utf-8"))
    model_size = sum(
        path.stat().st_size for path in (model_dir / "weights.npz", model_dir / "config.json")
    )
    metrics = {
        "model_version": "ai-reliability-v1",
        "dataset_version": "1.0.0",
        "dataset": dataset_summary,
        "training": training,
        "calibration": calibration,
        "methods": {
            "heuristic_residual_threshold": heuristic,
            "logistic_regression": {
                "validation": logistic_validation,
                "test": logistic,
            },
            "mlp": {"validation": mlp_validation, "test": mlp},
        },
        "feature_ablation": ablation,
        "model_size_bytes": model_size,
        "train_seconds": perf_counter() - started,
    }
    (model_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def measure_inference_latency(model: ReliabilityModel, iterations: int = 200) -> dict:
    environment = _resolve_environment("hospital_floor_01", None)
    settings = Settings(scenario="nlos", seed=404, route="held_out")
    truth = route_position(environment.routes[settings.route], 10.0, settings.speed)
    measurements, _ = generate_measurements(environment, settings, truth, 10.0, 20)
    baseline = solve(measurements)
    timings = []
    for _ in range(iterations):
        history = FeatureHistory()
        start = perf_counter()
        model.predict(measurements, environment, baseline, history)
        timings.append((perf_counter() - start) * 1000)
    return {
        "iterations": iterations,
        "mean_ms": float(np.mean(timings)),
        "median_ms": float(np.median(timings)),
        "p95_ms": float(np.percentile(timings, 95)),
    }


def write_model_card(model_dir: Path, latency: dict | None = None) -> dict:
    metrics = json.loads((model_dir / "metrics.json").read_text(encoding="utf-8"))
    model = ReliabilityModel.load(model_dir)
    if latency is None:
        latency = measure_inference_latency(model)
    metrics["inference_latency"] = latency
    (model_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true", help="Generate all dataset splits")
    parser.add_argument("--train", action="store_true", help="Train and save the AI model")
    parser.add_argument(
        "--all", action="store_true", help="Generate data, train, and measure latency"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_DATASET_CONFIG)
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--epochs", type=int, default=35)
    args = parser.parse_args()
    if args.all or args.generate:
        print(json.dumps(generate_dataset(args.config, args.dataset_dir), indent=2))
    if args.all or args.train:
        metrics = train_and_save(args.dataset_dir, args.model_dir, args.epochs)
        print(
            json.dumps(
                {
                    "model": metrics["model_version"],
                    "mlp_test_f1": metrics["methods"]["mlp"]["test"]["f1"],
                    "mlp_test_auc": metrics["methods"]["mlp"]["test"]["roc_auc"],
                    "model_size_bytes": metrics["model_size_bytes"],
                },
                indent=2,
            )
        )
        write_model_card(args.model_dir)
        print(f"Saved model to {args.model_dir}")


if __name__ == "__main__":
    main()
