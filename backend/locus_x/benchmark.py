"""Reproducible evaluation CLI: python -m backend.locus_x.benchmark."""

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import numpy as np

from .building import Building
from .environment import load_environments, resolve_floor
from .schemas import PRESETS, BenchmarkRequest, Environment, Settings
from .session import SimulationSession

ANCHOR_SELECTION = {
    3: ["A1", "A3", "A6"],
    4: ["A1", "A3", "A5", "A7"],
    6: ["A1", "A2", "A3", "A5", "A6", "A7"],
    8: [f"A{i}" for i in range(1, 9)],
}


def summarize(records: list[dict], method: str) -> dict:
    errors = [r[f"{method}_error"] for r in records if r[f"{method}_error"] is not None]
    nlos = [
        r[f"{method}_error"] for r in records if r["has_nlos"] and r[f"{method}_error"] is not None
    ]
    dropout = [
        r[f"{method}_error"]
        for r in records
        if r["has_dropout"] and r[f"{method}_error"] is not None
    ]
    coverage_key = {"proposed": "covered_95", "ai": "ai_covered_95"}.get(method)
    coverage = (
        [r[coverage_key] for r in records if r[coverage_key] is not None]
        if coverage_key is not None
        else []
    )
    return {
        "method": method,
        "frames": len(records),
        "valid_frames": len(errors),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
        "median": float(np.median(errors)) if errors else None,
        "p95": float(np.percentile(errors, 95)) if errors else None,
        "valid_rate": len(errors) / len(records),
        "update_rate": sum(r[f"{method}_updated"] for r in records) / len(records),
        "nlos_rmse": float(np.sqrt(np.mean(np.square(nlos)))) if nlos else None,
        "dropout_rmse": float(np.sqrt(np.mean(np.square(dropout)))) if dropout else None,
        "latency_ms": float(np.mean([r[f"{method}_runtime"] for r in records])),
        "coverage_95": float(np.mean(coverage)) if coverage else None,
    }


def run_benchmark(
    config: BenchmarkRequest, environment: Environment | Building | None = None, progress=None
) -> dict:
    configuration = environment or load_environments()[config.environment_id]
    environment = resolve_floor(configuration, config.floor_id)
    if config.route not in environment.routes:
        raise ValueError("Unknown target route")
    if not {a for count in config.anchor_counts for a in ANCHOR_SELECTION[count]} <= {
        a.id for a in environment.anchors
    }:
        raise ValueError("This benchmark protocol requires its named A1–A8 anchor subsets")
    start = perf_counter()
    rows, traces, distributions = [], {}, {}
    completed = 0
    total = len(config.scenarios) * len(config.anchor_counts) * len(config.seeds) * config.steps
    for scenario in config.scenarios:
        for count in config.anchor_counts:
            records = []
            key = f"{scenario}/{count}"
            for seed_index, seed in enumerate(config.seeds):
                settings = Settings(
                    scenario=scenario,
                    **PRESETS[scenario],
                    seed=seed,
                    route=config.route,
                    disabled_anchors=[
                        a.id for a in environment.anchors if a.id not in ANCHOR_SELECTION[count]
                    ],
                )
                session = SimulationSession(configuration, settings, environment.id)
                trace = []
                for _ in range(config.steps):
                    frame = session.advance(config.dt)
                    record = frame.evaluation.model_dump()
                    record.update(
                        {
                            "baseline_updated": frame.baseline.position is not None,
                            "proposed_updated": frame.tracking.updated,
                            "ai_updated": (
                                frame.ai_tracking.updated
                                if frame.ai_tracking is not None
                                else False
                            ),
                            "baseline_runtime": frame.baseline.runtime_ms,
                            "proposed_runtime": frame.robust.runtime_ms + frame.tracking.runtime_ms,
                            "ai_runtime": (
                                (frame.ai.runtime_ms if frame.ai is not None else 0.0)
                                + (
                                    frame.ai_tracking.runtime_ms
                                    if frame.ai_tracking is not None
                                    else 0.0
                                )
                                + (
                                    frame.ai_metadata.runtime_ms
                                    if frame.ai_metadata is not None
                                    else 0.0
                                )
                            ),
                        }
                    )
                    records.append(record)
                    trace.append(
                        {
                            "t": frame.timestamp,
                            "baseline": record["baseline_error"],
                            "proposed": record["proposed_error"],
                            "ai": record["ai_error"],
                        }
                    )
                    completed += 1
                    if progress and completed % 40 == 0:
                        progress(completed / total)
                if seed_index == 0:
                    traces[key] = trace
            for method in ("baseline", "proposed", "ai"):
                rows.append(
                    {
                        "scenario": scenario,
                        "anchors": count,
                        "seeds": ",".join(map(str, config.seeds)),
                        **summarize(records, method),
                    }
                )
            distributions[key] = {
                method: [
                    float(v) for v in np.quantile(values, np.linspace(0, 1, min(101, len(values))))
                ]
                if values
                else []
                for method in ("baseline", "proposed", "ai")
                if (
                    values := sorted(
                        r[f"{method}_error"] for r in records if r[f"{method}_error"] is not None
                    )
                )
                is not None
            }
    if progress:
        progress(1.0)
    return {
        "config": config.model_dump()
        if isinstance(configuration, Building)
        else config.model_dump(exclude={"environment_id", "floor_id"}),
        "environment_id": configuration.id,
        "environment_sha256": hashlib.sha256(configuration.model_dump_json().encode()).hexdigest(),
        **(
            {
                "result_version": "3.0",
                "environment_version": configuration.version,
                "floor_id": environment.id,
                "localization_mode": "2d_on_known_floor",
            }
            if isinstance(configuration, Building)
            else {}
        ),
        "rows": rows,
        "traces": traces,
        "distributions": distributions,
        "elapsed_seconds": perf_counter() - start,
        "notes": (
            "Synthetic held-out route and seeds. Runtime is nondeterministic. "
            "Coverage is empirical, not a calibration claim. "
            "Trace uses first seed; metrics use all seeds."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/benchmarks/competition.json"))
    parser.add_argument(
        "--output", type=Path, help="New output directory; existing results are protected"
    )
    args = parser.parse_args()
    config = BenchmarkRequest.model_validate_json(args.config.read_text(encoding="utf-8"))
    if args.output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = Path("artifacts/benchmark-runs") / f"{config.environment_id}-{stamp}"
    if any((args.output / name).exists() for name in ("results.json", "metrics.csv")):
        parser.error(
            "Output contains existing experiment artifacts; choose a new --output directory"
        )
    result = run_benchmark(config)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
    )
    with (args.output / "metrics.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(result["rows"][0]))
        writer.writeheader()
        writer.writerows(result["rows"])
    print(
        f"Evaluated {len(result['rows'])} method/scenario/count rows "
        f"in {result['elapsed_seconds']:.1f}s"
    )
    print(f"Saved {args.output / 'metrics.csv'} and results.json")


if __name__ == "__main__":
    main()
