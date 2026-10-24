"""Validation-only weight-policy selection and frozen v2 policy loading.

The module reuses the Phase 3 cached validation frames and G5 probabilities. It
never regenerates ranges and it never reads final-test seeds.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from .ai_phase3 import load_frames, load_samples
from .ai_policy import (  # noqa: F401  (re-exported public API)
    _jsonable,
    load_policy_file,
    policy_hash,
    policy_weights,
)
from .environment import load_environments, resolve_floor
from .estimators import solve
from .tracking import MotionTracker

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts" / "ai-reliability" / "phase4-final"
VALIDATION_OUTPUT = OUTPUT / "policy"
FINAL_MODEL = ROOT / "models" / "ai-reliability-v2"
POLICY_PATH = FINAL_MODEL / "policy-v2.json"
PROBABILITY_CACHE = VALIDATION_OUTPUT / "g5-validation-p_los.npy"
DT = 0.5
N_ANCHORS = 8
COUNTS = (3, 4, 5, 6, 7, 8)
BOOTSTRAP = 2000
BOOTSTRAP_SEED = 20261026
TOLERANCE_PERCENT = 0.5
CURRENT_POLICY_ID = "hard_t005_f005_min3"

THRESHOLDS = (0.05, 0.1, 0.2, 0.35, 0.5)
FLOORS = (0.05, 0.2)
FALLBACKS = ("min3", "min2_if4")


def candidate_grid() -> list[dict]:
    candidates = []
    for fallback in FALLBACKS:
        for threshold in THRESHOLDS:
            for floor in FLOORS:
                candidates.append(
                    {
                        "id": f"hard_t{str(threshold).replace('.', '')}_f"
                        f"{str(floor).replace('.', '')}_{fallback}",
                        "mode": "hard",
                        "threshold": threshold,
                        "floor": floor,
                        "fallback": fallback,
                    }
                )
    candidates.append(
        {
            "id": "soft_f005_min3",
            "mode": "soft",
            "threshold": None,
            "floor": 0.05,
            "fallback": "min3",
        }
    )
    return candidates


def load_policy(path: Path | str = POLICY_PATH) -> dict:
    return load_policy_file(path)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(payload), indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _measurements_for_frame(frame, environment, index, timestamp):
    from .schemas import Measurement

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


def _robust_estimate(frame, index):
    from .schemas import Estimate

    position = frame["robust"][index]
    covariance = frame["robust_cov"][index]
    if not np.isfinite(position).all():
        return Estimate(status="insufficient", reason="No robust fix")
    return Estimate(
        position=(float(position[0]), float(position[1])),
        status="valid",
        reason="Cached robust fix",
        covariance=covariance.tolist() if np.isfinite(covariance).all() else None,
    )


def load_validation_probabilities() -> tuple[dict, dict, np.ndarray]:
    frames, manifest = load_frames("validation")
    if PROBABILITY_CACHE.is_file():
        return frames, manifest, np.load(PROBABILITY_CACHE, mmap_mode="r")
    samples, _ = load_samples("validation")
    probabilities = np.load(
        ROOT
        / "artifacts"
        / "ai-reliability"
        / "phase3-v2"
        / "models"
        / "g5"
        / "validation-probabilities.npy"
    )
    count = int(manifest["samples"])
    probabilities = probabilities[:count]
    frame_run = np.asarray(frames["run_id"], dtype=np.int64)
    frame_step = np.asarray(frames["step"], dtype=np.int64)
    frame_key = frame_run * 1000 + frame_step
    order = np.argsort(frame_key)
    sorted_key = frame_key[order]
    sample_key = (
        np.asarray(samples["run_id"][:count], dtype=np.int64) * 1000
        + np.asarray(samples["step"][:count], dtype=np.int64)
    )
    positions = np.searchsorted(sorted_key, sample_key)
    if np.any(positions >= len(sorted_key)):
        raise RuntimeError("sample-to-frame mapping overflow")
    frame_index = order[positions]
    if not np.array_equal(sorted_key[positions], sample_key):
        raise RuntimeError("sample-to-frame mapping mismatch")
    matrix = np.full((len(frame_run), N_ANCHORS), np.nan, dtype=np.float32)
    matrix[
        frame_index,
        np.asarray(samples["anchor_index"][:count], dtype=np.int64),
    ] = (1.0 - probabilities).astype(np.float32)
    available_mask = np.asarray(frames["available"], dtype=bool)
    missing = available_mask & ~np.isfinite(matrix)
    if np.any(missing):
        # Packet-loss observations are excluded from supervised training and their
        # cached feature rows are NaN. They remain explicit fallbacks; the policy
        # is never allowed to invent a probability for them.
        finite = available_mask & ~missing
        if not np.any(finite):
            raise RuntimeError("no finite validation probabilities for available anchors")
    PROBABILITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.save(PROBABILITY_CACHE, matrix)
    return frames, manifest, matrix


def _run_indices(run_ids: np.ndarray) -> dict[int, np.ndarray]:
    return {
        int(run_id): np.flatnonzero(run_ids == run_id)
        for run_id in np.unique(run_ids)
    }


def evaluate_candidate(candidate: dict, force: bool = False) -> dict:
    started = perf_counter()
    directory = VALIDATION_OUTPUT / "frames"
    destination = directory / f"{candidate['id']}.npz"
    if destination.is_file() and not force:
        return {"id": candidate["id"], "cached": True}
    frames, manifest, probability_matrix = load_validation_probabilities()
    run_ids = np.asarray(frames["run_id"], dtype=np.int64)
    run_to_indices = _run_indices(run_ids)
    environments = {}
    catalogue = {int(row["run_id"]): row for row in manifest["run_catalog"]}
    total = len(run_ids)
    ai_error = np.full(total, np.nan, dtype=np.float32)
    fallback = np.zeros(total, dtype=bool)
    changed = np.zeros(total, dtype=bool)
    active = np.zeros(total, dtype=bool)
    for run_id, indices in run_to_indices.items():
        row = catalogue[run_id]
        key = (row["environment_id"], row["floor_id"])
        environment = environments.get(key)
        if environment is None:
            environment = resolve_floor(
                load_environments()[row["environment_id"]], row["floor_id"]
            )
            environments[key] = environment
        tracker = MotionTracker()
        for index in indices:
            index = int(index)
            timestamp = round(int(frames["step"][index]) * DT, 8)
            available_indices = np.flatnonzero(frames["available"][index])
            probabilities = probability_matrix[index, available_indices]
            weights, is_fallback, is_changed = policy_weights(
                probabilities, len(available_indices), candidate
            )
            estimate = _robust_estimate(frames, index)
            if weights is not None:
                measurements = _measurements_for_frame(
                    frames, environment, index, timestamp
                )
                anchor_ids = [
                    environment.anchors[int(anchor)].id for anchor in available_indices
                ]
                named_weights = {
                    anchor_id: weights[str(position)]
                    for position, anchor_id in enumerate(anchor_ids)
                }
                estimate = solve(
                    measurements,
                    robust=True,
                    ai_weights=named_weights,
                )
            tracking = tracker.update(estimate, timestamp)
            truth = frames["truth"][index]
            if tracking.position is not None:
                ai_error[index] = float(
                    np.linalg.norm(np.asarray(tracking.position) - truth)
                )
            fallback[index] = is_fallback
            changed[index] = is_changed
            active[index] = weights is not None
    directory.mkdir(parents=True, exist_ok=True)
    np.savez(
        destination,
        ai_error=ai_error,
        fallback=fallback,
        changed=changed,
        active=active,
    )
    return {
        "id": candidate["id"],
        "cached": False,
        "elapsed_seconds": perf_counter() - started,
        "fallback_rate": float(np.mean(fallback)),
        "changed_rate": float(np.mean(changed)),
        "active_rate": float(np.mean(active)),
    }


def _sse_by_scope(
    errors: np.ndarray,
    run_ids: np.ndarray,
    masks: dict[str, np.ndarray],
    run_count: int,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    result = {}
    for name, mask in masks.items():
        valid = mask & np.isfinite(errors)
        sse = np.bincount(
            run_ids[valid],
            weights=np.square(errors[valid].astype(float)),
            minlength=run_count,
        )
        counts = np.bincount(run_ids[valid], minlength=run_count)
        result[name] = (sse, counts)
    return result


def scope_masks(frames: dict, manifest: dict) -> dict[str, np.ndarray]:
    count = np.asarray(frames["count"])
    variant = np.asarray(frames["variant_index"])
    layout = np.asarray(frames["layout_index"])
    available = np.asarray(frames["n_available"])
    n_nlos = np.asarray(frames["n_nlos"])
    dropout = np.asarray(frames["has_dropout"], dtype=bool)
    outage = np.asarray(frames["has_outage"], dtype=bool)
    masks = {"overall": np.ones(len(count), dtype=bool)}
    for value in COUNTS:
        masks[f"anchor_count:{value}"] = count == value
    for index, name in enumerate(manifest["variants"]):
        masks[f"scenario:{name}"] = variant == index
    for index, name in enumerate(manifest["layouts"]):
        masks[f"layout:{name}"] = layout == index
    for index, name in enumerate(manifest["variants"]):
        for value in COUNTS:
            masks[f"scenario_anchor_count:{name}/{value}"] = (variant == index) & (
                count == value
            )
    solvable = available >= 3
    masks["focus:solvable_mixed_fault"] = (
        solvable & (n_nlos > 0) & (dropout | outage)
    )
    masks["focus:solvable_majority_nlos"] = (
        solvable & (n_nlos > available / 2) & (n_nlos < available)
    )
    masks["focus:solvable_all_nlos"] = solvable & (n_nlos == available)
    return masks


def bootstrap_comparison(
    baseline_sse: np.ndarray,
    baseline_count: np.ndarray,
    candidate_sse: np.ndarray,
    candidate_count: np.ndarray,
) -> dict:
    valid = (baseline_count > 0) & (candidate_count > 0)
    baseline_sse = baseline_sse[valid]
    baseline_count = baseline_count[valid]
    candidate_sse = candidate_sse[valid]
    candidate_count = candidate_count[valid]
    if not len(baseline_sse):
        return {
            "baseline_rmse": None,
            "candidate_rmse": None,
            "difference": None,
            "ci_low": None,
            "ci_high": None,
            "runs": 0,
            "frames": 0,
        }
    baseline_rmse = float(np.sqrt(baseline_sse.sum() / baseline_count.sum()))
    candidate_rmse = float(np.sqrt(candidate_sse.sum() / candidate_count.sum()))
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    indices = rng.integers(0, len(baseline_sse), size=(BOOTSTRAP, len(baseline_sse)))
    baseline_samples = np.sqrt(
        baseline_sse[indices].sum(axis=1) / baseline_count[indices].sum(axis=1)
    )
    candidate_samples = np.sqrt(
        candidate_sse[indices].sum(axis=1) / candidate_count[indices].sum(axis=1)
    )
    differences = candidate_samples - baseline_samples
    return {
        "baseline_rmse": baseline_rmse,
        "candidate_rmse": candidate_rmse,
        "difference": candidate_rmse - baseline_rmse,
        "ci_low": float(np.percentile(differences, 2.5)),
        "ci_high": float(np.percentile(differences, 97.5)),
        "runs": int(valid.sum()),
        "frames": int(baseline_count.sum()),
    }


def _label(comparison: dict) -> str:
    if comparison["ci_high"] is None:
        return "unchanged"
    baseline = comparison["baseline_rmse"]
    relative = (
        100.0 * comparison["difference"] / baseline
        if baseline not in (None, 0) and comparison["difference"] is not None
        else None
    )
    if comparison["ci_high"] < 0:
        return "improved"
    if comparison["ci_low"] > 0 and relative is not None and relative > TOLERANCE_PERCENT:
        return "regressed"
    return "unchanged"


def _candidate_frame_arrays(candidate_id: str) -> dict:
    path = VALIDATION_OUTPUT / "frames" / f"{candidate_id}.npz"
    with np.load(path) as data:
        return {key: np.asarray(data[key]) for key in data.files}


def select_policy(force: bool = False) -> dict:
    started = perf_counter()
    frames, manifest, _ = load_validation_probabilities()
    run_ids = np.asarray(frames["run_id"], dtype=np.int64)
    run_count = int(run_ids.max()) + 1
    masks = scope_masks(frames, manifest)
    candidates = candidate_grid()
    for candidate in candidates:
        evaluate_candidate(candidate, force=force)
    arrays = {candidate["id"]: _candidate_frame_arrays(candidate["id"]) for candidate in candidates}
    scoped = {
        candidate_id: _sse_by_scope(
            values["ai_error"], run_ids, masks, run_count
        )
        for candidate_id, values in arrays.items()
    }
    stat_error = np.linalg.norm(
        np.asarray(frames["stat_tracking"]) - np.asarray(frames["truth"]),
        axis=1,
    ).astype(np.float32)
    stat_scoped = _sse_by_scope(stat_error, run_ids, masks, run_count)
    current = CURRENT_POLICY_ID
    focus_scopes = (
        "focus:solvable_mixed_fault",
        "focus:solvable_majority_nlos",
        "focus:solvable_all_nlos",
    )
    candidates_summary = {}
    regression_rows = []
    for candidate in candidates:
        candidate_id = candidate["id"]
        regressions = []
        comparisons = {}
        for key, (sse, count) in scoped[candidate_id].items():
            if not (
                key.startswith("scenario_anchor_count:")
                or key.startswith("anchor_count:")
                or key == "overall"
            ):
                continue
            baseline_sse, baseline_count = stat_scoped[key]
            comparison = bootstrap_comparison(
                baseline_sse, baseline_count, sse, count
            )
            label = _label(comparison)
            comparisons[key] = {**comparison, "label": label}
            if label == "regressed":
                regressions.append(key)
        focus_improvements = {}
        for key in focus_scopes:
            current_sse, current_count = scoped[current][key]
            candidate_sse, candidate_count = scoped[candidate_id][key]
            comparison = bootstrap_comparison(
                current_sse, current_count, candidate_sse, candidate_count
            )
            improvement = (
                comparison["baseline_rmse"] - comparison["candidate_rmse"]
                if comparison["baseline_rmse"] is not None
                else None
            )
            focus_improvements[key] = {
                **comparison,
                "improvement": improvement,
            }
        score_values = [
            value["improvement"]
            for value in focus_improvements.values()
            if value["improvement"] is not None
        ]
        score = float(np.mean(score_values)) if score_values else float("-inf")
        fallback_rate = float(np.mean(arrays[candidate_id]["fallback"]))
        changed_rate = float(np.mean(arrays[candidate_id]["changed"]))
        candidates_summary[candidate_id] = {
            **candidate,
            "disqualified": bool(regressions),
            "regressions": regressions,
            "focus": focus_improvements,
            "score": score,
            "fallback_rate": fallback_rate,
            "changed_rate": changed_rate,
            "active_rate": float(np.mean(arrays[candidate_id]["active"])),
            "comparisons": comparisons,
        }
        for key in regressions:
            regression_rows.append(
                {
                    "candidate": candidate_id,
                    "scope": key,
                    **comparisons[key],
                }
            )
    eligible = [
        candidate
        for candidate in candidates
        if not candidates_summary[candidate["id"]]["disqualified"]
    ]
    winner = None
    for candidate in eligible:
        summary = candidates_summary[candidate["id"]]
        if summary["score"] > 0 and (
            winner is None
            or summary["score"] > candidates_summary[winner["id"]]["score"] + 1e-12
        ):
            winner = candidate
    if winner is None:
        selected = current
    else:
        selected = winner["id"]
    by_count = {}
    for value in COUNTS:
        key = f"anchor_count:{value}"
        mask = masks[key]
        by_count[str(value)] = {
            "frames": int(mask.sum()),
            "fallback_rate": float(np.mean(arrays[selected]["fallback"][mask])),
            "changed_rate": float(np.mean(arrays[selected]["changed"][mask])),
            "active_rate": float(np.mean(arrays[selected]["active"][mask])),
        }
    comparison_summary = {}
    for candidate in candidates:
        summary = candidates_summary[candidate["id"]]
        comparison_summary[candidate["id"]] = {
            "score": summary["score"],
            "fallback_rate": summary["fallback_rate"],
            "changed_rate": summary["changed_rate"],
            "disqualified": summary["disqualified"],
            "regression_scopes": summary["regressions"],
            "focus": {
                key: {
                    "current_rmse": value["baseline_rmse"],
                    "candidate_rmse": value["candidate_rmse"],
                    "improvement": value["improvement"],
                    "ci_low": value["ci_low"],
                    "ci_high": value["ci_high"],
                }
                for key, value in summary["focus"].items()
            },
        }
    payload = {
        "scope": "validation seeds and cached Phase 3 validation frames",
        "rule": (
            "no >+0.5% relative regression with a bootstrap interval entirely above zero "
            "in any scenario x anchor-count slice or anchor count; then largest mean paired "
            "improvement over the current policy on solvable mixed-fault, majority-NLOS and "
            "all-NLOS frames; then lowest fallback rate; then hard before soft and fixed grid order"
        ),
        "tolerance_percent": TOLERANCE_PERCENT,
        "bootstrap_resamples": BOOTSTRAP,
        "confidence_level": 0.95,
        "current_policy": current,
        "eligible": [candidate["id"] for candidate in eligible],
        "selected": selected,
        "candidates": comparison_summary,
        "selected_by_anchor_count": by_count,
        "selected_summary": {
            key: value
            for key, value in candidates_summary[selected].items()
            if key not in {"comparisons"}
        },
        "regressions": regression_rows,
        "elapsed_seconds": perf_counter() - started,
    }
    write_json(VALIDATION_OUTPUT / "selection.json", payload)
    summary_rows = [
        {
            "candidate": candidate["id"],
            "selected": candidate["id"] == selected,
            "score": candidates_summary[candidate["id"]]["score"],
            "fallback_rate": candidates_summary[candidate["id"]]["fallback_rate"],
            "changed_rate": candidates_summary[candidate["id"]]["changed_rate"],
            "active_rate": candidates_summary[candidate["id"]]["active_rate"],
            "disqualified": candidates_summary[candidate["id"]]["disqualified"],
            "regression_scopes": ";".join(candidates_summary[candidate["id"]]["regressions"]),
        }
        for candidate in candidates
    ]
    write_csv(VALIDATION_OUTPUT / "selection.csv", summary_rows)
    chosen = next(candidate for candidate in candidates if candidate["id"] == selected)
    policy = {
        "policy_version": "2.0.0",
        "policy_id": selected,
        "mode": chosen["mode"],
        "floor": chosen["floor"],
        "threshold": chosen["threshold"],
        "fallback_min_predicted_los": 3,
        "fallback_min_predicted_los_when_4plus": 2
        if chosen["fallback"] == "min2_if4"
        else 3,
        "ai_weight_rule": (
            "max(floor, p_los)"
            if chosen["mode"] == "soft"
            else "1 if p_los >= threshold else floor + (1-floor)*p_los/threshold"
        ),
        "final_weight": "statistical_weight * ai_weight",
        "selection_rule": payload["rule"],
        "validation": {
            "path": str((VALIDATION_OUTPUT / "selection.json").relative_to(ROOT)),
            "score": candidates_summary[selected]["score"],
            "fallback_rate": candidates_summary[selected]["fallback_rate"],
            "changed_rate": candidates_summary[selected]["changed_rate"],
            "selected_by_anchor_count": by_count,
        },
    }
    policy["policy_sha256"] = policy_hash(policy)
    write_json(POLICY_PATH, policy)
    return {
        "selected": selected,
        "score": candidates_summary[selected]["score"],
        "fallback_rate": candidates_summary[selected]["fallback_rate"],
        "candidates": len(candidates),
        "policy_sha256": policy["policy_sha256"],
    }


def compare_g5_v1() -> dict:
    """Phase 3 G5 versus v1 under the unchanged v1 policy, by count and scenario."""
    from .ai_phase3 import _load_partial_result

    output = OUTPUT / "comparison"
    v1 = _load_partial_result("v1")
    v2 = _load_partial_result("g5")
    if not np.array_equal(v1["run_id"], v2["run_id"]):
        raise RuntimeError("v1 and v2 partials are not frame-aligned")
    manifest = load_frames("validation")[1]
    rows = []

    def add(scope: str, name: str, mask: np.ndarray) -> None:
        valid = mask & np.isfinite(v1["ai_error"]) & np.isfinite(v2["ai_error"])
        if not np.any(valid):
            return
        sse_v1 = np.bincount(
            v1["run_id"][valid],
            weights=np.square(v1["ai_error"][valid].astype(float)),
            minlength=int(v1["run_id"].max()) + 1,
        )
        n_v1 = np.bincount(v1["run_id"][valid], minlength=int(v1["run_id"].max()) + 1)
        sse_v2 = np.bincount(
            v2["run_id"][valid],
            weights=np.square(v2["ai_error"][valid].astype(float)),
            minlength=int(v1["run_id"].max()) + 1,
        )
        n_v2 = np.bincount(v2["run_id"][valid], minlength=int(v1["run_id"].max()) + 1)
        comparison = bootstrap_comparison(sse_v1, n_v1, sse_v2, n_v2)
        rows.append(
            {
                "scope": scope,
                "name": name,
                **comparison,
                "relative_percent": (
                    100.0 * comparison["difference"] / comparison["baseline_rmse"]
                    if comparison["baseline_rmse"]
                    else None
                ),
                "label": _label(comparison),
            }
        )

    add("overall", "overall", np.ones(len(v1["run_id"]), dtype=bool))
    for count in COUNTS:
        add("anchor_count", str(count), v1["count"] == count)
    for index, name in enumerate(manifest["variants"]):
        add("scenario", name, v1["variant_index"] == index)
        for count in COUNTS:
            add(
                "scenario_anchor_count",
                f"{name}/{count}",
                (v1["variant_index"] == index) & (v1["count"] == count),
            )
    for index, name in enumerate(manifest["layouts"]):
        add("layout", name, v1["layout_index"] == index)
    write_csv(output / "g5-vs-v1.csv", rows)
    labels = {
        label: sum(1 for row in rows if row["label"] == label)
        for label in ("improved", "unchanged", "regressed")
    }
    payload = {
        "scope": "validation frames under the unchanged v1 policy",
        "baseline": "v1",
        "candidate": "g5",
        "bootstrap_resamples": BOOTSTRAP,
        "confidence_level": 0.95,
        "labels": labels,
        "rows": rows,
    }
    write_json(output / "g5-vs-v1.json", payload)
    return {"labels": labels, "rows": len(rows)}


def evaluate_candidates(workers: int = 1, force: bool = False) -> list[dict]:
    load_validation_probabilities()
    candidates = candidate_grid()
    if workers <= 1:
        return [evaluate_candidate(candidate, force=force) for candidate in candidates]
    from multiprocessing import Pool

    with Pool(processes=min(workers, len(candidates))) as pool:
        return pool.map(
            _evaluate_candidate_worker,
            [(candidate, force) for candidate in candidates],
        )


def _evaluate_candidate_worker(payload):
    candidate, force = payload
    return evaluate_candidate(candidate, force=force)


def refresh_policy_hash(path: Path = POLICY_PATH) -> str:
    """Recompute and write the policy_sha256 field after editing threshold/floor values."""
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("policy_sha256", None)
    raw["policy_sha256"] = policy_hash(raw)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return raw["policy_sha256"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--run-candidates", action="store_true")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--refresh-hash",
        action="store_true",
        help="Recompute policy_sha256 after manually editing threshold/floor in policy-v2.json",
    )
    args = parser.parse_args()
    if args.refresh_hash:
        new_hash = refresh_policy_hash()
        print(json.dumps({"policy_sha256": new_hash}))
        return
    if args.compare:
        print(json.dumps(compare_g5_v1(), indent=2, default=_jsonable))
    if args.run_candidates:
        print(
            json.dumps(
                evaluate_candidates(workers=args.workers, force=args.force),
                indent=2,
                default=_jsonable,
            )
        )
    if args.select:
        print(json.dumps(select_policy(force=args.force), indent=2, default=_jsonable))


if __name__ == "__main__":
    main()
