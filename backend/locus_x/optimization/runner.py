"""Deterministic optimizer runner and artifact writer.

CLI:
    python -m backend.locus_x.optimization.runner \
        --environment hospital_floor_01 --budget 8 --output artifacts/anchor-optimizer/run-1

The runner:
  1. resolves the environment/floor and enumerates legal candidate sites,
  2. runs greedy + swap search on the development split,
  3. selects among finalists on the validation split,
  4. builds manual/grid/optimized layouts at the same budget,
  5. evaluates all three on the *held-out* split with the fixed baseline
     estimator, then (optionally) with the LOCUS-X estimator,
  6. writes loadable environment configurations, comparison CSV/JSON and a
     reproducibility manifest. Existing artifact directories are never
     overwritten (same protection rule as the benchmark CLI).

Python API for the dashboard: :func:`run_optimization` returns the same dict
that is written to disk, without touching the filesystem when ``output=None``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from time import perf_counter

from ..building import Building
from ..environment import load_environments
from ..schemas import Environment
from .compare import compare_layouts, layout_floor, reference_layouts
from .layouts import build_environment_config, validate_environment_config
from .optimizer import optimize_placement, validate_splits
from .protocol import (
    OPTIMIZER_PROTOCOL_VERSION,
    ComparisonReport,
    OptimizationResult,
    OptimizerConfig,
    PlacementPolicy,
)


def configuration_hash(configuration: Environment | Building) -> str:
    return hashlib.sha256(configuration.model_dump_json().encode()).hexdigest()


def run_optimization(
    environment_id: str = "hospital_floor_01",
    floor_id: str | None = None,
    budgets: list[int] | None = None,
    config: OptimizerConfig | None = None,
    policy: PlacementPolicy | None = None,
    with_locus_estimator: bool = True,
    catalog: dict | None = None,
    output: Path | None = None,
    progress=None,
) -> dict:
    """Optimize, compare and (optionally) persist results for one floor."""
    start = perf_counter()
    config = config or OptimizerConfig()
    policy = policy or config.policy
    budgets = budgets or [8]
    catalog = catalog or load_environments()
    if environment_id not in catalog:
        raise ValueError(f"Unknown environment: {environment_id}")
    configuration = catalog[environment_id]
    validate_splits(config.development_split, config.validation_split, config.held_out_split)
    resolved_floor = layout_floor(configuration, floor_id)

    estimators = ["baseline", "locus"] if with_locus_estimator else ["baseline"]
    results: dict[int, OptimizationResult] = {}
    reports: dict[tuple[int, str], ComparisonReport] = {}
    configs: dict[tuple[int, str], dict] = {}
    for budget in budgets:
        result = optimize_placement(
            configuration,
            resolved_floor,
            budget=budget,
            policy=policy,
            config=config,
            progress=(
                (lambda step, total, b=budget: progress(f"budget {b}: layout {step}/{total}"))
                if progress
                else None
            ),
        )
        results[budget] = result
        layouts = reference_layouts(configuration, resolved_floor, budget, result.selected, policy)
        for estimator in estimators:
            reports[(budget, estimator)] = compare_layouts(
                configuration,
                resolved_floor,
                layouts,
                config.held_out_split,
                estimator,
                policy,
            )
        for layout in layouts:
            raw = build_environment_config(configuration, resolved_floor, layout)
            validate_environment_config(raw)
            configs[(budget, layout.kind)] = raw

    payload = {
        "optimizer_protocol": OPTIMIZER_PROTOCOL_VERSION,
        "environment_id": environment_id,
        "environment_sha256": configuration_hash(configuration),
        "floor_id": resolved_floor,
        "budgets": budgets,
        "estimators": estimators,
        "held_out_split": config.held_out_split.model_dump(),
        "development_split": config.development_split.model_dump(),
        "validation_split": config.validation_split.model_dump(),
        "policy": policy.model_dump(),
        "optimization": {str(b): results[b].model_dump() for b in budgets},
        "comparisons": {f"{b}/{e}": reports[(b, e)].model_dump() for (b, e) in reports},
        "environment_configs": {f"{b}/{kind}": configs[(b, kind)] for (b, kind) in configs},
        "runtime_seconds": perf_counter() - start,
        "notes": (
            "Selection used development + validation splits only. Held-out rows are "
            "reported once, never fed back. Estimator fixed per comparison table. "
            "Runtime is nondeterministic; layouts and metrics are not."
        ),
    }
    payload["reproducibility"] = {
        "optimizer_protocol": OPTIMIZER_PROTOCOL_VERSION,
        "environment_sha256": payload["environment_sha256"],
        "development_seeds": ",".join(map(str, config.development_split.seeds)),
        "validation_seeds": ",".join(map(str, config.validation_split.seeds)),
        "held_out_seeds": ",".join(map(str, config.held_out_split.seeds)),
    }
    # Layout hashes per budget/kind for the manifest.
    for budget in budgets:
        for kind in ("manual", "grid", "optimized"):
            layout = next(item for item in results[budget].candidates if item.kind == kind)
            payload["reproducibility"][f"layout_hash_{budget}_{kind}"] = layout.layout_hash
    if output is not None:
        write_artifacts(output, payload)
    return payload


def write_artifacts(output: Path, payload: dict) -> None:
    if any((output / name).exists() for name in ("manifest.json", "comparison.csv")):
        raise FileExistsError(
            "Output contains existing optimizer artifacts; choose a new --output directory"
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8"
    )
    rows = [row for report in payload["comparisons"].values() for row in report["rows"]]
    fieldnames = list(rows[0]) if rows else []
    # Flatten per_region_rmse for CSV consumers.
    flat_rows = []
    for row in rows:
        flat = {k: v for k, v in row.items() if k != "per_region_rmse"}
        for region, rmse in sorted(row["per_region_rmse"].items()):
            flat[f"rmse_{region}"] = rmse
        flat_rows.append(flat)
    if flat_rows:
        fieldnames = sorted({key for row in flat_rows for key in row})
        with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_rows)
    for key, raw in payload["environment_configs"].items():
        budget, kind = key.split("/")
        (output / f"environment-{budget}-{kind}.json").write_text(
            json.dumps(raw, indent=2, allow_nan=False), encoding="utf-8"
        )
    (output / "comparison.json").write_text(
        json.dumps(payload["comparisons"], indent=2, allow_nan=False), encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", default="hospital_floor_01")
    parser.add_argument("--floor", default=None)
    parser.add_argument("--budgets", type=int, nargs="+", default=[8])
    parser.add_argument("--protocol", type=Path, default=None, help="Optional OptimizerConfig JSON")
    parser.add_argument(
        "--baseline-only", action="store_true", help="Skip the LOCUS-X estimator comparison"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = (
        OptimizerConfig.model_validate_json(args.protocol.read_text(encoding="utf-8"))
        if args.protocol
        else OptimizerConfig()
    )
    payload = run_optimization(
        environment_id=args.environment,
        floor_id=args.floor,
        budgets=args.budgets,
        config=config,
        with_locus_estimator=not args.baseline_only,
        output=args.output,
        progress=lambda message: print(message, flush=True),
    )
    print(
        f"Optimized {len(args.budgets)} budget(s) in {payload['runtime_seconds']:.1f}s; "
        f"saved manifest, comparison and loadable environments to {args.output}"
    )


if __name__ == "__main__":
    main()
