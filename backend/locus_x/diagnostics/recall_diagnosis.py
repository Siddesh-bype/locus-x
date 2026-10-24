"""Analysis-only diagnosis of NLOS classifier recall.

This module reads post-hoc truth labels and saved caches. It is deliberately
outside the inference package path: no serving, estimator, tracker, policy or
model-loading code imports it.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import numpy as np

from ..ai_ablation import (
    BOOTSTRAP_SEED,
    expected_calibration_error,
    grouped_calibration_split,
)
from ..ai_phase3 import _nlos_group, _settings, _variants, load_config, load_frames, load_samples
from ..environment import load_environments, resolve_floor
from ..estimators import solve
from ..reliability import ReliabilityModel
from ..reliability_v2 import FEATURE_NAMES_V2, ReliabilityV2Model
from ..schemas import Measurement
from ..tracking import MotionTracker

ROOT = Path(__file__).resolve().parents[3]
FINAL_DIR = ROOT / "artifacts" / "ai-reliability" / "final-test"
VALIDATION_DIR = ROOT / "artifacts" / "ai-reliability" / "phase3-v2" / "dataset"
POLICY_DIR = ROOT / "artifacts" / "ai-reliability" / "phase4-final" / "policy"
MODEL_V1 = ROOT / "models" / "ai-reliability-v1"
MODEL_V2 = ROOT / "models" / "ai-reliability-v2"
OUTPUT = ROOT / "artifacts" / "ai-reliability" / "recall-diagnosis"
SEED_MANIFEST = ROOT / "configs" / "ai" / "recall-fix-test-seeds-v1.json"
THRESHOLDS = (0.1, 0.2, 0.3, 0.4, 0.5, 0.7, 0.9, 0.95)
POLICY_BAR = 0.95
POLICY_FLOOR = 0.05
N_ANCHORS = 8
COUNTS = (3, 4, 5, 6, 7, 8)
BOOTSTRAP = 2000
DT = 0.5
GROUP_NAMES = ("one", "minority", "half", "majority", "all")
FINAL_TEST_SEEDS = {8001, 8009, 8017, 8023, 8039, 8053, 8069, 8081}
POST_HOC_LABEL = "post-hoc diagnostic on inspected data"


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
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = fields or list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _finite(values: np.ndarray) -> np.ndarray:
    return np.asarray(values, dtype=float)[np.isfinite(values)]


def _rmse(values: np.ndarray) -> float:
    values = _finite(values)
    return float(np.sqrt(np.mean(values**2))) if len(values) else float("nan")


def _rate(mask: np.ndarray) -> float:
    return float(np.mean(mask)) if len(mask) else float("nan")


def _bootstrap_difference(
    stat: np.ndarray,
    ai: np.ndarray,
    runs: np.ndarray,
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP,
) -> dict:
    valid = np.isfinite(stat) & np.isfinite(ai)
    stat = np.asarray(stat, dtype=float)[valid]
    ai = np.asarray(ai, dtype=float)[valid]
    runs = np.asarray(runs)[valid]
    unique = np.unique(runs)
    if len(unique) == 0:
        return {
            "stat_rmse": None,
            "method_rmse": None,
            "difference": None,
            "relative_percent": None,
            "ci_low": None,
            "ci_high": None,
            "relative_ci_low": None,
            "relative_ci_high": None,
            "runs": 0,
            "frames": 0,
        }
    inverse = np.searchsorted(unique, runs)
    stat_sq = np.bincount(inverse, weights=stat**2, minlength=len(unique))
    ai_sq = np.bincount(inverse, weights=ai**2, minlength=len(unique))
    counts = np.bincount(inverse, minlength=len(unique)).astype(float)
    rng = np.random.default_rng(seed)
    chosen = rng.integers(0, len(unique), size=(resamples, len(unique)))
    stat_sample = np.sqrt(stat_sq[chosen].sum(axis=1) / counts[chosen].sum(axis=1))
    ai_sample = np.sqrt(ai_sq[chosen].sum(axis=1) / counts[chosen].sum(axis=1))
    differences = ai_sample - stat_sample
    stat_rmse = _rmse(stat)
    ai_rmse = _rmse(ai)
    difference = ai_rmse - stat_rmse
    relative = 100.0 * difference / stat_rmse if stat_rmse else float("nan")
    relative_samples = 100.0 * differences / stat_sample
    return {
        "stat_rmse": stat_rmse,
        "method_rmse": ai_rmse,
        "difference": difference,
        "relative_percent": relative,
        "ci_low": float(np.percentile(differences, 2.5)),
        "ci_high": float(np.percentile(differences, 97.5)),
        "relative_ci_low": float(np.nanpercentile(relative_samples, 2.5)),
        "relative_ci_high": float(np.nanpercentile(relative_samples, 97.5)),
        "runs": int(len(unique)),
        "frames": int(len(stat)),
    }


def _classification(y: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    y = np.asarray(y, dtype=int)
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
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return {
        "samples": int(len(y)),
        "positives": int(positive.sum()),
        "threshold": float(threshold),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "specificity": specificity,
        "positive_rate": float(predicted.mean()) if len(y) else 0.0,
        "true_positive_rate": float(positive.mean()) if len(y) else 0.0,
    }


def _pr_points(y: np.ndarray, probabilities: np.ndarray) -> list[dict]:
    y = np.asarray(y, dtype=int)
    probabilities = np.asarray(probabilities, dtype=float)
    positive = y == 1
    positives = int(positive.sum())
    rows = []
    for threshold in np.round(np.arange(0.0, 1.0001, 0.005), 3):
        predicted = probabilities >= threshold
        tp = int(np.sum(predicted & positive))
        fp = int(np.sum(predicted & ~positive))
        recall = tp / positives if positives else 0.0
        precision = tp / (tp + fp) if tp + fp else 1.0
        rows.append(
            {
                "threshold": float(threshold),
                "recall": recall,
                "precision": precision,
                "predicted_positive_rate": float(predicted.mean()) if len(y) else 0.0,
            }
        )
    return rows


def load_final_data() -> tuple[dict, dict]:
    with np.load(FINAL_DIR / "classifier-samples.npz", allow_pickle=False) as arrays:
        samples = {name: np.asarray(arrays[name]) for name in arrays.files}
    with np.load(FINAL_DIR / "frames.npz", allow_pickle=False) as arrays:
        frames = {name: np.asarray(arrays[name]) for name in arrays.files}
    return samples, frames


def load_validation_data() -> tuple[dict, dict, dict, dict]:
    samples, sample_manifest = load_samples("validation")
    frames, frame_manifest = load_frames("validation")
    v1 = np.load(
        ROOT
        / "artifacts"
        / "ai-reliability"
        / "phase3-v2"
        / "classifier"
        / "v1-validation-probabilities.npy",
        mmap_mode="r",
    )
    v2 = np.load(
        ROOT
        / "artifacts"
        / "ai-reliability"
        / "phase3-v2"
        / "models"
        / "g5"
        / "validation-probabilities.npy",
        mmap_mode="r",
    )
    count = int(sample_manifest["samples"])
    samples["p_v1"] = np.asarray(v1[:count], dtype=float)
    samples["p_v2"] = np.asarray(v2[:count], dtype=float)
    return samples, frames, sample_manifest, frame_manifest


def _frame_positions(samples: dict, frames: dict) -> np.ndarray:
    frame_keys = np.asarray(frames["run_id"], dtype=np.int64) * 1000 + np.asarray(
        frames["step"], dtype=np.int64
    )
    sample_keys = np.asarray(samples["run_id"], dtype=np.int64) * 1000 + np.asarray(
        samples["step"], dtype=np.int64
    )
    order = np.argsort(frame_keys)
    sorted_keys = frame_keys[order]
    positions = np.searchsorted(sorted_keys, sample_keys)
    if np.any(positions >= len(sorted_keys)):
        raise RuntimeError("sample-to-frame mapping overflow")
    indices = order[positions]
    if not np.array_equal(frame_keys[indices], sample_keys):
        raise RuntimeError("sample-to-frame mapping mismatch")
    return indices


def _probability_matrix(samples: dict, probabilities: np.ndarray, frames: dict) -> np.ndarray:
    indices = _frame_positions(samples, frames)
    matrix = np.full((len(frames["run_id"]), N_ANCHORS), np.nan, dtype=float)
    anchors = np.asarray(samples["anchor_index"], dtype=int)
    matrix[indices, anchors] = np.asarray(probabilities, dtype=float)
    return matrix


def _sample_gaps(samples: dict, frames: dict) -> np.ndarray:
    frame_ids = np.asarray(frames["run_id"], dtype=int)
    steps = np.asarray(frames["step"], dtype=int)
    available = np.asarray(frames["available"], dtype=bool)
    gap_matrix = np.full((len(frame_ids), N_ANCHORS), -1, dtype=np.int16)
    for run_id in np.unique(frame_ids):
        indices = np.flatnonzero(frame_ids == run_id)
        last_seen = np.full(N_ANCHORS, -1, dtype=int)
        for index in indices:
            current = np.flatnonzero(available[index])
            for anchor in current:
                if last_seen[anchor] >= 0:
                    gap_matrix[index, anchor] = steps[index] - last_seen[anchor]
            last_seen[current] = steps[index]
    indices = _frame_positions(samples, frames)
    anchors = np.asarray(samples["anchor_index"], dtype=int)
    return gap_matrix[indices, anchors]


def _anchor_positions(frame_manifest: dict) -> dict[int, np.ndarray]:
    environments = {}
    for row in frame_manifest["run_catalog"]:
        key = (row["environment_id"], row["floor_id"])
        if key not in environments:
            environment = resolve_floor(load_environments()[row["environment_id"]], row["floor_id"])
            environments[key] = np.asarray(
                [anchor.position for anchor in environment.anchors], dtype=float
            )
    positions = {}
    for index, _name in enumerate(frame_manifest["layouts"]):
        row = next(row for row in frame_manifest["run_catalog"] if row["layout_index"] == index)
        positions[index] = environments[(row["environment_id"], row["floor_id"])]
    if not positions:
        raise RuntimeError(f"no layouts for {frame_manifest.get('split')}")
    return positions


def _variant_settings(frame_manifest: dict, variant_index: int) -> dict:
    config = load_config()
    variants = _variants(config["splits"]["validation"])
    variant = variants[variant_index]
    settings = _settings(variant, 0, "held_out", [])
    return {
        "variant": variant["id"],
        "scenario": variant["scenario"],
        "nlos_bias": float(settings.nlos_bias),
        "noise_std": float(settings.noise_std),
        "walls_enabled": bool(settings.walls_enabled),
        "bias_kind": "variant_nominal",
    }


def _bias_label(value: float) -> str:
    if value <= 0:
        return "0"
    if value <= 2:
        return "(0,2]"
    if value <= 4:
        return "(2,4]"
    return ">4"


def _noise_label(value: float) -> str:
    if value <= 0:
        return "0"
    if value <= 0.3:
        return "(0,0.3]"
    if value <= 0.8:
        return "(0.3,0.8]"
    return ">0.8"


def _range_label(value: float) -> str:
    if value <= 5:
        return "0-5"
    if value <= 10:
        return "5-10"
    if value <= 20:
        return "10-20"
    if value <= 40:
        return "20-40"
    return ">40"


def _gap_label(value: int) -> str:
    if value < 0:
        return "never"
    if value <= 1:
        return "0-1"
    if value <= 5:
        return "2-5"
    if value <= 20:
        return "6-20"
    return ">20"


def _fraction_label(n_nlos: np.ndarray, n_available: np.ndarray) -> np.ndarray:
    fraction = np.divide(
        n_nlos,
        n_available,
        out=np.zeros(len(n_nlos), dtype=float),
        where=n_available > 0,
    )
    labels = np.full(len(n_nlos), "0", dtype=object)
    labels[(fraction > 0) & (fraction < 0.5)] = "(0,0.5)"
    labels[(fraction == 0.5) & (n_available > 0)] = "0.5"
    labels[(fraction > 0.5) & (fraction < 1)] = "(0.5,1)"
    labels[(fraction == 1) & (n_available > 0)] = "1"
    return labels


def _residual_label(values: np.ndarray) -> np.ndarray:
    labels = np.full(len(values), "<=0.5", dtype=object)
    labels[(values > 0.5) & (values <= 1.0)] = "(0.5,1]"
    labels[(values > 1.0) & (values <= 2.0)] = "(1,2]"
    labels[(values > 2.0) & (values <= 5.0)] = "(2,5]"
    labels[values > 5.0] = ">5"
    return labels


def _stratum_metrics(
    *,
    source: str,
    model: str,
    stratum_type: str,
    labels: np.ndarray,
    y: np.ndarray,
    probabilities: np.ndarray,
) -> list[dict]:
    rows = []
    for label in sorted({str(value) for value in labels}):
        mask = labels.astype(str) == label
        if not np.any(mask):
            continue
        selected_y = y[mask]
        selected_p = probabilities[mask]
        positive = selected_y == 1
        if not np.any(positive):
            continue
        at_half = _classification(selected_y, selected_p, 0.5)
        at_bar = _classification(selected_y, selected_p, POLICY_BAR)
        missed = int(np.sum(positive & (selected_p < 0.5)))
        rows.append(
            {
                "source": source,
                "model": model,
                "stratum_type": stratum_type,
                "stratum": label,
                "samples": int(mask.sum()),
                "nlos_samples": int(positive.sum()),
                "los_samples": int((~positive).sum()),
                "recall_0.5": at_half["recall"],
                "recall_policy_bar": at_bar["recall"],
                "precision_0.5": at_half["precision"],
                "specificity_0.5": at_half["specificity"],
                "mean_p_nlos_true_nlos": float(np.mean(selected_p[positive])),
                "mean_p_nlos_true_los": float(np.mean(selected_p[~positive]))
                if np.any(~positive)
                else float("nan"),
                "missed_nlos": missed,
                "missed_share": missed / int(positive.sum()),
            }
        )
    return rows


def _final_base(samples: dict, frames: dict) -> dict:
    indices = _frame_positions(samples, frames)
    return {
        "y": np.asarray(samples["y"], dtype=int),
        "p_v1": np.asarray(samples["p_v1"], dtype=float),
        "p_v2": np.asarray(samples["p_v2"], dtype=float),
        "run_id": np.asarray(samples["run_id"], dtype=int),
        "step": np.asarray(samples["step"], dtype=int),
        "count": np.asarray(samples["count"], dtype=int),
        "layout_index": np.asarray(samples["layout_index"], dtype=int),
        "variant_index": np.asarray(samples["variant_index"], dtype=int),
        "condition": np.asarray(samples["condition"]).astype(str),
        "n_available": np.asarray(frames["n_available"], dtype=int)[indices],
        "n_nlos": np.asarray(frames["n_nlos"], dtype=int)[indices],
        "v2_fallback": np.asarray(frames["v2_fallback"], dtype=int)[indices],
        "v2_changed": np.asarray(frames["v2_changed"], dtype=int)[indices],
        "gap": None,
        "true_range": None,
        "true_bias": None,
        "noise_std": None,
        "wall_count": None,
        "abs_residual": None,
    }


def _validation_base(samples: dict, frames: dict, frame_manifest: dict) -> dict:
    indices = _frame_positions(samples, frames)
    anchors = np.asarray(samples["anchor_index"], dtype=int)
    layouts = np.asarray(samples["layout_index"], dtype=int)
    truth = np.asarray(frames["truth"], dtype=float)
    positions = _anchor_positions(frame_manifest)
    anchor_positions = np.stack([positions[int(layout)] for layout in layouts])
    sample_positions = anchor_positions[np.arange(len(indices)), anchors]
    true_range = np.linalg.norm(truth[indices] - sample_positions, axis=1)
    wall_count = np.rint(np.asarray(samples["X"][:, 14], dtype=float) * 3.0).astype(int)
    wall_count = np.clip(wall_count, 0, 3)
    variant_index = np.asarray(samples["variant_index"], dtype=int)
    settings = {
        int(index): _variant_settings(frame_manifest, int(index))
        for index in np.unique(variant_index)
    }
    true_bias = np.asarray(
        [
            settings[int(index)]["nlos_bias"] * min(int(wall), 3) / 2
            if settings[int(index)]["walls_enabled"]
            else 0.0
            for index, wall in zip(variant_index, wall_count, strict=True)
        ],
        dtype=float,
    )
    noise_std = np.asarray([settings[int(index)]["noise_std"] for index in variant_index])
    return {
        "X": np.asarray(samples["X"]),
        "y": np.asarray(samples["y"], dtype=int),
        "p_v1": np.asarray(samples["p_v1"], dtype=float),
        "p_v2": np.asarray(samples["p_v2"], dtype=float),
        "run_id": np.asarray(samples["run_id"], dtype=int),
        "step": np.asarray(samples["step"], dtype=int),
        "anchor_index": anchors,
        "count": np.asarray(samples["count"], dtype=int),
        "layout_index": layouts,
        "variant_index": variant_index,
        "condition": np.where(
            np.asarray(samples["has_dropout"], dtype=bool), "packet_loss", "no_loss"
        ),
        "n_available": np.asarray(samples["n_available"], dtype=int),
        "n_nlos": np.asarray(samples["n_nlos"], dtype=int),
        "v2_fallback": None,
        "v2_changed": None,
        "gap": _sample_gaps(samples, frames),
        "true_range": true_range,
        "true_bias": true_bias,
        "noise_std": noise_std,
        "wall_count": wall_count,
        "abs_residual": np.abs(np.asarray(samples["X"][:, 4], dtype=float)),
    }


def _metric_mark(threshold: float) -> str:
    if abs(threshold - 0.5) < 1e-12:
        return "threshold_0.5"
    if abs(threshold - POLICY_BAR) < 1e-12:
        return "policy_bar_0.95"
    return ""


def part1_thresholds(final: dict, validation: dict, out: Path) -> dict:
    rows = []
    pr_rows = []
    for source, base in (("final_test_inspected", final), ("validation", validation)):
        for model in ("v1", "v2"):
            y = base["y"]
            probabilities = base[f"p_{model}"]
            for threshold in THRESHOLDS:
                metrics = _classification(y, probabilities, threshold)
                rows.append(
                    {
                        "source": source,
                        "model": model,
                        "inspection": POST_HOC_LABEL
                        if source == "final_test_inspected"
                        else "validation selection data",
                        "threshold": threshold,
                        "mark": _metric_mark(threshold),
                        **{key: value for key, value in metrics.items() if key != "threshold"},
                        "ece": expected_calibration_error(y, probabilities),
                    }
                )
            for point in _pr_points(y, probabilities):
                pr_rows.append(
                    {
                        "source": source,
                        "model": model,
                        "inspection": POST_HOC_LABEL
                        if source == "final_test_inspected"
                        else "validation selection data",
                        **point,
                    }
                )
    write_csv(out / "part1_thresholds.csv", rows)
    write_csv(out / "part1_pr_curve.csv", pr_rows)
    best = {}
    for source, base in (("final_test_inspected", final), ("validation", validation)):
        best[source] = {}
        for model in ("v1", "v2"):
            y = base["y"]
            probabilities = base[f"p_{model}"]
            candidates = [
                _classification(y, probabilities, threshold)
                for threshold in np.round(np.arange(0.01, 0.991, 0.01), 2)
            ]
            selected = max(candidates, key=lambda row: row["f1"])
            best[source][model] = selected
    return {"thresholds": rows, "pr_points": len(pr_rows), "validation_f1_max": best}


def _append_strata(
    rows: list[dict],
    *,
    source: str,
    model: str,
    base: dict,
) -> None:
    y = base["y"]
    probabilities = base[f"p_{model}"]
    n_available = base["n_available"]
    n_nlos = base["n_nlos"]
    groups = np.asarray(
        [
            GROUP_NAMES[int(value)] if 0 <= int(value) < len(GROUP_NAMES) else "unknown"
            for value in _nlos_group(n_nlos, n_available)
        ]
    )
    strata = {
        "available_anchors": np.asarray([str(value) for value in n_available]),
        "nlos_count": np.asarray([str(value) for value in n_nlos]),
        "nlos_group": groups,
        "nlos_fraction": _fraction_label(n_nlos, n_available),
        "packet_loss": np.asarray(base["condition"]),
        "layout": np.asarray([str(value) for value in base["layout_index"]]),
        "variant": np.asarray([str(value) for value in base["variant_index"]]),
    }
    if base["gap"] is not None:
        strata["frames_since_last_observation"] = np.asarray(
            [_gap_label(int(value)) for value in base["gap"]]
        )
    if base["true_bias"] is not None:
        strata["true_bias"] = np.asarray([_bias_label(float(value)) for value in base["true_bias"]])
        strata["noise_sigma"] = np.asarray(
            [_noise_label(float(value)) for value in base["noise_std"]]
        )
        strata["walls_crossed"] = np.asarray([str(value) for value in base["wall_count"]])
        strata["true_range"] = np.asarray(
            [_range_label(float(value)) for value in base["true_range"]]
        )
        strata["abs_residual_norm"] = _residual_label(base["abs_residual"])
    for stratum_type, labels in strata.items():
        rows.extend(
            _stratum_metrics(
                source=source,
                model=model,
                stratum_type=stratum_type,
                labels=labels,
                y=y,
                probabilities=probabilities,
            )
        )


def part2_strata(final: dict, validation: dict, out: Path) -> dict:
    rows = []
    for source, base in (("final_test_inspected", final), ("validation", validation)):
        for model in ("v1", "v2"):
            _append_strata(rows, source=source, model=model, base=base)
    write_csv(out / "part2_strata.csv", rows)
    ranked = sorted(
        [row for row in rows if row["source"] == "validation"],
        key=lambda row: row["missed_nlos"],
        reverse=True,
    )
    write_csv(out / "part2_ranked_missed_nlos.csv", ranked)
    return {
        "rows": len(rows),
        "top_missed_strata": ranked[:15],
        "final_truth_limits": (
            "final test saved classifier probabilities and frame aggregates but not "
            "per-anchor true range, walls crossed, or injected bias; those strata are "
            "validation-only"
        ),
    }


def part3_features(validation: dict, samples: dict, out: Path) -> dict:
    config = json.loads((ROOT / "models" / "ai-reliability-v2" / "config.json").read_text())
    selected_names = list(config["variant"]["features"])
    selected_indices = [FEATURE_NAMES_V2.index(name) for name in selected_names]
    feature_sets = {
        "v1_24": (list(range(24)), list(FEATURE_NAMES_V2[:24])),
        "v2_26": (selected_indices, selected_names),
    }
    X = np.asarray(samples["X"], dtype=float)
    y = validation["y"]
    rows = []
    for model, (indices, names) in feature_sets.items():
        probabilities = validation["p_v1" if model == "v1_24" else "p_v2"]
        caught = (y == 1) & (probabilities >= 0.5)
        missed = (y == 1) & (probabilities < 0.5)
        los = y == 0
        for index, name in zip(indices, names, strict=True):
            values = X[:, index]
            caught_values = values[caught]
            missed_values = values[missed]
            los_values = values[los]
            caught_mean = float(np.mean(caught_values))
            missed_mean = float(np.mean(missed_values))
            los_mean = float(np.mean(los_values))
            caught_var = float(np.var(caught_values))
            missed_var = float(np.var(missed_values))
            los_var = float(np.var(los_values))
            pooled_caught = math.sqrt(max(1e-12, (caught_var + missed_var) / 2))
            pooled_los = math.sqrt(max(1e-12, (los_var + missed_var) / 2))
            rows.append(
                {
                    "model": model,
                    "feature": name,
                    "index": int(index),
                    "caught_n": int(caught.sum()),
                    "missed_n": int(missed.sum()),
                    "true_los_n": int(los.sum()),
                    "caught_mean": caught_mean,
                    "missed_mean": missed_mean,
                    "true_los_mean": los_mean,
                    "caught_median": float(np.median(caught_values)),
                    "missed_median": float(np.median(missed_values)),
                    "true_los_median": float(np.median(los_values)),
                    "caught_std": math.sqrt(caught_var),
                    "missed_std": math.sqrt(missed_var),
                    "true_los_std": math.sqrt(los_var),
                    "smd_missed_vs_caught": (missed_mean - caught_mean) / pooled_caught,
                    "smd_missed_vs_los": (missed_mean - los_mean) / pooled_los,
                }
            )
    rows.sort(key=lambda row: (row["model"], -abs(row["smd_missed_vs_caught"])))
    write_csv(out / "part3_feature_separation.csv", rows)
    top = []
    for model in ("v1_24", "v2_26"):
        selected = sorted(
            [row for row in rows if row["model"] == model],
            key=lambda row: abs(row["smd_missed_vs_caught"]),
            reverse=True,
        )[:5]
        top.extend(selected)
    write_csv(out / "part3_top_separators.csv", top)
    return {"top_separators": top}


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -60, 60)))


def _fit_weighted_mlp(
    X: np.ndarray,
    y: np.ndarray,
    X_validation: np.ndarray,
    y_validation: np.ndarray,
    *,
    hidden: tuple[int, int],
    positive_weight: float,
    seed: int,
    epochs: int = 35,
    batch_size: int = 512,
    learning_rate: float = 1.5e-3,
    l2: float = 1e-4,
) -> tuple[dict, dict]:
    from ..ai_training import _binary_nll, _forward, _init_mlp, _predict_mlp

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
    sample_weight = np.where(np.asarray(y) == 1, float(positive_weight), 1.0)
    beta1, beta2, epsilon = 0.9, 0.999, 1e-8
    step = 0
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
            probabilities = _sigmoid(logits)
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
            gradients = {
                "W1": dW1,
                "b1": db1,
                "W2": dW2,
                "b2": db2,
                "W3": dW3,
                "b3": db3,
            }
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
            best_weights = type(weights)(**{key: value.copy() for key, value in parameters.items()})
            best_epoch = epoch + 1
            stale = 0
        else:
            stale += 1
            if stale >= 6:
                break
    if best_weights is None:
        raise RuntimeError("MLP probe did not produce a finite model")
    return (
        {
            "W1": best_weights.W1,
            "b1": best_weights.b1,
            "W2": best_weights.W2,
            "b2": best_weights.b2,
            "W3": best_weights.W3,
            "b3": best_weights.b3,
        },
        {
            "best_epoch": best_epoch,
            "best_validation_log_loss": best_loss,
            "epochs_completed": len(history),
            "history": history,
        },
    )


def _mlp_probabilities(
    weights: dict, X: np.ndarray, calibration: tuple[float, float]
) -> np.ndarray:
    from ..ai_training import _forward

    logits = _forward(type("Weights", (), weights)(), X)[0]
    slope, intercept = calibration
    return _sigmoid(slope * logits + intercept)


def _training_features(samples: dict) -> tuple[np.ndarray, np.ndarray]:
    config = json.loads((ROOT / "models" / "ai-reliability-v2" / "config.json").read_text())
    names = list(config["variant"]["features"])
    indices = [FEATURE_NAMES_V2.index(name) for name in names]
    return np.asarray(samples["X"][:, indices], dtype=float), np.asarray(names)


def _metric_compact(y: np.ndarray, probabilities: np.ndarray, threshold: float) -> dict:
    metrics = _classification(y, probabilities, threshold)
    metrics["ece"] = expected_calibration_error(y, probabilities)
    metrics["mean_predicted_nlos"] = float(np.mean(probabilities))
    return metrics


def part4_training_health(
    final: dict,
    validation: dict,
    train_samples: dict,
    out: Path,
) -> dict:
    v1 = ReliabilityModel.load(MODEL_V1)
    v2 = ReliabilityV2Model.load(MODEL_V2)
    if not v1.available or not v2.available:
        raise RuntimeError("saved model unavailable")
    X_train = np.asarray(train_samples["X"])
    y_train = np.asarray(train_samples["y"], dtype=int)
    v1_logits = v1._forward((X_train[:, :24] - v1.mean) / v1.scale)
    slope, intercept = v1.calibration
    train_probs = {
        "v1": _sigmoid(slope * v1_logits + intercept),
        "v2": v2._probabilities(X_train),
    }
    sources = {
        "train_in_sample": (y_train, train_probs),
        "validation": (validation["y"], {"v1": validation["p_v1"], "v2": validation["p_v2"]}),
        "final_test_inspected": (final["y"], {"v1": final["p_v1"], "v2": final["p_v2"]}),
    }
    rows = []
    balance = []
    for source, (y, by_model) in sources.items():
        balance.append(
            {
                "source": source,
                "inspection": POST_HOC_LABEL if source == "final_test_inspected" else "",
                "samples": int(len(y)),
                "nlos_samples": int(np.sum(y == 1)),
                "los_samples": int(np.sum(y == 0)),
                "nlos_rate": float(np.mean(y)),
            }
        )
        for model, probabilities in by_model.items():
            for threshold in (0.5, POLICY_BAR):
                rows.append(
                    {
                        "source": source,
                        "model": model,
                        "inspection": POST_HOC_LABEL if source == "final_test_inspected" else "",
                        **_metric_compact(y, probabilities, threshold),
                    }
                )
    write_csv(out / "part4_training_health.csv", rows)
    write_csv(out / "part4_class_balance.csv", balance)
    config = json.loads((MODEL_V2 / "config.json").read_text())
    selected = config["variant"]["features"]
    calibration_mask = grouped_calibration_split(np.asarray(train_samples["run_id"], dtype=int))
    fit_rows = np.flatnonzero(~calibration_mask)
    cal_rows = np.flatnonzero(calibration_mask)
    positives = max(1, int(np.sum(y_train[fit_rows] == 1)))
    negatives = max(1, int(np.sum(y_train[fit_rows] == 0)))
    protocol = {
        "source": "models/ai-reliability-v2/config.json and frozen training code",
        "selected_features": selected,
        "architecture": [len(selected), 32, 16, 1],
        "activation": "relu",
        "loss": "class-weighted binary cross-entropy",
        "class_weight": {"nlos": negatives / positives, "los": 1.0},
        "sample_weights": "1 for LOS and negatives/positives for NLOS; no normalization in MLP",
        "epochs": 35,
        "batch_size": 512,
        "learning_rate": 1.5e-3,
        "l2": 1e-4,
        "optimizer": "Adam",
        "early_stopping": "patience 6 on validation log loss",
        "calibration": "Platt on run-grouped training calibration split",
        "fit_samples": int(len(fit_rows)),
        "calibration_samples": int(len(cal_rows)),
        "fit_positive_rate": float(np.mean(y_train[fit_rows])),
    }
    write_json(out / "part4_training_protocol.json", protocol)
    return {"health": rows, "balance": balance, "protocol": protocol}


def part4_probes(
    train_samples: dict,
    validation: dict,
    out: Path,
    *,
    seeds: tuple[int, ...] = (20261021, 20261022, 20261023, 20261024, 20261025),
    learning_seeds: tuple[int, ...] = (20261021, 20261022, 20261023),
) -> dict:
    from ..ai_training import fit_logistic, fit_platt

    X_train, _ = _training_features(train_samples)
    y_train = np.asarray(train_samples["y"], dtype=int)
    X_validation = np.asarray(validation["X"][:, _selected_indices()], dtype=float)
    y_validation = validation["y"]
    calibration_mask = grouped_calibration_split(np.asarray(train_samples["run_id"], dtype=int))
    fit_rows = np.flatnonzero(~calibration_mask)
    cal_rows = np.flatnonzero(calibration_mask)
    X_fit_raw = X_train[fit_rows]
    X_cal_raw = X_train[cal_rows]
    y_fit = y_train[fit_rows]
    y_cal = y_train[cal_rows]
    mean = np.mean(X_fit_raw, axis=0)
    scale = np.std(X_fit_raw, axis=0)
    scale = np.where(scale < 1e-9, 1.0, scale)
    X_fit = (X_fit_raw - mean) / scale
    X_cal = (X_cal_raw - mean) / scale
    X_val = (X_validation - mean) / scale
    positives = max(1, int(np.sum(y_fit == 1)))
    negatives = max(1, int(np.sum(y_fit == 0)))
    balanced_weight = negatives / positives
    capacity_rows = []
    logistic_weights, logistic_bias = fit_logistic(X_fit, y_fit)
    logistic_probabilities = _sigmoid(X_val @ logistic_weights + logistic_bias)
    capacity_rows.append(
        {
            "probe": "logistic",
            "seed": seeds[0],
            "hidden": "none",
            "positive_weight": balanced_weight,
            **_metric_compact(y_validation, logistic_probabilities, 0.5),
        }
    )
    for seed in seeds:
        print(f"part4 capacity seed {seed}", flush=True)
        for probe, hidden in (("current_32_16", (32, 16)), ("wider_deeper_64_32", (64, 32))):
            weights, training = _fit_weighted_mlp(
                X_fit,
                y_fit,
                X_cal,
                y_cal,
                hidden=hidden,
                positive_weight=balanced_weight,
                seed=seed,
            )
            calibration = fit_platt(_mlp_logits(weights, X_cal), y_cal)[:2]
            probabilities = _mlp_probabilities(weights, X_val, calibration)
            capacity_rows.append(
                {
                    "probe": probe,
                    "seed": seed,
                    "hidden": str(hidden),
                    "positive_weight": balanced_weight,
                    "best_epoch": training["best_epoch"],
                    **_metric_compact(y_validation, probabilities, 0.5),
                }
            )
    write_csv(out / "part4_capacity_probes.csv", capacity_rows)
    class_rows = []
    for positive_weight in (1.0, 2.0, 3.0, 5.0):
        for seed in seeds:
            weights, training = _fit_weighted_mlp(
                X_fit,
                y_fit,
                X_cal,
                y_cal,
                hidden=(32, 16),
                positive_weight=positive_weight,
                seed=seed,
            )
            calibration = fit_platt(_mlp_logits(weights, X_cal), y_cal)[:2]
            probabilities = _mlp_probabilities(weights, X_val, calibration)
            class_rows.append(
                {
                    "positive_weight": positive_weight,
                    "seed": seed,
                    "best_epoch": training["best_epoch"],
                    **_metric_compact(y_validation, probabilities, 0.5),
                }
            )
    write_csv(out / "part4_class_weight_probes.csv", class_rows)
    curve_rows = []
    for fraction in (0.25, 0.5, 1.0):
        for seed in learning_seeds:
            rng = np.random.default_rng(seed + int(fraction * 100))
            count = max(1, int(round(len(X_fit) * fraction)))
            chosen = np.sort(rng.choice(len(X_fit), size=count, replace=False))
            weights, training = _fit_weighted_mlp(
                X_fit[chosen],
                y_fit[chosen],
                X_cal,
                y_cal,
                hidden=(32, 16),
                positive_weight=balanced_weight,
                seed=seed,
            )
            calibration = fit_platt(_mlp_logits(weights, X_cal), y_cal)[:2]
            probabilities = _mlp_probabilities(weights, X_val, calibration)
            metrics = _metric_compact(y_validation, probabilities, 0.5)
            for entry in training["history"]:
                curve_rows.append(
                    {
                        "fraction": fraction,
                        "seed": seed,
                        "samples": count,
                        "epoch": entry["epoch"],
                        "train_loss": entry["train_loss"],
                        "validation_log_loss": entry["validation_log_loss"],
                        "final_validation_recall": metrics["recall"],
                        "final_validation_precision": metrics["precision"],
                        "final_validation_ece": metrics["ece"],
                    }
                )
    write_csv(out / "part4_learning_curves.csv", curve_rows)
    capacity_summary = _probe_summary(capacity_rows, "probe")
    weight_summary = _probe_summary(class_rows, "positive_weight")
    write_csv(out / "part4_capacity_summary.csv", capacity_summary)
    write_csv(out / "part4_class_weight_summary.csv", weight_summary)
    return {
        "capacity": capacity_summary,
        "class_weight": weight_summary,
        "learning_rows": len(curve_rows),
    }


def _selected_indices() -> list[int]:
    config = json.loads((MODEL_V2 / "config.json").read_text())
    return [FEATURE_NAMES_V2.index(name) for name in config["variant"]["features"]]


def _mlp_logits(weights: dict, X: np.ndarray) -> np.ndarray:
    from ..ai_training import _forward

    return _forward(type("Weights", (), weights)(), X)[0]


def _probe_summary(rows: list[dict], key: str) -> list[dict]:
    summary = []
    for value in sorted({row[key] for row in rows}):
        selected = [row for row in rows if row[key] == value]
        summary.append(
            {
                key: value,
                "seeds": len({row["seed"] for row in selected}),
                "recall_mean": float(np.mean([row["recall"] for row in selected])),
                "recall_std": float(np.std([row["recall"] for row in selected])),
                "precision_mean": float(np.mean([row["precision"] for row in selected])),
                "f1_mean": float(np.mean([row["f1"] for row in selected])),
                "ece_mean": float(np.mean([row["ece"] for row in selected])),
            }
        )
    return summary


def _measurements_for_frame(
    frame: dict, environment, index: int, timestamp: float
) -> list[Measurement]:
    measurements = []
    for anchor_index in np.flatnonzero(frame["available"][index]):
        anchor = environment.anchors[int(anchor_index)]
        measurements.append(
            Measurement(
                anchor_id=anchor.id,
                anchor_position=anchor.position,
                timestamp=timestamp,
                observed_range=float(frame["observed"][index, anchor_index]),
            )
        )
    return measurements


def evaluate_probability_matrix(
    nlos_matrix: np.ndarray,
    frames: dict,
    manifest: dict,
    *,
    mode: str,
    threshold: float = POLICY_FLOOR,
    floor: float = POLICY_FLOOR,
    fallback_min: int = 3,
    name: str,
) -> dict:
    from ..schemas import Estimate

    arrays = defaultdict(list)
    run_ids = np.asarray(frames["run_id"], dtype=int)
    for catalog in manifest["run_catalog"]:
        run_id = int(catalog["run_id"])
        indices = np.flatnonzero(run_ids == run_id)
        environment = resolve_floor(
            load_environments()[catalog["environment_id"]], catalog["floor_id"]
        )
        tracker = MotionTracker()
        for index in indices:
            timestamp = round(int(frames["step"][index]) * DT, 8)
            available = np.flatnonzero(frames["available"][index])
            robust = np.asarray(frames["robust"][index], dtype=float)
            estimate = (
                Estimate(
                    position=(float(robust[0]), float(robust[1])),
                    status="valid",
                    reason="Cached robust fix",
                    covariance=np.asarray(frames["robust_cov"][index], dtype=float).tolist()
                    if np.isfinite(frames["robust_cov"][index]).all()
                    else None,
                )
                if np.isfinite(robust).all()
                else Estimate(status="insufficient", reason="No robust fix")
            )
            fallback = True
            changed = False
            predicted_los = 0
            if len(available) >= 3:
                probabilities = nlos_matrix[index, available]
                if np.isfinite(probabilities).all():
                    p_los = 1.0 - probabilities
                    predicted_los = int(np.sum(p_los >= 0.5))
                    if predicted_los >= fallback_min:
                        if mode == "hard":
                            weights = np.where(
                                p_los >= threshold,
                                1.0,
                                floor + (1.0 - floor) * (p_los / max(threshold, 1e-12)),
                            )
                        else:
                            weights = np.where(p_los < 0.5, floor, 1.0)
                        weights = np.clip(weights, 0.0, 1.0)
                        changed = bool(np.any(weights < 1.0 - 1e-12))
                        fallback = False
                        if changed:
                            measurements = _measurements_for_frame(
                                frames, environment, index, timestamp
                            )
                            anchor_weights = {
                                environment.anchors[int(anchor)].id: float(weight)
                                for anchor, weight in zip(available, weights, strict=True)
                            }
                            estimate = solve(measurements, robust=True, ai_weights=anchor_weights)
            tracking = tracker.update(estimate, timestamp)
            truth = np.asarray(frames["truth"][index], dtype=float)
            stat_position = np.asarray(frames["stat_tracking"][index], dtype=float)
            arrays["run_id"].append(run_id)
            arrays["step"].append(int(frames["step"][index]))
            arrays["stat_error"].append(
                float(np.linalg.norm(stat_position - truth))
                if np.isfinite(stat_position).all()
                else np.nan
            )
            arrays["ai_error"].append(
                float(np.linalg.norm(np.asarray(tracking.position) - truth))
                if tracking.position is not None
                else np.nan
            )
            arrays["fallback"].append(int(fallback))
            arrays["changed"].append(int(changed))
            arrays["predicted_los"].append(predicted_los)
            arrays["available"].append(int(len(available)))
            arrays["n_nlos"].append(int(frames["n_nlos"][index]))
            arrays["has_dropout"].append(int(frames["has_dropout"][index]))
            arrays["count"].append(int(frames["count"][index]))
    result = {key: np.asarray(values) for key, values in arrays.items()}
    result["name"] = name
    result["stat_rmse"] = _rmse(result["stat_error"])
    result["rmse"] = _rmse(result["ai_error"])
    return result


def _condition_row(result: dict, *, condition: str, scope: str, name: str) -> dict:
    bootstrap = _bootstrap_difference(result["stat_error"], result["ai_error"], result["run_id"])
    return {
        "condition": condition,
        "scope": scope,
        "name": name,
        "frames": int(np.sum(np.isfinite(result["stat_error"]) & np.isfinite(result["ai_error"]))),
        "stat_rmse": bootstrap["stat_rmse"],
        "method_rmse": bootstrap["method_rmse"],
        "difference": bootstrap["difference"],
        "relative_percent": bootstrap["relative_percent"],
        "ci_low": bootstrap["ci_low"],
        "ci_high": bootstrap["ci_high"],
        "relative_ci_low": bootstrap["relative_ci_low"],
        "relative_ci_high": bootstrap["relative_ci_high"],
        "fallback_rate": _rate(result["fallback"].astype(bool)),
        "changed_rate": _rate(result["changed"].astype(bool)),
    }


def _mask_result(result: dict, mask: np.ndarray) -> dict:
    subset = {}
    for key, value in result.items():
        if isinstance(value, np.ndarray) and len(value) == len(mask):
            subset[key] = value[mask]
    subset["name"] = result["name"]
    subset["stat_rmse"] = _rmse(subset["stat_error"])
    subset["rmse"] = _rmse(subset["ai_error"])
    return subset


def _degraded_matrix(
    nlos_matrix: np.ndarray,
    recall: float,
    *,
    false_alarm_rate: float = 0.05,
    seed: int = 20261027,
) -> np.ndarray:
    rng = np.random.default_rng(seed + int(recall * 1000))
    positive = nlos_matrix >= 0.5
    negative = nlos_matrix < 0.5
    detect = positive & (rng.random(nlos_matrix.shape) < recall)
    alarm = negative & (rng.random(nlos_matrix.shape) < false_alarm_rate)
    degraded = np.full(nlos_matrix.shape, np.nan, dtype=float)
    available = np.isfinite(nlos_matrix)
    degraded[available] = 0.0
    degraded[detect] = 1.0
    degraded[alarm] = 1.0
    return degraded


def part5_oracle(validation: dict, frames: dict, manifest: dict, out: Path) -> dict:
    v2_matrix = _probability_matrix(
        {key: validation[key] for key in ("run_id", "step", "anchor_index")},
        validation["p_v2"],
        frames,
    )
    available = np.asarray(frames["available"], dtype=bool)
    nlos_mask = np.asarray(frames["nlos_mask"], dtype=bool)
    oracle = np.full((len(available), N_ANCHORS), np.nan, dtype=float)
    oracle[available] = np.where(nlos_mask[available], 1.0, 0.0)
    conditions = {
        "v2_aggressive_p05": (v2_matrix, "binary", 0.5),
        "oracle_true_labels": (oracle, "binary", 0.5),
        "degraded_recall_0.50": (
            _degraded_matrix(oracle, 0.50),
            "binary",
            0.5,
        ),
        "degraded_recall_0.67": (
            _degraded_matrix(oracle, 0.67),
            "binary",
            0.5,
        ),
        "degraded_recall_0.80": (
            _degraded_matrix(oracle, 0.80),
            "binary",
            0.5,
        ),
        "degraded_recall_0.90": (
            _degraded_matrix(oracle, 0.90),
            "binary",
            0.5,
        ),
    }
    results = {}
    overall = []
    by_count = []
    focus = []
    for name, (matrix, mode, threshold) in conditions.items():
        print(f"part5 {name}", flush=True)
        result = evaluate_probability_matrix(
            matrix,
            frames,
            manifest,
            mode=mode,
            threshold=threshold,
            floor=POLICY_FLOOR,
            fallback_min=3,
            name=name,
        )
        results[name] = result
        overall.append(_condition_row(result, condition=name, scope="all", name="all"))
        for count in COUNTS:
            mask = result["count"] == count
            if np.any(mask):
                by_count.append(
                    _condition_row(
                        _mask_result(result, mask),
                        condition=name,
                        scope="anchor_count",
                        name=str(count),
                    )
                )
        for label, mask in (
            (
                "majority_nlos",
                (result["available"] >= 3) & (result["n_nlos"] > result["available"] / 2),
            ),
            ("all_nlos", (result["available"] >= 3) & (result["n_nlos"] == result["available"])),
            (
                "solvable_mixed_nlos",
                (result["available"] >= 3)
                & (result["n_nlos"] > 0)
                & (result["n_nlos"] < result["available"]),
            ),
        ):
            if np.any(mask):
                focus.append(
                    _condition_row(
                        _mask_result(result, mask),
                        condition=name,
                        scope="focus",
                        name=label,
                    )
                )
    write_csv(out / "part5_overall.csv", overall)
    write_csv(out / "part5_by_count.csv", by_count)
    write_csv(out / "part5_focus.csv", focus)
    write_csv(
        out / "part5_bootstrap.csv",
        [row for row in overall if row["scope"] == "all"],
    )
    oracle_row = next(row for row in overall if row["condition"] == "oracle_true_labels")
    return {
        "overall": overall,
        "oracle_max_gain_rmse": oracle_row["difference"],
        "oracle_max_gain_relative_percent": oracle_row["relative_percent"],
        "oracle_ci": [oracle_row["ci_low"], oracle_row["ci_high"]],
        "degraded": [row for row in overall if row["condition"].startswith("degraded")],
    }


def part6_final_fallback(final: dict, samples: dict, frames: dict, out: Path) -> dict:
    indices = _frame_positions(samples, frames)
    n_frames = len(frames["run_id"])
    predicted_los = np.zeros(n_frames, dtype=float)
    seen = np.zeros(n_frames, dtype=bool)
    p_v2 = np.asarray(final["p_v2"], dtype=float)
    np.add.at(predicted_los, indices, (p_v2 < 0.5).astype(float))
    seen[indices] = True
    predicted_los[~seen] = np.nan
    available = np.asarray(frames["n_available"], dtype=int)
    fallback = np.asarray(frames["v2_fallback"], dtype=bool)
    changed = np.asarray(frames["v2_changed"], dtype=bool)
    split = np.full(n_frames, "not_fallback", dtype=object)
    split[fallback & (available < 3)] = "A_fewer_than_3_available"
    split[fallback & (available >= 3) & np.isfinite(predicted_los) & (predicted_los < 3)] = (
        "B_predicted_los_under_3"
    )
    split[fallback & (split == "not_fallback")] = "fallback_other"
    frame_error = np.asarray(frames["v2_error"], dtype=float)
    valid = np.asarray(frames["v2_valid"], dtype=bool)
    rows = []
    for label in (
        "A_fewer_than_3_available",
        "B_predicted_los_under_3",
        "fallback_other",
        "not_fallback",
    ):
        mask = split == label
        if not np.any(mask):
            continue
        rows.append(
            {
                "split": label,
                "frames": int(mask.sum()),
                "share_of_frames": float(np.mean(mask)),
                "rmse": _rmse(frame_error[mask & valid]),
                "valid_rate": float(np.mean(valid[mask])),
                "changed_rate": float(np.mean(changed[mask])),
                "nlos_readings": int(np.sum((np.asarray(final["y"]) == 1) & mask[indices])),
            }
        )
    write_csv(out / "part6_fallback_split.csv", rows)
    by_count = []
    condition = np.asarray(final["condition"]).astype(str)
    for count in COUNTS:
        for condition_name in ("no_loss", "packet_loss"):
            mask = (np.asarray(final["count"]) == count) & (condition == condition_name)
            for label in ("A_fewer_than_3_available", "B_predicted_los_under_3"):
                selected = mask & (split[indices] == label)
                if not np.any(selected):
                    continue
                by_count.append(
                    {
                        "count": count,
                        "condition": condition_name,
                        "split": label,
                        "samples": int(selected.sum()),
                        "nlos_samples": int(np.sum(final["y"][selected] == 1)),
                        "share_of_stratum": float(np.mean(split[indices][mask] == label)),
                        "nlos_readings_with_reduced_weight": int(
                            np.sum((final["y"][selected] == 1) & (p_v2[selected] >= POLICY_BAR))
                        ),
                        "los_readings_with_reduced_weight": int(
                            np.sum((final["y"][selected] == 0) & (p_v2[selected] >= POLICY_BAR))
                        ),
                    }
                )
    write_csv(out / "part6_fallback_by_count.csv", by_count)
    y = np.asarray(final["y"], dtype=int)
    reduced = p_v2 >= POLICY_BAR
    reading_rows = []
    for label in ("A_fewer_than_3_available", "B_predicted_los_under_3", "not_fallback"):
        mask = split[indices] == label
        if not np.any(mask):
            continue
        reading_rows.append(
            {
                "split": label,
                "nlos_readings": int(np.sum(mask & (y == 1))),
                "nlos_with_reduced_weight": int(np.sum(mask & (y == 1) & reduced)),
                "nlos_reduced_share": float(np.mean(reduced[mask & (y == 1)]))
                if np.any(mask & (y == 1))
                else float("nan"),
                "los_readings": int(np.sum(mask & (y == 0))),
                "los_with_reduced_weight": int(np.sum(mask & (y == 0) & reduced)),
                "los_reduced_share": float(np.mean(reduced[mask & (y == 0)]))
                if np.any(mask & (y == 0))
                else float("nan"),
            }
        )
    write_csv(out / "part6_reduced_weights.csv", reading_rows)
    return {"split": rows, "reduced": reading_rows}


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256(path.read_bytes())
    return digest.hexdigest()


def verify_seed_manifest() -> dict:
    manifest = json.loads(SEED_MANIFEST.read_text(encoding="utf-8"))
    fresh = {int(seed) for seed in manifest["fresh_seeds"]}
    excluded = {int(seed) for seed in manifest["excluded_used_seeds"]}
    if len(fresh) < 8:
        raise ValueError("recall-fix seed manifest needs at least 8 seeds")
    if fresh & excluded:
        raise ValueError("fresh recall-fix seeds overlap excluded seeds")
    if fresh & FINAL_TEST_SEEDS:
        raise ValueError("fresh recall-fix seeds overlap final-test seeds")
    for path in sorted((ROOT / "configs" / "ai").glob("*.json")):
        if path == SEED_MANIFEST:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))

        def seed_values(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if "seed" in key.lower() and isinstance(item, list):
                        yield from [
                            int(candidate)
                            for candidate in item
                            if isinstance(candidate, (int, float))
                        ]
                    else:
                        yield from seed_values(item)
            elif isinstance(value, list):
                for item in value:
                    yield from seed_values(item)

        used = set(seed_values(payload))
        overlap = fresh & used
        if overlap:
            raise ValueError(f"fresh recall-fix seeds overlap {path.name}: {sorted(overlap)}")
    final_samples, _ = load_final_data()
    validation_samples, _, _, _ = load_validation_data()
    for name, samples in (
        ("final_test", final_samples),
        ("validation", validation_samples),
    ):
        if "seed" in samples:
            overlap = fresh & set(np.unique(np.asarray(samples["seed"])).astype(int))
            if overlap:
                raise ValueError(f"fresh recall-fix seeds already in {name}: {sorted(overlap)}")
    return {
        "manifest": str(SEED_MANIFEST.relative_to(ROOT)),
        "sha256": _sha256(SEED_MANIFEST),
        "fresh_seeds": sorted(fresh),
        "excluded_seed_count": len(excluded),
        "status": "reserved and not evaluated",
    }


def _frozen_hashes() -> dict:
    files = [
        "models/ai-reliability-v1/config.json",
        "models/ai-reliability-v1/weights.npz",
        "models/ai-reliability-v1/metrics.json",
        "models/ai-reliability-v2/config.json",
        "models/ai-reliability-v2/policy-v2.json",
        "models/ai-reliability-v2/promotion.json",
    ]
    files.extend(str(path.relative_to(ROOT)) for path in sorted((MODEL_V2).glob("seed_*.npz")))
    return {name: _sha256(ROOT / name) for name in files}


def run(output: Path = OUTPUT, *, skip_probes: bool = False) -> dict:
    started = perf_counter()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True, exist_ok=True)
    seed_check = verify_seed_manifest()
    final_samples, final_frames = load_final_data()
    validation_samples, validation_frames, sample_manifest, frame_manifest = load_validation_data()
    train_samples, train_manifest = load_samples("train")
    final_base = _final_base(final_samples, final_frames)
    validation_base = _validation_base(validation_samples, validation_frames, frame_manifest)
    part1 = part1_thresholds(final_base, validation_base, output)
    part2 = part2_strata(final_base, validation_base, output)
    part3 = part3_features(validation_base, validation_samples, output)
    part4_health = part4_training_health(final_base, validation_base, train_samples, output)
    probe_result = (
        {"skipped": True} if skip_probes else part4_probes(train_samples, validation_base, output)
    )
    part5 = part5_oracle(validation_base, validation_frames, frame_manifest, output)
    part6 = part6_final_fallback(final_base, final_samples, final_frames, output)
    scope = {
        "status": "complete",
        "inspection": POST_HOC_LABEL,
        "seed_manifest": seed_check,
        "sources": {
            "final_test": {
                "path": "artifacts/ai-reliability/final-test",
                "classifier_samples": int(len(final_base["y"])),
                "frames": int(len(final_frames["run_id"])),
                "status": POST_HOC_LABEL,
                "missing": [
                    "per-anchor true range",
                    "per-anchor walls crossed",
                    "per-anchor injected bias",
                    "per-anchor frames since last observation",
                ],
            },
            "validation": {
                "path": "artifacts/ai-reliability/phase3-v2/dataset/validation",
                "classifier_samples": int(len(validation_base["y"])),
                "frames": int(len(validation_frames["run_id"])),
                "status": "validation selection data, already inspected",
                "missing": [],
            },
            "train": {
                "path": "artifacts/ai-reliability/phase3-v2/dataset/train",
                "classifier_samples": int(len(train_samples["y"])),
                "runs": int(train_manifest["runs"]),
            },
        },
        "reuse": {
            "regenerated_simulations": [],
            "note": (
                "All parts reuse saved caches. Missing final-test per-anchor truth is "
                "reported, not regenerated."
            ),
        },
        "frozen_hashes": _frozen_hashes(),
        "elapsed_seconds": perf_counter() - started,
    }
    write_json(output / "part0_scope.json", scope)
    summary = {
        "status": "complete",
        "inspection": POST_HOC_LABEL,
        "seed_manifest": seed_check,
        "part1": part1,
        "part2": part2,
        "part3": part3,
        "part4": {"health": part4_health, "probes": probe_result},
        "part5": part5,
        "part6": part6,
        "elapsed_seconds": perf_counter() - started,
        "unmeasured": [
            "final-test per-anchor true range, walls, and injected bias were not saved",
            "no new seeds were evaluated",
            "no model, policy, estimator, tracker, simulator, or schema was changed",
        ],
    }
    write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--skip-probes", action="store_true")
    args = parser.parse_args()
    result = run(args.output, skip_probes=args.skip_probes)
    print(json.dumps({"status": result["status"], "elapsed_seconds": result["elapsed_seconds"]}))


if __name__ == "__main__":
    main()
