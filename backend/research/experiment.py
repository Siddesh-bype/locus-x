"""Controlled replay; truth is attached only after inference has completed.

Run: python -m backend.research.experiment --split tuning
"""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import numpy as np

from backend.locus_x.benchmark import ANCHOR_SELECTION
from backend.locus_x.environment import load_environments, route_position
from backend.locus_x.schemas import Settings
from backend.locus_x.simulation import generate_measurements
from backend.research.candidates import OriginalTracker, candidate_solve, original

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts/nlos-audit"
PROTOCOL = ROOT / "configs/benchmarks/nlos-audit/protocol.json"
LEVELS = (0.5, 0.8, 0.9, 0.95, 0.99)


def dump_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def error_stats(values):
    values = np.asarray(values, dtype=float)
    return {
        "rmse": float(np.sqrt(np.mean(values**2))) if len(values) else None,
        "mean": float(np.mean(values)) if len(values) else None,
        "median": float(np.median(values)) if len(values) else None,
        "p95": float(np.percentile(values, 95)) if len(values) else None,
    }


def summarize(rows):
    baseline = {(r["scenario"], r["anchors"], r["seed"], r["frame"]): r
                for r in rows if r["method"] == "baseline"}
    groups = defaultdict(list)
    for row in rows:
        groups[row["scenario"], row["anchors"], row["method"]].append(row)
    summaries = []
    for (scenario, count, method), group in groups.items():
        valid = [r for r in group if r["error"] is not None]
        matched = [(r, baseline[scenario, count, r["seed"], r["frame"]]) for r in valid
                   if baseline[scenario, count, r["seed"], r["frame"]]["error"] is not None]
        nees = [r["nees"] for r in valid if r["nees"] is not None]
        raw = [r["raw_error"] for r in group if r["raw_error"] is not None]
        summary = {
            "scenario": scenario, "anchors": count, "method": method,
            "frames": len(group), "valid_frames": len(valid),
            **error_stats([r["error"] for r in valid]),
            "valid_rate": len(valid)/len(group),
            "update_rate": np.mean([r["updated"] for r in group]).item(),
            "nlos_rmse": error_stats([r["error"] for r in valid if r["has_nlos"]])["rmse"],
            "dropout_rmse": error_stats([r["error"] for r in valid if r["has_dropout"]])["rmse"],
            "matched_frames": len(matched),
            **{f"matched_{k}": v for k, v in error_stats([r["error"] for r, _ in matched]).items()},
            "matched_baseline_rmse": error_stats([b["error"] for _, b in matched])["rmse"],
            "matched_delta_mse": float(np.mean([r["error"]**2-b["error"]**2 for r,b in matched]))
            if matched else None,
            "raw_rmse": error_stats(raw)["rmse"],
            "mean_nees": float(np.mean(nees)) if nees else None,
            "nees_p95": float(np.quantile(nees, 0.95)) if nees else None,
            "mean_semimajor_m": float(np.mean([r["semimajor"] for r in valid])) if valid else None,
            "latency_ms": float(np.mean([r["latency_ms"] for r in group])),
            "latency_p95_ms": float(np.quantile([r["latency_ms"] for r in group], 0.95)),
            "bias_fit_rate": float(np.mean([r["bias_fitted"] for r in group])),
        }
        for level in LEVELS:
            threshold = -2 * np.log(1-level)  # Exact chi-square quantile with 2 d.o.f.
            summary[f"coverage_{int(level*100)}"] = float(np.mean(np.array(nees) <= threshold)) if nees else None
        summaries.append(summary)
    return summaries


def run(split, methods):
    protocol = json.loads(PROTOCOL.read_text())
    settings_plan = protocol[split]
    env = load_environments()["hospital_floor_01"]
    all_rows = []
    start = perf_counter()
    for scenario in protocol["scenarios"]:
        for count in protocol["anchor_counts"]:
            for seed in settings_plan["seeds"]:
                settings = Settings(
                    scenario=scenario, seed=seed, route=settings_plan["route"],
                    disabled_anchors=[a.id for a in env.anchors if a.id not in ANCHOR_SELECTION[count]],
                    **settings_plan.get("overrides", {}).get(scenario, {}),
                )
                trackers = {method: OriginalTracker() for method in methods if method != "baseline"}
                trusts = {method: {} for method in methods}
                for frame in range(settings_plan["steps"]+1):
                    timestamp = frame * settings_plan["dt"]
                    truth = route_position(env.routes[settings.route], timestamp, settings.speed)
                    measurements, labels = generate_measurements(env, settings, truth, timestamp, frame)
                    serialized = json.dumps([m.model_dump() for m in measurements], sort_keys=True)
                    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()[:16]
                    for method in methods:
                        if method == "baseline":
                            estimate = original.solve(measurements)
                            result = estimate
                            updated = estimate.position is not None
                            runtime = estimate.runtime_ms
                        else:
                            estimate = candidate_solve(measurements, method, trusts[method])
                            result = trackers[method].update(estimate, timestamp)
                            updated = result.updated
                            runtime = estimate.runtime_ms + result.runtime_ms
                        assert serialized == json.dumps([m.model_dump() for m in measurements], sort_keys=True)
                        if frame == 0:
                            continue  # Initialization never counts as an evaluated frame.
                        # Evaluation begins here. None of these values feed back into inference.
                        error = nees = semimajor = px = py = cxx = cxy = cyy = None
                        if result.position is not None:
                            px, py = result.position
                            delta = np.array(result.position)-truth
                            error = float(np.linalg.norm(delta))
                            covariance = np.array(result.covariance)
                            nees = float(delta @ np.linalg.solve(covariance, delta))
                            semimajor = float(np.sqrt(np.linalg.eigvalsh(covariance).max()*5.9914645471))
                            cxx, cxy, cyy = covariance[0,0], covariance[0,1], covariance[1,1]
                        raw_error = float(np.linalg.norm(np.array(estimate.position)-truth)) if estimate.position is not None else None
                        all_rows.append(dict(
                            split=split, scenario=scenario, anchors=count, seed=seed, frame=frame,
                            t=timestamp, method=method, input_hash=fingerprint,
                            n_ranges=len(measurements), truth_x=truth[0], truth_y=truth[1],
                            x=px, y=py, error=error, raw_error=raw_error, updated=updated,
                            status=result.status, nees=nees, semimajor=semimajor,
                            cov_xx=cxx, cov_xy=cxy, cov_yy=cyy,
                            has_nlos=any(l.kind == "nlos" for l in labels),
                            has_dropout=any(l.kind in ("packet_loss", "outage") for l in labels),
                            bias_fitted="shared range bias fitted" in estimate.reason,
                            latency_ms=runtime,
                        ))
            print(f"{split}: {scenario}/{count} complete", flush=True)
    destination = OUTPUT / split
    destination.mkdir(exist_ok=True)
    summaries = summarize(all_rows)
    dump_csv(destination / "frames.csv", all_rows)
    dump_csv(destination / "metrics.csv", summaries)
    result = dict(split=split, methods=methods, protocol=protocol,
                  elapsed_seconds=perf_counter()-start, rows=summaries)
    (destination / "results.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Saved {len(all_rows)} method/frame rows in {result['elapsed_seconds']:.1f}s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=["tuning", "calibration", "reference", "heldout"], required=True)
    parser.add_argument("--methods", nargs="+", default=["baseline", "legacy", "huber_only", "asymmetric_huber", "common_nonnegative_bias"])
    args = parser.parse_args()
    run(args.split, args.methods)
