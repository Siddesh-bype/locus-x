"""Phase 3 v2 feature experiments.

This module generates causal v2 datasets, trains the preregistered feature
ablations, and evaluates the unchanged v1 policy. It never reads final-test
seeds and it does not modify the v1 model, schema, policy, estimator, tracker or
simulator.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import defaultdict
from functools import partial
from pathlib import Path
from time import perf_counter

import numpy as np

from .ai_ablation import (
    BOOTSTRAP_SEED,
    grouped_calibration_split,
    metric_row,
    reliability_bins,
)
from .ai_evaluation import base_variants, mixed_variants
from .ai_training import _forward, _standardize, fit_mlp, fit_platt
from .environment import load_environments, resolve_floor, route_position
from .estimators import solve, valid_observations
from .reliability import FEATURE_NAMES, ReliabilityModel
from .reliability_v2 import (
    FEATURE_NAMES_V2,
    FEATURE_VERSION_V2,
    G0_MAPFREE_NAMES,
    G1_MISSINGNESS,
    G2_RANGE,
    G3_CONSISTENCY,
    G4_TRACKER,
    G5_RECEPTION,
    MODEL_VERSION_V2,
    FeatureHistoryV2,
    ReliabilityV2Model,
    extract_features_v2,
)
from .schemas import AIPrediction, Estimate, Measurement, Settings
from .simulation import generate_measurements
from .tracking import MotionTracker

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "ai" / "reliability-dataset-v2.json"
OUTPUT = ROOT / "artifacts" / "ai-reliability" / "phase3-v2"
DATASET = OUTPUT / "dataset"
MODEL_OUTPUT = OUTPUT / "models"
PARTIAL = OUTPUT / "end-to-end" / "partials"
CLASSIFIER_PARTIAL = OUTPUT / "classifier" / "partials"
FINAL_MODEL = ROOT / "models" / MODEL_VERSION_V2
V1_MODEL = ROOT / "models" / "ai-reliability-v1"

DT = 0.5
POLICY_FLOOR = 0.05
POLICY_THRESHOLD = 0.05
TRAINING_SEEDS = (20261021, 20261022, 20261023, 20261024, 20261025)
BOOTSTRAP = 2000
N_ANCHORS = 8
COUNTS = (3, 4, 5, 6, 7, 8)
FOCUS_FAULTS = ("nlos", "nlos_packet", "nlos_packet_outage", "all_four")
NLOS_GROUP_NAMES = ("one", "minority", "half", "majority", "all")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def feature_count(names: list[str]) -> int:
    return len(names)


def ablation_variants() -> list[dict]:
    groups = {
        "G2": G2_RANGE,
        "G3": G3_CONSISTENCY,
        "G4": G4_TRACKER,
        "G5": G5_RECEPTION,
        "G1": G1_MISSINGNESS,
    }
    all_new = [*G2_RANGE, *G3_CONSISTENCY, *G4_TRACKER, *G5_RECEPTION, *G1_MISSINGNESS]
    base = list(G0_MAPFREE_NAMES)
    variants = [
        {"name": "g0_full", "names": list(FEATURE_NAMES), "family": "control"},
        {"name": "g0_mapfree", "names": base, "family": "control"},
    ]
    for group in ("G2", "G3", "G4", "G5", "G1"):
        variants.append(
            {
                "name": group.lower(),
                "names": [*base, *groups[group]],
                "family": "mapfree",
                "group": group,
            }
        )
    variants.extend(
        [
            {
                "name": "g1_g5",
                "names": [*base, *G1_MISSINGNESS, *G5_RECEPTION],
                "family": "mapfree",
                "group": "minimum",
            },
            {
                "name": "all_mapfree",
                "names": [*base, *all_new],
                "family": "mapfree",
                "group": "combined",
            },
            {
                "name": "all_map",
                "names": [*FEATURE_NAMES, *all_new],
                "family": "comparison",
                "group": "combined",
            },
        ]
    )
    for variant in variants:
        variant["indices"] = [FEATURE_NAMES_V2.index(name) for name in variant["names"]]
        variant["n_features"] = len(variant["indices"])
    return variants


def _layouts(split_config: dict) -> list[tuple[str, str, str | None]]:
    return [
        (layout["name"], layout["environment_id"], layout["floor_id"])
        for layout in split_config["layouts"]
    ]


def _variants(split_config: dict) -> list[dict]:
    if split_config["variants"] in (
        "base_variants+mixed_variants",
        "base_variants+mixed_variants+anchor_failure",
    ):
        variants = [*base_variants(), *mixed_variants()]
        if split_config["variants"].endswith("+anchor_failure"):
            variants.append(
                {"id": "anchor_failure", "scenario": "anchor_failure", "overrides": {}}
            )
        return variants
    return list(split_config["variants"])


def iter_run_specs(config: dict, split: str):
    split_config = config["splits"][split]
    layouts = _layouts(split_config)
    variants = _variants(split_config)
    subset_mode = split_config.get("subsets", "all")
    for layout_name, environment_id, floor_id in layouts:
        for route in split_config["routes"]:
            for seed in split_config["seeds"]:
                for variant in variants:
                    for count in split_config["anchor_counts"]:
                        subsets = config["anchor_subsets"][str(count)]
                        if subset_mode == "first_per_count":
                            subsets = subsets[:1]
                        for subset in subsets:
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


def _settings(variant: dict, seed: int, route: str, disabled: list[str]) -> Settings:
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
    values = {"scenario": variant["scenario"], **variant.get("overrides", {})}
    return Settings(seed=seed, route=route, disabled_anchors=disabled, **values)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_jsonable) + "\n", encoding="utf-8")


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


def _memmap(path: Path, dtype, shape):
    path.parent.mkdir(parents=True, exist_ok=True)
    return np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=shape)


def _nlos_group(n_nlos: np.ndarray, n_available: np.ndarray) -> np.ndarray:
    group = np.full(len(n_nlos), -1, dtype=np.int8)
    one = n_nlos == 1
    all_nlos = n_nlos == n_available
    half = (n_available % 2 == 0) & (n_nlos == n_available // 2)
    minority = (~one) & (~all_nlos) & (~half) & (n_nlos < n_available / 2)
    majority = (~one) & (~all_nlos) & (~half) & (n_nlos > n_available / 2)
    group[one] = 0
    group[minority] = 1
    group[half] = 2
    group[majority] = 3
    group[all_nlos] = 4
    return group


def generate_split(split: str, config: dict | None = None) -> dict:
    config = config or load_config()
    split_config = config["splits"][split]
    steps = int(split_config["steps"])
    specs = list(iter_run_specs(config, split))
    total_frames = len(specs) * steps
    sample_capacity = total_frames * N_ANCHORS
    split_dir = DATASET / split
    frame_dir = split_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    started = perf_counter()

    X = _memmap(split_dir / "X.npy", np.float32, (sample_capacity, len(FEATURE_NAMES_V2)))
    y = _memmap(split_dir / "y.npy", np.int8, (sample_capacity,))
    sample_run = _memmap(split_dir / "run-id.npy", np.int32, (sample_capacity,))
    sample_step = _memmap(split_dir / "step.npy", np.int16, (sample_capacity,))
    sample_anchor = _memmap(split_dir / "anchor-index.npy", np.int8, (sample_capacity,))
    sample_layout = _memmap(split_dir / "layout-index.npy", np.int8, (sample_capacity,))
    sample_variant = _memmap(split_dir / "variant-index.npy", np.int16, (sample_capacity,))
    sample_count = _memmap(split_dir / "count.npy", np.int8, (sample_capacity,))
    sample_seed = _memmap(split_dir / "seed.npy", np.int32, (sample_capacity,))
    sample_n_nlos = _memmap(split_dir / "n-nlos.npy", np.int8, (sample_capacity,))
    sample_n_available = _memmap(split_dir / "n-available.npy", np.int8, (sample_capacity,))
    sample_dropout = _memmap(split_dir / "has-dropout.npy", np.bool_, (sample_capacity,))
    sample_outage = _memmap(split_dir / "has-outage.npy", np.bool_, (sample_capacity,))

    frame_features = _memmap(
        frame_dir / "features.npy", np.float32, (total_frames, N_ANCHORS, len(FEATURE_NAMES_V2))
    )
    frame_features[:] = np.nan
    frame_observed = _memmap(frame_dir / "observed.npy", np.float32, (total_frames, N_ANCHORS))
    frame_observed[:] = np.nan
    frame_available = _memmap(frame_dir / "available.npy", np.bool_, (total_frames, N_ANCHORS))
    frame_nlos = _memmap(frame_dir / "nlos-mask.npy", np.bool_, (total_frames, N_ANCHORS))
    frame_truth = _memmap(frame_dir / "truth.npy", np.float32, (total_frames, 2))
    frame_baseline = _memmap(frame_dir / "baseline.npy", np.float32, (total_frames, 2))
    frame_robust = _memmap(frame_dir / "robust.npy", np.float32, (total_frames, 2))
    frame_robust_cov = _memmap(frame_dir / "robust-cov.npy", np.float32, (total_frames, 2, 2))
    frame_stat = _memmap(frame_dir / "stat-tracking.npy", np.float32, (total_frames, 2))
    frame_n_nlos = _memmap(frame_dir / "n-nlos.npy", np.int8, (total_frames,))
    frame_n_available = _memmap(frame_dir / "n-available.npy", np.int8, (total_frames,))
    frame_dropout = _memmap(frame_dir / "has-dropout.npy", np.bool_, (total_frames,))
    frame_outage = _memmap(frame_dir / "has-outage.npy", np.bool_, (total_frames,))
    frame_run = _memmap(frame_dir / "run-id.npy", np.int32, (total_frames,))
    frame_step = _memmap(frame_dir / "step.npy", np.int16, (total_frames,))
    frame_layout = _memmap(frame_dir / "layout-index.npy", np.int8, (total_frames,))
    frame_variant = _memmap(frame_dir / "variant-index.npy", np.int16, (total_frames,))
    frame_count = _memmap(frame_dir / "count.npy", np.int8, (total_frames,))
    frame_seed = _memmap(frame_dir / "seed.npy", np.int32, (total_frames,))
    frame_truth[:] = np.nan
    frame_baseline[:] = np.nan
    frame_robust[:] = np.nan
    frame_robust_cov[:] = np.nan
    frame_stat[:] = np.nan

    layouts = _layouts(split_config)
    layout_names = [layout[0] for layout in layouts]
    variants = _variants(split_config)
    variant_names = [variant["id"] for variant in variants]
    scenario_names = [variant["scenario"] for variant in variants]
    catalog = []
    sample_position = 0
    frame_position = 0
    for run_id, spec in enumerate(specs):
        environment = resolve_floor(
            load_environments()[spec["environment_id"]], spec["floor_id"]
        )
        if len(environment.anchors) != N_ANCHORS:
            raise RuntimeError("Phase 3 frame cache assumes eight anchors")
        anchor_index = {anchor.id: index for index, anchor in enumerate(environment.anchors)}
        disabled = [
            anchor.id for anchor in environment.anchors if anchor.id not in spec["subset"]
        ]
        settings = _settings(spec["variant"], spec["seed"], spec["route"], disabled)
        history = FeatureHistoryV2()
        tracker = MotionTracker()
        layout_index = layout_names.index(spec["layout"])
        variant_index = variant_names.index(spec["variant"]["id"])
        run_samples = 0
        run_nlos = 0
        for step in range(steps):
            timestamp = round(step * DT, 8)
            truth = route_position(
                environment.routes[settings.route], timestamp, settings.speed
            )
            measurements, fault_labels = generate_measurements(
                environment, settings, truth, timestamp, step
            )
            baseline = solve(measurements)
            robust = solve(measurements, robust=True)
            previous_state = None if tracker.state is None else tracker.state.copy()
            previous_time = tracker.timestamp
            tracking = tracker.update(robust, timestamp)
            batch = extract_features_v2(
                measurements,
                environment,
                baseline,
                history,
                previous_state,
                previous_time,
            )
            history.tracker_state = None if tracker.state is None else tracker.state.copy()
            history.tracker_timestamp = tracker.timestamp
            label_by_anchor = {
                label.anchor_id: label
                for label in fault_labels
                if label.available and label.kind in ("los", "nlos")
            }
            available_mask = np.zeros(N_ANCHORS, dtype=bool)
            nlos_mask = np.zeros(N_ANCHORS, dtype=bool)
            for measurement in valid_observations(measurements):
                index = anchor_index[measurement.anchor_id]
                available_mask[index] = True
                frame_observed[frame_position, index] = measurement.observed_range
            for anchor_id, label in label_by_anchor.items():
                nlos_mask[anchor_index[anchor_id]] = label.kind == "nlos"
            has_dropout = any(label.kind == "packet_loss" for label in fault_labels)
            has_outage = any(label.kind == "outage" for label in fault_labels)
            n_nlos = int(np.sum(nlos_mask & available_mask))
            n_available = int(np.sum(available_mask))
            frame_available[frame_position] = available_mask
            frame_nlos[frame_position] = nlos_mask
            frame_truth[frame_position] = truth
            frame_n_nlos[frame_position] = n_nlos
            frame_n_available[frame_position] = n_available
            frame_dropout[frame_position] = has_dropout
            frame_outage[frame_position] = has_outage
            frame_run[frame_position] = run_id
            frame_step[frame_position] = step
            frame_layout[frame_position] = layout_index
            frame_variant[frame_position] = variant_index
            frame_count[frame_position] = spec["count"]
            frame_seed[frame_position] = spec["seed"]
            if baseline.position is not None:
                frame_baseline[frame_position] = baseline.position
            if robust.position is not None:
                frame_robust[frame_position] = robust.position
            if robust.covariance is not None:
                frame_robust_cov[frame_position] = robust.covariance
            if tracking.position is not None:
                frame_stat[frame_position] = tracking.position
            if batch is not None:
                for anchor_id, row in zip(batch.anchors, batch.matrix, strict=True):
                    index = anchor_index[anchor_id]
                    frame_features[frame_position, index] = row
                    label = label_by_anchor.get(anchor_id)
                    if label is None:
                        continue
                    X[sample_position] = row
                    y[sample_position] = 1 if label.kind == "nlos" else 0
                    sample_run[sample_position] = run_id
                    sample_step[sample_position] = step
                    sample_anchor[sample_position] = index
                    sample_layout[sample_position] = layout_index
                    sample_variant[sample_position] = variant_index
                    sample_count[sample_position] = spec["count"]
                    sample_seed[sample_position] = spec["seed"]
                    sample_n_nlos[sample_position] = n_nlos
                    sample_n_available[sample_position] = n_available
                    sample_dropout[sample_position] = has_dropout
                    sample_outage[sample_position] = has_outage
                    sample_position += 1
                    run_samples += 1
                    run_nlos += int(y[sample_position - 1])
            frame_position += 1
        catalog.append(
            {
                "run_id": run_id,
                "layout": spec["layout"],
                "layout_index": layout_index,
                "environment_id": spec["environment_id"],
                "floor_id": spec["floor_id"],
                "route": spec["route"],
                "seed": spec["seed"],
                "variant": spec["variant"]["id"],
                "variant_index": variant_index,
                "scenario": spec["variant"]["scenario"],
                "count": spec["count"],
                "subset": spec["subset"],
                "samples": run_samples,
                "nlos_samples": run_nlos,
            }
        )
    for array in (
        X,
        y,
        sample_run,
        sample_step,
        sample_anchor,
        sample_layout,
        sample_variant,
        sample_count,
        sample_seed,
        sample_n_nlos,
        sample_n_available,
        sample_dropout,
        sample_outage,
        frame_features,
        frame_observed,
        frame_available,
        frame_nlos,
        frame_truth,
        frame_baseline,
        frame_robust,
        frame_robust_cov,
        frame_stat,
        frame_n_nlos,
        frame_n_available,
        frame_dropout,
        frame_outage,
        frame_run,
        frame_step,
        frame_layout,
        frame_variant,
        frame_count,
        frame_seed,
    ):
        array.flush()
    sample_arrays = {
        "X": "X.npy",
        "y": "y.npy",
        "run_id": "run-id.npy",
        "step": "step.npy",
        "anchor_index": "anchor-index.npy",
        "layout_index": "layout-index.npy",
        "variant_index": "variant-index.npy",
        "count": "count.npy",
        "seed": "seed.npy",
        "n_nlos": "n-nlos.npy",
        "n_available": "n-available.npy",
        "has_dropout": "has-dropout.npy",
        "has_outage": "has-outage.npy",
    }
    frame_arrays = {
        "features": "features.npy",
        "observed": "observed.npy",
        "available": "available.npy",
        "nlos_mask": "nlos-mask.npy",
        "truth": "truth.npy",
        "baseline": "baseline.npy",
        "robust": "robust.npy",
        "robust_cov": "robust-cov.npy",
        "stat_tracking": "stat-tracking.npy",
        "n_nlos": "n-nlos.npy",
        "n_available": "n-available.npy",
        "has_dropout": "has-dropout.npy",
        "has_outage": "has-outage.npy",
        "run_id": "run-id.npy",
        "step": "step.npy",
        "layout_index": "layout-index.npy",
        "variant_index": "variant-index.npy",
        "count": "count.npy",
        "seed": "seed.npy",
    }
    manifest = {
        "split": split,
        "dataset_version": config["dataset_version"],
        "feature_version": FEATURE_VERSION_V2,
        "feature_names": FEATURE_NAMES_V2,
        "config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "dt": DT,
        "steps": steps,
        "samples": sample_position,
        "frames": frame_position,
        "runs": len(catalog),
        "los_samples": int(np.sum(y[:sample_position] == 0)),
        "nlos_samples": int(np.sum(y[:sample_position] == 1)),
        "layouts": layout_names,
        "variants": variant_names,
        "scenarios": scenario_names,
        "seeds": list(split_config["seeds"]),
        "anchor_counts": list(split_config["anchor_counts"]),
        "run_catalog": catalog,
        "sample_arrays": sample_arrays,
        "frame_arrays": frame_arrays,
        "elapsed_seconds": perf_counter() - started,
    }
    _write_json(split_dir / "manifest.json", manifest)
    _write_json(split_dir / "sample-index.json", {"samples": sample_position})
    _write_json(split_dir / "frame-index.json", {"frames": frame_position})
    return manifest


def load_manifest(split: str) -> dict:
    return json.loads((DATASET / split / "manifest.json").read_text(encoding="utf-8"))


def load_samples(split: str, mmap_mode: str = "r"):
    manifest = load_manifest(split)
    directory = DATASET / split
    samples = manifest["samples"]
    arrays = {}
    for key, filename in manifest["sample_arrays"].items():
        data = np.load(directory / filename, mmap_mode=mmap_mode)
        arrays[key] = data[:samples]
    return arrays, manifest


def load_frames(split: str, mmap_mode: str = "r"):
    manifest = load_manifest(split)
    directory = DATASET / split / "frames"
    frames = int(manifest["frames"])
    arrays = {}
    for key, filename in manifest["frame_arrays"].items():
        data = np.load(directory / filename, mmap_mode=mmap_mode)
        arrays[key] = data[:frames]
    return arrays, manifest


def _chunked(indices: np.ndarray, size: int = 250_000):
    for start in range(0, len(indices), size):
        yield indices[start : start + size]


def _v1_probabilities(X: np.ndarray, model: ReliabilityModel) -> np.ndarray:
    output = np.empty(len(X), dtype=np.float64)
    for indices in _chunked(np.arange(len(X))):
        matrix = (np.asarray(X[indices, :24], dtype=float) - model.mean) / model.scale
        logits = model._forward(matrix)
        output[indices] = 1.0 / (
            1.0 + np.exp(-np.clip(model.calibration[0] * logits + model.calibration[1], -60, 60))
        )
    return output


def _checkpoint_probabilities(
    checkpoint: dict, X: np.ndarray, indices: np.ndarray
) -> np.ndarray:
    output = np.empty(len(X), dtype=np.float64)
    mean = checkpoint["mean"]
    scale = checkpoint["scale"]
    weights = type("Weights", (), checkpoint)()
    for chunk in _chunked(np.arange(len(X))):
        matrix = (np.asarray(X[chunk][:, indices], dtype=float) - mean) / scale
        logits = _forward(weights, matrix)[0]
        slope, intercept = np.asarray(checkpoint["calibration"], dtype=float)
        output[chunk] = 1.0 / (
            1.0 + np.exp(-np.clip(slope * logits + intercept, -60, 60))
        )
    return output


def _save_checkpoint(
    directory: Path,
    seed: int,
    indices: list[int],
    mean: np.ndarray,
    scale: np.ndarray,
    weights,
    calibration: tuple[float, float],
) -> dict:
    full_mean = np.zeros(len(FEATURE_NAMES_V2))
    full_scale = np.ones(len(FEATURE_NAMES_V2))
    full_mean[indices] = mean
    full_scale[indices] = scale
    full_w1 = np.zeros((len(FEATURE_NAMES_V2), weights.W1.shape[1]))
    full_w1[indices] = weights.W1
    path = directory / f"seed_{seed}.npz"
    np.savez_compressed(
        path,
        mean=full_mean,
        scale=full_scale,
        W1=full_w1,
        b1=weights.b1,
        W2=weights.W2,
        b2=weights.b2,
        W3=weights.W3,
        b3=weights.b3,
        calibration=np.asarray(calibration),
    )
    return {
        "file": path.name,
        "sha256": _sha256_file(path),
        "seed": seed,
        "selected_features": len(indices),
    }


def _load_checkpoint_for_local(path: Path, indices: list[int]) -> dict:
    with np.load(path, allow_pickle=False) as arrays:
        return {
            "mean": np.asarray(arrays["mean"], dtype=float)[indices],
            "scale": np.asarray(arrays["scale"], dtype=float)[indices],
            "W1": np.asarray(arrays["W1"], dtype=float)[indices],
            "b1": np.asarray(arrays["b1"], dtype=float),
            "W2": np.asarray(arrays["W2"], dtype=float),
            "b2": np.asarray(arrays["b2"], dtype=float),
            "W3": np.asarray(arrays["W3"], dtype=float),
            "b3": np.asarray(arrays["b3"], dtype=float),
            "calibration": np.asarray(arrays["calibration"], dtype=float),
        }


def _scope_masks(meta: dict, manifest: dict) -> list[tuple[str, str, np.ndarray]]:
    n_available = np.asarray(meta["n_available"])
    n_nlos = np.asarray(meta["n_nlos"])
    has_dropout = np.asarray(meta["has_dropout"], dtype=bool)
    has_outage = np.asarray(meta["has_outage"], dtype=bool)
    masks = [
        ("all", "all", np.ones(len(n_available), dtype=bool)),
        ("solvable", "solvable", n_available >= 3),
        ("coasting", "coasting", n_available < 3),
    ]
    for index, name in enumerate(manifest["layouts"]):
        masks.append(("layout", name, np.asarray(meta["layout_index"]) == index))
    for index, name in enumerate(manifest["variants"]):
        masks.append(("variant", name, np.asarray(meta["variant_index"]) == index))
    for count in COUNTS:
        masks.append(("anchor_count", str(count), np.asarray(meta["count"]) == count))
    group = _nlos_group(n_nlos, n_available)
    for index, name in enumerate(NLOS_GROUP_NAMES):
        masks.append(("nlos_group", name, group == index))
    masks.extend(
        [
            ("calibration", "no_loss", ~has_dropout),
            ("calibration", "packet_loss", has_dropout),
            ("focus", "nlos", n_nlos > 0),
            ("focus", "all_nlos", (n_available >= 3) & (n_nlos == n_available)),
            (
                "focus",
                "majority_nlos",
                (n_available >= 3) & (n_nlos > n_available / 2) & (n_nlos < n_available),
            ),
            ("focus", "nlos_packet", (n_nlos > 0) & has_dropout),
            (
                "focus",
                "nlos_packet_outage",
                (n_nlos > 0) & has_dropout & has_outage,
            ),
            (
                "focus",
                "solvable_majority_nlos",
                (n_available >= 3) & (n_nlos > n_available / 2) & (n_nlos < n_available),
            ),
            (
                "focus",
                "solvable_all_nlos",
                (n_available >= 3) & (n_nlos == n_available),
            ),
        ]
    )
    return masks


def _metric_rows(
    y: np.ndarray,
    probabilities: np.ndarray,
    meta: dict,
    manifest: dict,
    *,
    variant: str,
    seed,
    scope_masks=None,
) -> list[dict]:
    rows = []
    for scope, name, mask in scope_masks or _scope_masks(meta, manifest):
        if not np.any(mask):
            continue
        rows.append(
            {
                "variant": variant,
                "seed": seed,
                "scope": scope,
                "name": name,
                **metric_row(y[mask], probabilities[mask]),
            }
        )
    return rows


def _fit_variant(
    train_X: np.ndarray,
    y_train: np.ndarray,
    run_ids: np.ndarray,
    val_X: np.ndarray,
    variant: dict,
    seed: int,
    fit_mask: np.ndarray,
    calibration_mask: np.ndarray,
    directory: Path,
) -> tuple[dict, np.ndarray, list[dict]]:
    indices = variant["indices"]
    fit_indices = np.flatnonzero(fit_mask)
    cal_indices = np.flatnonzero(calibration_mask)
    X_fit = np.asarray(train_X[fit_indices][:, indices], dtype=float)
    X_cal = np.asarray(train_X[cal_indices][:, indices], dtype=float)
    y_fit = np.asarray(y_train[fit_indices], dtype=int)
    y_cal = np.asarray(y_train[cal_indices], dtype=int)
    X_fit, X_cal, _, mean, scale = _standardize(X_fit, X_cal, X_cal)
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
    slope, intercept, calibration = fit_platt(_forward(weights, X_cal)[0], y_cal)
    checkpoint_entry = _save_checkpoint(
        directory,
        seed,
        indices,
        mean,
        scale,
        weights,
        (slope, intercept),
    )
    local = _load_checkpoint_for_local(directory / checkpoint_entry["file"], indices)
    probabilities = _checkpoint_probabilities(local, val_X, indices)
    return {
        "entry": checkpoint_entry,
        "mean": mean,
        "scale": scale,
        "weights": weights,
        "calibration": (slope, intercept),
        "training": training,
    }, probabilities, []


def _config_for_model(
    variant: dict, entries: list[dict], training_rows: list[dict]
) -> dict:
    return {
        "model_version": MODEL_VERSION_V2,
        "feature_version": FEATURE_VERSION_V2,
        "feature_names": FEATURE_NAMES_V2,
        "variant": {
            "name": variant["name"],
            "family": variant["family"],
            "group": variant.get("group"),
            "n_features": variant["n_features"],
            "features": variant["names"],
        },
        "checkpoints": entries,
        "weight_policy": {
            "floor": POLICY_FLOOR,
            "threshold": POLICY_THRESHOLD,
            "ambiguous_rule": "predicted_los < 3",
            "unchanged_from_v1": True,
        },
        "training": {
            "seeds": list(TRAINING_SEEDS),
            "epochs": 35,
            "batch_size": 512,
            "learning_rate": 1.5e-3,
            "l2": 1e-4,
            "calibration": "Platt on run-grouped training calibration split",
            "rows": training_rows,
        },
        "label_rule": (
            "y=1 iff an available observation has simulator kind='nlos'; y=0 iff kind='los'. "
            "Disabled, outage and packet-loss observations are excluded from supervision."
        ),
        "dataset_config": "configs/ai/reliability-dataset-v2.json",
    }


def train_one_variant(name: str) -> dict:
    variant = next(
        (current for current in ablation_variants() if current["name"] == name),
        None,
    )
    if variant is None:
        raise ValueError(f"unknown variant: {name}")
    train, _ = load_samples("train")
    validation, val_manifest = load_samples("validation")
    X_train = train["X"]
    y_train = np.asarray(train["y"], dtype=int)
    run_ids = np.asarray(train["run_id"], dtype=int)
    X_val = validation["X"]
    y_val = np.asarray(validation["y"], dtype=int)
    meta = {
        key: np.asarray(validation[key])
        for key in (
            "run_id",
            "layout_index",
            "variant_index",
            "count",
            "n_nlos",
            "n_available",
            "has_dropout",
            "has_outage",
        )
    }
    calibration_mask = grouped_calibration_split(run_ids)
    fit_mask = ~calibration_mask
    directory = MODEL_OUTPUT / name
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    training_rows = []
    seed_probabilities = []
    rows = []
    for seed in TRAINING_SEEDS:
        model_info, probabilities, _ = _fit_variant(
            X_train,
            y_train,
            run_ids,
            X_val,
            variant,
            seed,
            fit_mask,
            calibration_mask,
            directory,
        )
        entries.append(model_info["entry"])
        training_rows.append(
            {
                "seed": seed,
                "best_epoch": model_info["training"]["best_epoch"],
                "best_validation_log_loss": model_info["training"][
                    "best_validation_log_loss"
                ],
            }
        )
        seed_probabilities.append(probabilities)
        rows.extend(
            _metric_rows(
                y_val,
                probabilities,
                meta,
                val_manifest,
                variant=name,
                seed=seed,
            )
        )
    ensemble = np.mean(np.asarray(seed_probabilities), axis=0)
    np.save(directory / "validation-probabilities.npy", ensemble.astype(np.float32))
    _write_json(directory / "config.json", _config_for_model(variant, entries, training_rows))
    rows.extend(
        _metric_rows(
            y_val,
            ensemble,
            meta,
            val_manifest,
            variant=name,
            seed="ensemble",
        )
    )
    CLASSIFIER_PARTIAL.mkdir(parents=True, exist_ok=True)
    payload = {
        "variant": {
            "name": name,
            "family": variant["family"],
            "n_features": variant["n_features"],
        },
        "rows": rows,
        "training_rows": training_rows,
    }
    _write_json(CLASSIFIER_PARTIAL / f"{name}.json", payload)
    return {
        "variant": name,
        "rows": len(rows),
        "checkpoints": len(entries),
        "n_features": variant["n_features"],
    }


def run_classifier_from_partials() -> dict:
    started = perf_counter()
    train, train_manifest = load_samples("train")
    validation, val_manifest = load_samples("validation")
    X_val = validation["X"]
    y_val = np.asarray(validation["y"], dtype=int)
    meta = {
        key: np.asarray(validation[key])
        for key in (
            "run_id",
            "layout_index",
            "variant_index",
            "count",
            "n_nlos",
            "n_available",
            "has_dropout",
            "has_outage",
        )
    }
    classifier_dir = OUTPUT / "classifier"
    classifier_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    ensembles = {}
    selection_rows = []
    for variant in ablation_variants():
        partial = CLASSIFIER_PARTIAL / f"{variant['name']}.json"
        probabilities_path = MODEL_OUTPUT / variant["name"] / "validation-probabilities.npy"
        if not partial.is_file() or not probabilities_path.is_file():
            continue
        payload = json.loads(partial.read_text(encoding="utf-8"))
        rows.extend(payload["rows"])
        ensembles[variant["name"]] = np.load(probabilities_path)
        selection_rows.append(payload["variant"])
    v1 = ReliabilityModel.load(V1_MODEL)
    if not v1.available:
        raise RuntimeError("v1 model is not available")
    v1_probabilities = _v1_probabilities(X_val, v1)
    ensembles["v1"] = v1_probabilities
    np.save(
        classifier_dir / "v1-validation-probabilities.npy",
        v1_probabilities.astype(np.float32),
    )
    rows.extend(
        _metric_rows(
            y_val,
            v1_probabilities,
            meta,
            val_manifest,
            variant="v1",
            seed="frozen",
        )
    )
    _write_csv(classifier_dir / "metrics.csv", rows)
    ensemble_rows = [
        row for row in rows if row["seed"] == "ensemble" or row["seed"] == "frozen"
    ]
    _write_csv(classifier_dir / "summary.csv", ensemble_rows)
    _write_json(
        classifier_dir / "summary.json",
        {
            "scope": "validation seeds and counts from configs/ai/reliability-dataset-v2.json",
            "label_rule": train_manifest.get(
                "label_rule", "available los/nlos only"
            ),
            "variants": selection_rows,
            "samples": int(len(y_val)),
            "frames": val_manifest["frames"],
            "runs": val_manifest["runs"],
            "elapsed_seconds": perf_counter() - started,
            "train_samples": int(len(train["y"])),
        },
    )
    _calibration_by_anchor_count(ensembles, y_val, meta, val_manifest)
    _six_anchor_classifier(ensembles, y_val, meta, val_manifest)
    return {"rows": len(rows), "variants": len(ensembles)}


def run_classifier() -> dict:
    started = perf_counter()
    train, train_manifest = load_samples("train")
    validation, val_manifest = load_samples("validation")
    X_train = train["X"]
    y_train = np.asarray(train["y"], dtype=int)
    run_ids = np.asarray(train["run_id"], dtype=int)
    X_val = validation["X"]
    y_val = np.asarray(validation["y"], dtype=int)
    meta = {key: np.asarray(validation[key]) for key in (
        "run_id",
        "layout_index",
        "variant_index",
        "count",
        "n_nlos",
        "n_available",
        "has_dropout",
        "has_outage",
    )}
    calibration_mask = grouped_calibration_split(run_ids)
    fit_mask = ~calibration_mask
    MODEL_OUTPUT.mkdir(parents=True, exist_ok=True)
    classifier_dir = OUTPUT / "classifier"
    classifier_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    ensembles = {}
    selection_rows = []
    for variant in ablation_variants():
        directory = MODEL_OUTPUT / variant["name"]
        directory.mkdir(parents=True, exist_ok=True)
        entries = []
        training_rows = []
        seed_probabilities = []
        for seed in TRAINING_SEEDS:
            model_info, probabilities, _ = _fit_variant(
                X_train,
                y_train,
                run_ids,
                X_val,
                variant,
                seed,
                fit_mask,
                calibration_mask,
                directory,
            )
            entries.append(model_info["entry"])
            training_rows.append(
                {
                    "seed": seed,
                    "best_epoch": model_info["training"]["best_epoch"],
                    "best_validation_log_loss": model_info["training"][
                        "best_validation_log_loss"
                    ],
                }
            )
            seed_probabilities.append(probabilities)
            rows.extend(
                _metric_rows(
                    y_val,
                    probabilities,
                    meta,
                    val_manifest,
                    variant=variant["name"],
                    seed=seed,
                )
            )
        ensemble = np.mean(np.asarray(seed_probabilities), axis=0)
        ensembles[variant["name"]] = ensemble
        np.save(directory / "validation-probabilities.npy", ensemble.astype(np.float32))
        _write_json(
            directory / "config.json",
            _config_for_model(variant, entries, training_rows),
        )
        rows.extend(
            _metric_rows(
                y_val,
                ensemble,
                meta,
                val_manifest,
                variant=variant["name"],
                seed="ensemble",
            )
        )
        selection_rows.append(
            {
                "variant": variant["name"],
                "family": variant["family"],
                "n_features": variant["n_features"],
            }
        )
    v1 = ReliabilityModel.load(V1_MODEL)
    if not v1.available:
        raise RuntimeError("v1 model is not available")
    v1_probabilities = _v1_probabilities(X_val, v1)
    ensembles["v1"] = v1_probabilities
    np.save(classifier_dir / "v1-validation-probabilities.npy", v1_probabilities.astype(np.float32))
    rows.extend(
        _metric_rows(y_val, v1_probabilities, meta, val_manifest, variant="v1", seed="frozen")
    )
    _write_csv(OUTPUT / "classifier" / "metrics.csv", rows)
    ensemble_rows = [row for row in rows if row["seed"] == "ensemble" or row["seed"] == "frozen"]
    _write_csv(OUTPUT / "classifier" / "summary.csv", ensemble_rows)
    _write_json(
        OUTPUT / "classifier" / "summary.json",
        {
            "scope": "validation seeds and counts from configs/ai/reliability-dataset-v2.json",
            "label_rule": train_manifest["label_rule"]
            if "label_rule" in train_manifest
            else "available los/nlos only",
            "variants": selection_rows,
            "samples": int(len(y_val)),
            "frames": val_manifest["frames"],
            "runs": val_manifest["runs"],
            "elapsed_seconds": perf_counter() - started,
        },
    )
    _calibration_by_anchor_count(ensembles, y_val, meta, val_manifest)
    _six_anchor_classifier(ensembles, y_val, meta, val_manifest)
    return {"rows": len(rows), "variants": len(ensembles)}


def _calibration_by_anchor_count(ensembles, y_val, meta, manifest):
    rows = []
    bins = []
    count_array = np.asarray(meta["count"])
    dropout = np.asarray(meta["has_dropout"], dtype=bool)
    for variant, probabilities in ensembles.items():
        for condition, condition_mask in (("no_loss", ~dropout), ("packet_loss", dropout)):
            for count in COUNTS:
                mask = condition_mask & (count_array == count)
                if not np.any(mask):
                    continue
                metrics = metric_row(y_val[mask], probabilities[mask])
                rows.append(
                    {
                        "variant": variant,
                        "condition": condition,
                        "anchor_count": count,
                        **metrics,
                    }
                )
                for entry in reliability_bins(y_val[mask], probabilities[mask]):
                    bins.append(
                        {
                            "variant": variant,
                            "condition": condition,
                            "anchor_count": count,
                            **entry,
                        }
                    )
    _write_csv(OUTPUT / "calibration" / "by-anchor-count.csv", rows)
    _write_csv(OUTPUT / "calibration" / "reliability-by-anchor-count.csv", bins)
    summary = {}
    for variant in ensembles:
        rows_for_variant = [row for row in rows if row["variant"] == variant]
        by_condition_count = {
            (row["condition"], row["anchor_count"]): row["ece"]
            for row in rows_for_variant
        }
        increases = {}
        for count in COUNTS:
            no_loss = by_condition_count.get(("no_loss", count))
            packet_loss = by_condition_count.get(("packet_loss", count))
            increases[str(count)] = (
                packet_loss - no_loss
                if no_loss is not None and packet_loss is not None
                else None
            )
        summary[variant] = {"packet_loss_ece_increase": increases}
    _write_json(OUTPUT / "calibration" / "summary.json", summary)


def _six_anchor_classifier(ensembles, y_val, meta, manifest):
    count_array = np.asarray(meta["count"])
    mask = count_array == 6
    n_nlos = np.asarray(meta["n_nlos"])[mask]
    n_available = np.asarray(meta["n_available"])[mask]
    summary = {}
    for variant, probabilities in ensembles.items():
        selected = probabilities[mask]
        summary[variant] = {
            "samples": int(mask.sum()),
            "anchor_misclassification_rate": float(
                np.mean((selected >= 0.5).astype(int) != y_val[mask])
            ),
            "frame_nlos_count_distribution": {
                str(value): int(np.sum(n_nlos == value)) for value in range(N_ANCHORS + 1)
            },
            "mean_n_nlos": float(np.mean(n_nlos)) if len(n_nlos) else None,
            "mean_n_available": float(np.mean(n_available)) if len(n_available) else None,
        }
    _write_json(OUTPUT / "classifier" / "six-anchor.json", summary)


def _probability_from_model(model: ReliabilityV2Model, matrix: np.ndarray) -> np.ndarray:
    return model._probabilities(matrix)


def _policy(probabilities: np.ndarray, anchors: list[str]):
    weights = {}
    predictions = []
    for anchor, nlos_probability in zip(anchors, probabilities, strict=True):
        p_los = float(np.clip(1.0 - nlos_probability, 0.0, 1.0))
        if p_los >= POLICY_THRESHOLD:
            weight = 1.0
        else:
            weight = POLICY_FLOOR + (1.0 - POLICY_FLOOR) * (p_los / POLICY_THRESHOLD)
        predictions.append(AIPrediction(anchor_id=anchor, p_los=p_los, weight=float(weight)))
        weights[anchor] = float(weight)
    predicted_los = sum(prediction.p_los >= 0.5 for prediction in predictions)
    if predicted_los < 3:
        return None, True, predictions
    return weights, False, predictions


def _robust_estimate(frame, index) -> Estimate:
    position = frame["robust"][index]
    covariance = frame["robust_cov"][index]
    if not np.isfinite(position).all():
        return Estimate(status="insufficient", reason="No robust fix")
    return Estimate(
        position=(float(position[0]), float(position[1])),
        status="valid",
        reason="Cached robust fix",
        covariance=(
            covariance.tolist() if np.isfinite(covariance).all() else None
        ),
    )


def _measurements_for_frame(frame, environment, index, timestamp) -> list[Measurement]:
    measurements = []
    available = np.flatnonzero(frame["available"][index])
    for anchor_index in available:
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


def evaluate_frames(
    probabilities_fn,
    frames,
    manifest: dict,
    *,
    model_name: str,
) -> dict:
    arrays = defaultdict(list)
    run_to_indices = {}
    run_ids = np.asarray(frames["run_id"])
    for run_id in np.unique(run_ids):
        run_to_indices[int(run_id)] = np.flatnonzero(run_ids == run_id)
    environments = {}
    for catalog in manifest["run_catalog"]:
        run_id = int(catalog["run_id"])
        index_array = run_to_indices[run_id]
        key = (catalog["environment_id"], catalog["floor_id"])
        if key not in environments:
            environments[key] = resolve_floor(
                load_environments()[catalog["environment_id"]], catalog["floor_id"]
            )
        environment = environments[key]
        tracker = MotionTracker()
        for index in index_array:
            timestamp = round(int(frames["step"][index]) * DT, 8)
            measurements = _measurements_for_frame(frames, environment, index, timestamp)
            robust_estimate = _robust_estimate(frames, index)
            available = np.flatnonzero(frames["available"][index])
            probabilities = None
            if len(available) >= 3:
                matrix = np.asarray(frames["features"][index, available], dtype=float)
                if np.isfinite(matrix).all():
                    probabilities = probabilities_fn(matrix)
            ambiguous = True
            predicted_nlos = 0
            ai_estimate = robust_estimate
            if probabilities is not None and np.isfinite(probabilities).all():
                anchors = [environment.anchors[int(i)].id for i in available]
                weights, ambiguous, predictions = _policy(probabilities, anchors)
                predicted_nlos = sum(prediction.p_los < 0.5 for prediction in predictions)
                if weights is not None:
                    ai_estimate = solve(
                        measurements,
                        robust=True,
                        ai_weights=weights,
                    )
            ai_tracking = tracker.update(ai_estimate, timestamp)
            stat_position = frames["stat_tracking"][index]
            truth = frames["truth"][index]
            stat_error = (
                float(np.linalg.norm(stat_position - truth))
                if np.isfinite(stat_position).all()
                else np.nan
            )
            ai_error = (
                float(np.linalg.norm(np.asarray(ai_tracking.position) - truth))
                if ai_tracking.position is not None
                else np.nan
            )
            arrays["run_id"].append(run_id)
            arrays["step"].append(int(frames["step"][index]))
            arrays["stat_error"].append(stat_error)
            arrays["ai_error"].append(ai_error)
            arrays["ambiguous"].append(int(ambiguous))
            arrays["predicted_nlos"].append(predicted_nlos)
            arrays["available"].append(int(len(available)))
            arrays["n_nlos"].append(int(frames["n_nlos"][index]))
            arrays["has_dropout"].append(bool(frames["has_dropout"][index]))
            arrays["has_outage"].append(bool(frames["has_outage"][index]))
            arrays["layout_index"].append(int(frames["layout_index"][index]))
            arrays["variant_index"].append(int(frames["variant_index"][index]))
            arrays["count"].append(int(frames["count"][index]))
    result = {key: np.asarray(value) for key, value in arrays.items()}
    result["model"] = model_name
    return result


def _rmse(values: np.ndarray) -> float:
    values = values[np.isfinite(values)]
    return float(np.sqrt(np.mean(values**2))) if len(values) else float("nan")


def _bootstrap_from_runs(stat: np.ndarray, ai: np.ndarray, runs: np.ndarray) -> dict:
    valid = np.isfinite(stat) & np.isfinite(ai)
    stat = stat[valid]
    ai = ai[valid]
    runs = runs[valid]
    if len(stat) == 0:
        return {
            "stat_rmse": None,
            "ai_rmse": None,
            "difference": None,
            "ci_low": None,
            "ci_high": None,
        }
    unique_runs = np.unique(runs)
    run_to_stats = {}
    for run in unique_runs:
        selected = runs == run
        run_to_stats[int(run)] = (
            float(np.sum(stat[selected] ** 2)),
            float(np.sum(ai[selected] ** 2)),
            int(selected.sum()),
        )
    observed_stat = _rmse(stat)
    observed_ai = _rmse(ai)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples = np.empty(BOOTSTRAP, dtype=float)
    for index in range(BOOTSTRAP):
        chosen = rng.choice(unique_runs, size=len(unique_runs), replace=True)
        stat_sq = 0.0
        ai_sq = 0.0
        count = 0
        for run in chosen:
            run_stat, run_ai, run_count = run_to_stats[int(run)]
            stat_sq += run_stat
            ai_sq += run_ai
            count += run_count
        samples[index] = np.sqrt(ai_sq / count) - np.sqrt(stat_sq / count)
    return {
        "stat_rmse": observed_stat,
        "ai_rmse": observed_ai,
        "difference": observed_ai - observed_stat,
        "ci_low": float(np.percentile(samples, 2.5)),
        "ci_high": float(np.percentile(samples, 97.5)),
        "runs": int(len(unique_runs)),
        "frames": int(len(stat)),
    }


def _end_to_end_scopes(result: dict, manifest: dict):
    n_available = result["available"]
    n_nlos = result["n_nlos"]
    dropout = result["has_dropout"].astype(bool)
    outage = result["has_outage"].astype(bool)
    masks = [
        ("all", "all", np.ones(len(n_available), dtype=bool)),
        ("solvable", "solvable", n_available >= 3),
        ("coasting", "coasting", n_available < 3),
    ]
    for index, name in enumerate(manifest["layouts"]):
        masks.append(("layout", name, result["layout_index"] == index))
    for index, name in enumerate(manifest["variants"]):
        masks.append(("variant", name, result["variant_index"] == index))
    for count in COUNTS:
        masks.append(("anchor_count", str(count), result["count"] == count))
    masks.extend(
        [
            ("focus", "nlos", n_nlos > 0),
            ("focus", "all_nlos", (n_available >= 3) & (n_nlos == n_available)),
            (
                "focus",
                "majority_nlos",
                (n_available >= 3) & (n_nlos > n_available / 2) & (n_nlos < n_available),
            ),
            ("focus", "nlos_packet", (n_nlos > 0) & dropout),
            ("focus", "nlos_packet_outage", (n_nlos > 0) & dropout & outage),
            (
                "focus",
                "solvable_majority_nlos",
                (n_available >= 3) & (n_nlos > n_available / 2) & (n_nlos < n_available),
            ),
            (
                "focus",
                "solvable_all_nlos",
                (n_available >= 3) & (n_nlos == n_available),
            ),
        ]
    )
    return masks


def _end_to_end_metrics(result: dict, manifest: dict) -> list[dict]:
    rows = []
    for scope, name, mask in _end_to_end_scopes(result, manifest):
        if not np.any(mask):
            continue
        interval = _bootstrap_from_runs(
            result["stat_error"][mask],
            result["ai_error"][mask],
            result["run_id"][mask],
        )
        stat_rmse = interval["stat_rmse"]
        difference = interval["difference"]
        relative = (
            100.0 * difference / stat_rmse
            if stat_rmse is not None and stat_rmse > 0 and difference is not None
            else None
        )
        ci_low = interval["ci_low"]
        ci_high = interval["ci_high"]
        relative_low = (
            100.0 * ci_low / stat_rmse
            if stat_rmse not in (None, 0) and ci_low is not None
            else None
        )
        relative_high = (
            100.0 * ci_high / stat_rmse
            if stat_rmse not in (None, 0) and ci_high is not None
            else None
        )
        if ci_high is not None and ci_high < 0:
            label = "improved"
        elif ci_low is not None and ci_low > 0 and relative is not None and relative > 0.5:
            label = "regressed"
        else:
            label = "unchanged"
        rows.append(
            {
                "variant": result["model"],
                "scope": scope,
                "name": name,
                "stat_rmse": stat_rmse,
                "ai_rmse": interval["ai_rmse"],
                "difference": difference,
                "relative_percent": relative,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "relative_ci_low": relative_low,
                "relative_ci_high": relative_high,
                "runs": interval["runs"],
                "frames": interval["frames"],
                "label": label,
            }
        )
    return rows


def _end_to_end_model_names(selected_only: bool = False) -> list[str]:
    names = ["v1"]
    for variant in ablation_variants():
        if variant["family"] == "comparison":
            continue
        if selected_only and variant["name"] not in {"g0_full", "g0_mapfree", "all_mapfree"}:
            continue
        names.append(variant["name"])
    names.append("all_map")
    return names


def evaluate_named_model(name: str, frames=None, manifest: dict | None = None) -> dict | None:
    if frames is None or manifest is None:
        frames, manifest = load_frames("validation")
    if name == "v1":
        model = ReliabilityModel.load(V1_MODEL)
        if not model.available:
            raise RuntimeError("v1 model is not available")
        probabilities_fn = partial(_v1_probabilities, model=model)
    else:
        model = ReliabilityV2Model.load(MODEL_OUTPUT / name)
        if not model.available:
            return None
        probabilities_fn = partial(_probability_from_model, model)
    result = evaluate_frames(
        probabilities_fn,
        frames,
        manifest,
        model_name=name,
    )
    PARTIAL.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        PARTIAL / f"{name}.npz",
        **{
            key: value
            for key, value in result.items()
            if key != "model"
        },
    )
    return result


def _load_partial_result(name: str) -> dict:
    path = PARTIAL / f"{name}.npz"
    with np.load(path) as data:
        result = {key: np.asarray(data[key]) for key in data.files}
    result["model"] = name
    return result


def run_end_to_end_from_partials(selected_only: bool = False) -> dict:
    started = perf_counter()
    frames, manifest = load_frames("validation")
    results = {}
    rows = []
    for name in _end_to_end_model_names(selected_only):
        if not (PARTIAL / f"{name}.npz").is_file():
            continue
        result = _load_partial_result(name)
        results[name] = result
        rows.extend(_end_to_end_metrics(result, manifest))
    if "v1" not in results:
        raise RuntimeError("v1 partial result is missing")
    frame_dir = OUTPUT / "end-to-end" / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for name, result in results.items():
        arrays = {key: value for key, value in result.items() if key != "model"}
        np.savez_compressed(frame_dir / f"{name}.npz", **arrays)
    _write_csv(OUTPUT / "end-to-end" / "metrics.csv", rows)
    _write_json(
        OUTPUT / "end-to-end" / "summary.json",
        {
            "scope": "validation seeds and counts from configs/ai/reliability-dataset-v2.json",
            "comparison": "AI-assisted LOCUS-X versus statistical LOCUS-X on identical frames",
            "policy": {
                "floor": POLICY_FLOOR,
                "threshold": POLICY_THRESHOLD,
                "ambiguous": "predicted_los < 3",
            },
            "tolerance_percent": 0.5,
            "rows": len(rows),
            "elapsed_seconds": perf_counter() - started,
        },
    )
    _six_anchor_end_to_end(results, manifest)
    if selected_only:
        return {"rows": len(rows), "models": list(results), "promotion": None}
    latency = measure_latency()
    selection = _write_selection(results, rows, latency)
    promotion = _promote_selected(selection)
    return {
        "rows": len(rows),
        "models": list(results),
        "latency_runs": latency["runs"],
        "promotion": promotion,
    }


def run_end_to_end(selected_only: bool = False) -> dict:
    started = perf_counter()
    frames, manifest = load_frames("validation")
    v1 = ReliabilityModel.load(V1_MODEL)
    if not v1.available:
        raise RuntimeError("v1 model is not available")
    results = {}
    rows = []
    v1_result = evaluate_frames(
        lambda matrix: _v1_probabilities(matrix, v1),
        frames,
        manifest,
        model_name="v1",
    )
    results["v1"] = v1_result
    rows.extend(_end_to_end_metrics(v1_result, manifest))
    variants = [variant for variant in ablation_variants() if variant["family"] != "comparison"]
    for variant in variants:
        if selected_only and variant["name"] not in {"g0_full", "g0_mapfree", "all_mapfree"}:
            continue
        directory = MODEL_OUTPUT / variant["name"]
        model = ReliabilityV2Model.load(directory)
        if not model.available:
            continue
        result = evaluate_frames(
            lambda matrix, model=model: _probability_from_model(model, matrix),
            frames,
            manifest,
            model_name=variant["name"],
        )
        results[variant["name"]] = result
        rows.extend(_end_to_end_metrics(result, manifest))
    comparison = next(
        variant for variant in ablation_variants() if variant["name"] == "all_map"
    )
    comparison_model = ReliabilityV2Model.load(MODEL_OUTPUT / comparison["name"])
    if comparison_model.available:
        result = evaluate_frames(
            lambda matrix: _probability_from_model(comparison_model, matrix),
            frames,
            manifest,
            model_name="all_map",
        )
        results["all_map"] = result
        rows.extend(_end_to_end_metrics(result, manifest))
    frame_dir = OUTPUT / "end-to-end" / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    for name, result in results.items():
        arrays = {
            key: value
            for key, value in result.items()
            if key != "model"
        }
        np.savez_compressed(frame_dir / f"{name}.npz", **arrays)
    _write_csv(OUTPUT / "end-to-end" / "metrics.csv", rows)
    _write_json(
        OUTPUT / "end-to-end" / "summary.json",
        {
            "scope": "validation seeds and counts from configs/ai/reliability-dataset-v2.json",
            "comparison": "AI-assisted LOCUS-X versus statistical LOCUS-X on identical frames",
            "policy": {
                "floor": POLICY_FLOOR,
                "threshold": POLICY_THRESHOLD,
                "ambiguous": "predicted_los < 3",
            },
            "tolerance_percent": 0.5,
            "rows": len(rows),
            "elapsed_seconds": perf_counter() - started,
        },
    )
    _six_anchor_end_to_end(results, manifest)
    if selected_only:
        return {"rows": len(rows), "models": list(results), "promotion": None}
    latency = measure_latency()
    selection = _write_selection(results, rows, latency)
    promotion = _promote_selected(selection)
    return {
        "rows": len(rows),
        "models": list(results),
        "latency_runs": latency["runs"],
        "promotion": promotion,
    }


def _six_anchor_end_to_end(results, manifest):
    summary = {}
    for name, result in results.items():
        mask = result["count"] == 6
        if not np.any(mask):
            continue
        n_nlos = result["n_nlos"][mask]
        summary[name] = {
            "frames": int(mask.sum()),
            "fallback_rate": float(np.mean(result["ambiguous"][mask])),
            "mean_predicted_nlos": float(np.mean(result["predicted_nlos"][mask])),
            "frame_nlos_count_distribution": {
                str(value): int(np.sum(n_nlos == value)) for value in range(N_ANCHORS + 1)
            },
            "stat_rmse": _rmse(result["stat_error"][mask]),
            "ai_rmse": _rmse(result["ai_error"][mask]),
        }
    _write_json(OUTPUT / "end-to-end" / "six-anchor.json", summary)


def _latency_summary(values: list[float]) -> dict:
    if not values:
        return {"mean_ms": None, "median_ms": None, "p95_ms": None, "frames": 0}
    array = np.asarray(values, dtype=float)
    return {
        "mean_ms": float(np.mean(array)),
        "median_ms": float(np.median(array)),
        "p95_ms": float(np.percentile(array, 95)),
        "frames": int(len(array)),
    }


def measure_latency() -> dict:
    frames, manifest = load_frames("validation")
    environments = {}
    run_by_key = {}
    for catalog in manifest["run_catalog"]:
        key = (catalog["layout"], catalog["count"])
        run_by_key.setdefault(key, int(catalog["run_id"]))
    selected_runs = list(run_by_key.values())
    run_ids = np.asarray(frames["run_id"])
    run_to_indices = {
        run_id: np.flatnonzero(run_ids == run_id) for run_id in selected_runs
    }
    models = {}
    for variant in ablation_variants():
        if variant["family"] == "comparison":
            continue
        model = ReliabilityV2Model.load(MODEL_OUTPUT / variant["name"])
        if model.available:
            models[variant["name"]] = model
    v1 = ReliabilityModel.load(V1_MODEL)
    rows = []
    for name, model in models.items():
        feature_ms = []
        model_ms = []
        total_ms = []
        for catalog in manifest["run_catalog"]:
            run_id = int(catalog["run_id"])
            if run_id not in run_to_indices:
                continue
            key = (catalog["environment_id"], catalog["floor_id"])
            environment = environments.get(key)
            if environment is None:
                environment = resolve_floor(
                    load_environments()[catalog["environment_id"]], catalog["floor_id"]
                )
                environments[key] = environment
            history = FeatureHistoryV2()
            tracker = MotionTracker()
            for index in run_to_indices[run_id]:
                timestamp = round(int(frames["step"][index]) * DT, 8)
                start = perf_counter()
                measurements = _measurements_for_frame(
                    frames, environment, index, timestamp
                )
                baseline = solve(measurements)
                robust = solve(measurements, robust=True)
                previous_state = None if tracker.state is None else tracker.state.copy()
                previous_time = tracker.timestamp
                feature_start = perf_counter()
                batch = extract_features_v2(
                    measurements,
                    environment,
                    baseline,
                    history,
                    previous_state,
                    previous_time,
                )
                feature_elapsed = (perf_counter() - feature_start) * 1000.0
                model_start = perf_counter()
                if batch is not None:
                    model._probabilities(batch.matrix)
                model_elapsed = (perf_counter() - model_start) * 1000.0
                tracker.update(robust, timestamp)
                history.tracker_state = (
                    None if tracker.state is None else tracker.state.copy()
                )
                history.tracker_timestamp = tracker.timestamp
                feature_ms.append(feature_elapsed)
                model_ms.append(model_elapsed)
                total_ms.append((perf_counter() - start) * 1000.0)
        rows.append(
            {
                "variant": name,
                "family": next(
                    variant["family"]
                    for variant in ablation_variants()
                    if variant["name"] == name
                ),
                "n_features": next(
                    variant["n_features"]
                    for variant in ablation_variants()
                    if variant["name"] == name
                ),
                "features": _latency_summary(feature_ms),
                "model_inference": _latency_summary(model_ms),
                "total": _latency_summary(total_ms),
            }
        )
    if v1.available:
        feature_ms = []
        model_ms = []
        total_ms = []
        for catalog in manifest["run_catalog"]:
            run_id = int(catalog["run_id"])
            if run_id not in run_to_indices:
                continue
            key = (catalog["environment_id"], catalog["floor_id"])
            environment = environments.get(key)
            if environment is None:
                environment = resolve_floor(
                    load_environments()[catalog["environment_id"]], catalog["floor_id"]
                )
                environments[key] = environment
            history = FeatureHistoryV2()
            tracker = MotionTracker()
            for index in run_to_indices[run_id]:
                timestamp = round(int(frames["step"][index]) * DT, 8)
                start = perf_counter()
                measurements = _measurements_for_frame(
                    frames, environment, index, timestamp
                )
                baseline = solve(measurements)
                robust = solve(measurements, robust=True)
                previous_state = None if tracker.state is None else tracker.state.copy()
                previous_time = tracker.timestamp
                feature_start = perf_counter()
                batch = extract_features_v2(
                    measurements,
                    environment,
                    baseline,
                    history,
                    previous_state,
                    previous_time,
                )
                feature_elapsed = (perf_counter() - feature_start) * 1000.0
                model_start = perf_counter()
                if batch is not None:
                    _v1_probabilities(batch.matrix, v1)
                model_elapsed = (perf_counter() - model_start) * 1000.0
                tracker.update(robust, timestamp)
                history.tracker_state = (
                    None if tracker.state is None else tracker.state.copy()
                )
                history.tracker_timestamp = tracker.timestamp
                feature_ms.append(feature_elapsed)
                model_ms.append(model_elapsed)
                total_ms.append((perf_counter() - start) * 1000.0)
        rows.append(
            {
                "variant": "v1",
                "family": "control",
                "n_features": len(FEATURE_NAMES),
                "features": _latency_summary(feature_ms),
                "model_inference": _latency_summary(model_ms),
                "total": _latency_summary(total_ms),
            }
        )
    payload = {
        "scope": "one validation run per layout and anchor count",
        "runs": len(selected_runs),
        "rows": rows,
    }
    _write_json(OUTPUT / "latency.json", payload)
    _write_csv(OUTPUT / "latency.csv", [
        {
            "variant": row["variant"],
            "family": row["family"],
            "n_features": row["n_features"],
            "feature_mean_ms": row["features"]["mean_ms"],
            "feature_median_ms": row["features"]["median_ms"],
            "feature_p95_ms": row["features"]["p95_ms"],
            "model_mean_ms": row["model_inference"]["mean_ms"],
            "model_median_ms": row["model_inference"]["median_ms"],
            "model_p95_ms": row["model_inference"]["p95_ms"],
            "total_mean_ms": row["total"]["mean_ms"],
            "total_median_ms": row["total"]["median_ms"],
            "total_p95_ms": row["total"]["p95_ms"],
        }
        for row in rows
    ])
    return payload


def _write_selection(results, rows, latency: dict | None = None):
    latency_by_variant = {
        row["variant"]: row["total"]["mean_ms"] for row in (latency or {}).get("rows", [])
    }
    candidates = {
        variant["name"]: variant
        for variant in ablation_variants()
        if variant["family"] == "mapfree"
    }
    summary = {}
    for name, variant in candidates.items():
        variant_rows = [row for row in rows if row["variant"] == name]
        if not variant_rows:
            continue
        regressions = [
            row
            for row in variant_rows
            if row["scope"] in {"all", "solvable", "coasting", "layout", "focus", "anchor_count"}
            and row["label"] == "regressed"
        ]
        anchor_regressions = [
            row
            for row in variant_rows
            if row["scope"] == "anchor_count" and row["label"] == "regressed"
        ]
        focus = {
            row["name"]: row
            for row in variant_rows
            if row["scope"] == "focus"
        }
        majority = focus.get("solvable_majority_nlos")
        all_nlos = focus.get("solvable_all_nlos")
        nlos_packet = focus.get("nlos_packet")
        nlos_packet_outage = focus.get("nlos_packet_outage")
        summary[name] = {
            "n_features": variant["n_features"],
            "disqualified_regressions": len(regressions),
            "anchor_count_regressions": len(anchor_regressions),
            "majority_nlos_difference": majority["difference"] if majority else None,
            "majority_nlos_ci_high": majority["ci_high"] if majority else None,
            "all_nlos_difference": all_nlos["difference"] if all_nlos else None,
            "all_nlos_ci_high": all_nlos["ci_high"] if all_nlos else None,
            "nlos_packet_difference": nlos_packet["difference"] if nlos_packet else None,
            "nlos_packet_outage_difference": (
                nlos_packet_outage["difference"] if nlos_packet_outage else None
            ),
        }
        summary[name]["supported_majority"] = bool(
            majority is not None
            and majority["ci_high"] is not None
            and majority["ci_high"] < 0
        )
        summary[name]["supported_all_nlos"] = bool(
            all_nlos is not None
            and all_nlos["ci_high"] is not None
            and all_nlos["ci_high"] < 0
        )
        supported = [
            summary[name]["majority_nlos_difference"]
            if summary[name]["supported_majority"]
            else None,
            summary[name]["all_nlos_difference"]
            if summary[name]["supported_all_nlos"]
            else None,
        ]
        supported_values = [value for value in supported if value is not None]
        summary[name]["best_supported_focus_difference"] = (
            min(supported_values) if supported_values else None
        )
    eligible = [
        name
        for name, row in summary.items()
        if row["disqualified_regressions"] == 0
        and row["best_supported_focus_difference"] is not None
    ]
    eligible.sort(
        key=lambda name: (
            summary[name]["best_supported_focus_difference"],
            summary[name]["majority_nlos_difference"]
            if summary[name]["majority_nlos_difference"] is not None
            else 0.0,
            summary[name]["all_nlos_difference"]
            if summary[name]["all_nlos_difference"] is not None
            else 0.0,
            summary[name]["nlos_packet_difference"]
            if summary[name]["nlos_packet_difference"] is not None
            else 0.0,
            summary[name]["nlos_packet_outage_difference"]
            if summary[name]["nlos_packet_outage_difference"] is not None
            else 0.0,
            summary[name]["n_features"],
            latency_by_variant.get(name, float("inf")),
        )
    )
    selected = eligible[0] if eligible else "v1"
    selection = {
        "rule": (
            "no significant +>0.5% relative regression in any focus slice, layout, or "
            "anchor count; then largest supported majority/all-NLOS improvement; mixed "
            "NLOS+packet-loss scopes, map-free, and fewer features break ties; otherwise v1"
        ),
        "candidates": summary,
        "eligible": eligible,
        "selected": selected,
        "selected_n_features": summary.get(selected, {}).get("n_features"),
    }
    _write_json(OUTPUT / "selection.json", selection)
    return selection


def _promote_selected(selection: dict) -> dict:
    selected = selection["selected"]
    if selected == "v1":
        return {"promoted": False, "selected": "v1", "reason": "selection rule retained v1"}
    source = MODEL_OUTPUT / selected
    if not source.is_dir():
        raise RuntimeError(f"selected model directory is missing: {source}")
    config = json.loads((source / "config.json").read_text(encoding="utf-8"))
    FINAL_MODEL.mkdir(parents=True, exist_ok=True)
    for stale in FINAL_MODEL.glob("*.npz"):
        stale.unlink()
    for entry in config["checkpoints"]:
        shutil.copy2(source / entry["file"], FINAL_MODEL / entry["file"])
    shutil.copy2(OUTPUT / "selection.json", FINAL_MODEL / "selection.json")
    shutil.copy2(source / "config.json", FINAL_MODEL / "config.json")
    model = ReliabilityV2Model.load(FINAL_MODEL)
    if not model.available:
        raise RuntimeError(f"promoted model failed validation: {model.error}")
    result = {
        "promoted": True,
        "selected": selected,
        "directory": str(FINAL_MODEL.relative_to(ROOT)),
        "checkpoints": len(config["checkpoints"]),
        "n_features": selection.get("selected_n_features"),
    }
    _write_json(FINAL_MODEL / "promotion.json", result)
    return result


def check_g0() -> dict:
    v2, _ = load_samples("train")
    phase2_dir = ROOT / "artifacts" / "ai-reliability" / "ablation-v1" / "dataset"
    phase2 = np.load(phase2_dir / "train.npz")
    x_phase2 = phase2["X"]
    y_phase2 = phase2["y"]
    if len(v2["X"]) != len(x_phase2):
        raise AssertionError(f"sample count differs: {len(v2['X'])} vs {len(x_phase2)}")
    max_difference = 0.0
    for indices in _chunked(np.arange(len(x_phase2)), 100_000):
        difference = np.max(np.abs(np.asarray(v2["X"][indices, :24]) - x_phase2[indices]))
        max_difference = max(max_difference, float(difference))
    label_equal = bool(np.array_equal(np.asarray(v2["y"]), y_phase2))
    result = {
        "samples": int(len(x_phase2)),
        "max_abs_g0_difference": max_difference,
        "labels_equal": label_equal,
        "v2_feature_version": FEATURE_VERSION_V2,
        "phase2_feature_names": len(FEATURE_NAMES),
    }
    _write_json(OUTPUT / "g0-control.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--g0-check", action="store_true")
    parser.add_argument("--classifier", action="store_true")
    parser.add_argument("--train-variant")
    parser.add_argument("--aggregate-classifier", action="store_true")
    parser.add_argument("--end-to-end", action="store_true")
    parser.add_argument("--end-to-end-selected", action="store_true")
    parser.add_argument("--evaluate-model")
    parser.add_argument("--aggregate-end-to-end", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if args.all or args.generate:
        print(json.dumps(generate_split("train"), indent=2)[:2000])
        print(json.dumps(generate_split("validation"), indent=2)[:2000])
    if args.all or args.g0_check:
        print(json.dumps(check_g0(), indent=2))
    if args.all or args.classifier:
        print(json.dumps(run_classifier(), indent=2))
    if args.train_variant:
        print(json.dumps(train_one_variant(args.train_variant), indent=2))
    if args.aggregate_classifier:
        print(json.dumps(run_classifier_from_partials(), indent=2))
    if args.all or args.end_to_end:
        print(json.dumps(run_end_to_end(), indent=2))
    if args.end_to_end_selected:
        print(json.dumps(run_end_to_end(selected_only=True), indent=2))
    if args.evaluate_model:
        result = evaluate_named_model(args.evaluate_model)
        summary = None
        if result is not None:
            summary = {
                "model": result["model"],
                "frames": int(len(result["run_id"])),
            }
        print(json.dumps(summary, indent=2))
    if args.aggregate_end_to_end:
        print(json.dumps(run_end_to_end_from_partials(), indent=2))


if __name__ == "__main__":
    main()
