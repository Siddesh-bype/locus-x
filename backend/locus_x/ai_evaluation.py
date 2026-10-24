"""Controlled three-method evaluation for LOCUS-X AI reliability."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import numpy as np

from .benchmark import ANCHOR_SELECTION, summarize
from .environment import load_environments, resolve_floor
from .reliability import ReliabilityModel
from .schemas import Settings
from .session import SimulationSession

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT / "models" / "ai-reliability-v1"
DEFAULT_OUTPUT = ROOT / "artifacts" / "ai-reliability" / "final-evaluation"
METHODS = ("baseline", "proposed", "ai")


def _variant(
    variant_id: str,
    noise_std: float,
    nlos_bias: float = 0.0,
    dropout: float = 0.0,
    outage: bool = False,
) -> dict:
    if outage:
        scenario = "chaos"
    elif dropout > 0:
        scenario = "packet_loss"
    elif nlos_bias > 0:
        scenario = "nlos"
    elif noise_std >= 1.0:
        scenario = "high_noise"
    elif noise_std > 0:
        scenario = "noise"
    else:
        scenario = "clean"
    return {
        "id": variant_id,
        "scenario": scenario,
        "noise_std": noise_std,
        "nlos_bias": nlos_bias,
        "dropout": dropout,
        "outage": outage,
        "walls_enabled": True,
    }


def base_variants() -> list[dict]:
    return [
        _variant("clean", 0.0),
        _variant("noise", 0.4),
        _variant("high_noise", 1.2),
        _variant("nlos", 0.15, 2.5),
        _variant("packet_loss", 0.2, 0.0, 0.4),
        _variant("anchor_outage", 0.2, 0.0, 0.0, True),
        _variant("chaos", 0.65, 4.0, 0.25, True),
    ]


def mixed_variants() -> list[dict]:
    return [
        _variant("noise_nlos", 0.4, 2.5),
        _variant("noise_packet", 0.4, 0.0, 0.4),
        _variant("noise_outage", 0.4, 0.0, 0.0, True),
        _variant("nlos_packet", 0.15, 2.5, 0.4),
        _variant("nlos_outage", 0.15, 2.5, 0.0, True),
        _variant("packet_outage", 0.2, 0.0, 0.4, True),
        _variant("noise_nlos_packet", 0.4, 2.5, 0.4),
        _variant("noise_nlos_outage", 0.4, 2.5, 0.0, True),
        _variant("noise_packet_outage", 0.4, 0.0, 0.4, True),
        _variant("nlos_packet_outage", 0.15, 2.5, 0.4, True),
        _variant("all_four", 0.65, 4.0, 0.25, True),
    ]


def _settings(variant: dict, seed: int, route: str, disabled: list[str]) -> Settings:
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


def run_session(
    environment,
    settings: Settings,
    model: ReliabilityModel,
    steps: int,
    dt: float = 0.5,
) -> list[dict]:
    session = SimulationSession(environment, settings)
    session.ai_model = model
    session.restart()
    records = []
    for _ in range(steps):
        frame = session.advance(dt)
        labels = frame.simulator_labels
        n_nlos = sum(label.kind == "nlos" for label in labels)
        n_available = len(frame.measurements)
        record = {
            "timestamp": frame.timestamp,
            "baseline_error": frame.evaluation.baseline_error,
            "proposed_error": frame.evaluation.proposed_error,
            "ai_error": frame.evaluation.ai_error,
            "baseline_updated": frame.baseline.position is not None,
            "proposed_updated": frame.tracking.updated,
            "ai_updated": (frame.ai_tracking.updated if frame.ai_tracking is not None else False),
            "baseline_runtime": frame.baseline.runtime_ms,
            "proposed_runtime": frame.robust.runtime_ms + frame.tracking.runtime_ms,
            "ai_runtime": (
                (frame.ai.runtime_ms if frame.ai is not None else 0.0)
                + (frame.ai_tracking.runtime_ms if frame.ai_tracking is not None else 0.0)
                + (frame.ai_metadata.runtime_ms if frame.ai_metadata is not None else 0.0)
            ),
            "covered_95": frame.evaluation.covered_95,
            "ai_covered_95": frame.evaluation.ai_covered_95,
            "has_nlos": frame.evaluation.has_nlos,
            "has_dropout": frame.evaluation.has_dropout,
            "n_nlos": n_nlos,
            "n_available": n_available,
            "ai_ambiguous": bool(
                frame.ai_metadata.ambiguous if frame.ai_metadata is not None else False
            ),
            "ai_predicted_nlos": (
                frame.ai_metadata.n_predicted_nlos if frame.ai_metadata is not None else 0
            ),
        }
        records.append(record)
    return records


def evaluate_protocol(
    variants: list[dict],
    environment_id: str = "hospital_floor_01",
    floor_id: str | None = None,
    route: str = "held_out",
    seeds: tuple[int, ...] = (303, 404),
    counts: tuple[int, ...] = (3, 4, 6, 8),
    steps: int = 120,
    dt: float = 0.5,
    model: ReliabilityModel | None = None,
) -> dict:
    model = model or ReliabilityModel.load()
    environments = load_environments()
    environment = resolve_floor(environments[environment_id], floor_id)
    all_records = []
    rows = []
    started = perf_counter()
    for variant in variants:
        for count in counts:
            records = []
            for seed in seeds:
                disabled = [
                    anchor.id
                    for anchor in environment.anchors
                    if anchor.id not in ANCHOR_SELECTION[count]
                ]
                settings = _settings(variant, seed, route, disabled)
                run_records = run_session(environment, settings, model, steps, dt)
                for record in run_records:
                    record.update(
                        {
                            "variant": variant["id"],
                            "scenario": variant["scenario"],
                            "count": count,
                            "seed": seed,
                        }
                    )
                records.extend(run_records)
                all_records.extend(run_records)
            for method in METHODS:
                rows.append(
                    {
                        "variant": variant["id"],
                        "scenario": variant["scenario"],
                        "anchors": count,
                        "seeds": ",".join(map(str, seeds)),
                        **summarize(records, method),
                    }
                )
    return {
        "environment_id": environment_id,
        "floor_id": environment.id,
        "route": route,
        "seeds": list(seeds),
        "steps": steps,
        "dt": dt,
        "rows": rows,
        "records": all_records,
        "elapsed_seconds": perf_counter() - started,
    }


def _group_summary(records: list[dict], method: str) -> dict:
    errors = [
        record[f"{method}_error"] for record in records if record[f"{method}_error"] is not None
    ]
    return {
        "frames": len(records),
        "valid_frames": len(errors),
        "rmse": float(np.sqrt(np.mean(np.square(errors)))) if errors else None,
        "median": float(np.median(errors)) if errors else None,
        "p95": float(np.percentile(errors, 95)) if errors else None,
        "valid_rate": len(errors) / len(records) if records else None,
    }


def majority_bias_analysis(records: list[dict]) -> list[dict]:
    groups = {
        "one_nlos": lambda r: r["n_nlos"] == 1,
        "minority_nlos": lambda r: 0 < r["n_nlos"] < r["n_available"] / 2,
        "half_nlos": lambda r: r["n_available"] >= 3 and r["n_nlos"] * 2 == r["n_available"],
        "majority_nlos": lambda r: r["n_available"] >= 3 and r["n_nlos"] > r["n_available"] / 2,
        "all_nlos": lambda r: r["n_available"] >= 3 and r["n_nlos"] == r["n_available"],
        "three_or_four_anchors": lambda r: r["n_available"] in (3, 4) and r["has_nlos"],
    }
    rows = []
    for name, predicate in groups.items():
        selected = [record for record in records if predicate(record)]
        if not selected:
            continue
        row = {
            "group": name,
            "frames": len(selected),
            "mean_available": float(np.mean([r["n_available"] for r in selected])),
            "mean_nlos": float(np.mean([r["n_nlos"] for r in selected])),
            "ambiguous_rate": float(np.mean([r["ai_ambiguous"] for r in selected])),
        }
        for method in METHODS:
            row.update(
                {
                    f"{method}_{key}": value
                    for key, value in _group_summary(selected, method).items()
                }
            )
        rows.append(row)
    return rows


def _relative_improvement(row: dict) -> float | None:
    if not row["proposed"]["rmse"] or row["ai"]["rmse"] is None:
        return None
    return (row["proposed"]["rmse"] - row["ai"]["rmse"]) / row["proposed"]["rmse"]


def tune_policy(
    model: ReliabilityModel,
    output: Path,
    steps: int = 60,
    seeds: tuple[int, ...] = (11, 22),
    counts: tuple[int, ...] = (3, 8),
) -> dict:
    variants = [
        variant
        for variant in base_variants()
        if variant["id"] in ("noise", "high_noise", "nlos", "packet_loss", "chaos")
    ]
    candidates = [
        {"floor": 0.05, "threshold": 0.05},
        {"floor": 0.08, "threshold": 0.10},
        {"floor": 0.08, "threshold": 0.15},
        {"floor": 0.15, "threshold": 0.20},
        {"floor": 0.08, "threshold": 0.25},
        {"floor": 0.20, "threshold": 0.25},
        {"floor": 0.08, "threshold": 0.35},
    ]
    results = []
    for candidate in candidates:
        policy_model = model.with_policy(candidate["floor"], candidate["threshold"])
        evaluation = evaluate_protocol(
            variants,
            environment_id="hospital_floor_01",
            route="held_out",
            seeds=seeds,
            counts=counts,
            steps=steps,
            model=policy_model,
        )
        by_key = defaultdict(dict)
        for row in evaluation["rows"]:
            by_key[(row["variant"], row["anchors"])][row["method"]] = row
        improvements = [
            _relative_improvement(rows)
            for rows in by_key.values()
            if _relative_improvement(rows) is not None
        ]
        results.append(
            {
                **candidate,
                "mean_relative_improvement": float(np.mean(improvements)),
                "worst_relative_improvement": float(np.min(improvements)),
                "rows": evaluation["rows"],
            }
        )
    best = max(results, key=lambda item: item["mean_relative_improvement"])
    summary = {
        "selection_scope": "validation seeds 11,22; legacy held_out route; 3 and 8 anchors",
        "candidates": [
            {key: value for key, value in item.items() if key != "rows"} for item in results
        ],
        "selected": {
            key: best[key]
            for key in (
                "floor",
                "threshold",
                "mean_relative_improvement",
                "worst_relative_improvement",
            )
        },
        "diagnostic_rows": best["rows"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def apply_policy(model_dir: Path, floor: float, threshold: float) -> dict:
    config_path = model_dir / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["weight_policy"]["floor"] = float(floor)
    config["weight_policy"]["threshold"] = float(threshold)
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


def _write_metrics_csv(path: Path, rows: list[dict]):
    fields = list(rows[0])
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def run_final_evaluation(
    model: ReliabilityModel,
    output: Path,
    steps: int = 120,
) -> dict:
    variants = base_variants() + mixed_variants()
    primary = evaluate_protocol(
        variants,
        environment_id="hospital_floor_01",
        route="held_out",
        seeds=(303, 404),
        counts=(3, 4, 6, 8),
        steps=steps,
        model=model,
    )
    layout = evaluate_protocol(
        variants,
        environment_id="hospital_v3",
        floor_id="second",
        route="held_out",
        seeds=(303, 404),
        counts=(3, 4, 6, 8),
        steps=max(60, steps // 2),
        model=model,
    )
    majority = majority_bias_analysis(primary["records"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(
        json.dumps(
            {
                "primary": {key: value for key, value in primary.items() if key != "records"},
                "layout_generalization": {
                    key: value for key, value in layout.items() if key != "records"
                },
                "majority_bias": majority,
                "method": "baseline, statistical LOCUS-X, AI-assisted LOCUS-X",
                "model_version": model.version,
                "feature_version": model.feature_version,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_metrics_csv(output / "metrics.csv", primary["rows"])
    _write_metrics_csv(output / "layout-generalization.csv", layout["rows"])
    _write_metrics_csv(output / "majority-bias.csv", majority)
    return {
        "primary": primary,
        "layout_generalization": layout,
        "majority_bias": majority,
    }


def main():
    model_dir = DEFAULT_MODEL_DIR
    model = ReliabilityModel.load(model_dir)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tune-policy", action="store_true")
    parser.add_argument("--apply-policy", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--steps", type=int, default=120)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.tune_policy:
        summary = tune_policy(model, args.output.parent / "policy-tuning.json")
        print(json.dumps(summary["selected"], indent=2))
        if args.apply_policy:
            apply_policy(
                model_dir,
                summary["selected"]["floor"],
                summary["selected"]["threshold"],
            )
            print(f"Applied policy to {model_dir / 'config.json'}")
    if args.evaluate:
        tuned = ReliabilityModel.load(model_dir)
        result = run_final_evaluation(tuned, args.output, args.steps)
        print(
            json.dumps(
                {
                    "primary_rows": len(result["primary"]["rows"]),
                    "layout_rows": len(result["layout_generalization"]["rows"]),
                    "elapsed_primary": result["primary"]["elapsed_seconds"],
                    "elapsed_layout": result["layout_generalization"]["elapsed_seconds"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
