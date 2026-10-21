"""Reproduce the known failure without changing the production algorithm."""

import csv
import json
from pathlib import Path

import numpy as np

from backend.locus_x.benchmark import ANCHOR_SELECTION
from backend.locus_x.environment import load_environments
from backend.locus_x.schemas import Settings
from backend.locus_x.session import SimulationSession


def main():
    output = Path("artifacts/nlos-audit")
    env = load_environments()["hospital_floor_01"]
    rows = []
    summary = []
    reference = json.loads((output / "reference/results.json").read_text())
    for scenario in ("noise", "nlos", "chaos"):
        for count in (3, 4, 6, 8):
            group = []
            for seed in (101, 202):
                session = SimulationSession(env, Settings(
                    scenario=scenario, seed=seed, route="held_out",
                    disabled_anchors=[a.id for a in env.anchors
                                      if a.id not in ANCHOR_SELECTION[count]],
                ))
                for _ in range(160):
                    f = session.advance(0.5)
                    truth = np.array(f.evaluation.ground_truth)
                    available = [label for label in f.simulator_labels if label.available]
                    row = dict(scenario=scenario, anchors=count, seed=seed,
                               frame=f.frame_index, t=f.timestamp,
                               n_ranges=len(f.measurements),
                               biased_fraction=np.mean([x.injected_bias > 0 for x in available])
                               if available else None,
                               mean_injected_bias=np.mean([x.injected_bias for x in available])
                               if available else None,
                               baseline=f.evaluation.baseline_error,
                               proposed=f.evaluation.proposed_error,
                               raw_robust=None, raw_nees=None, tracked_nees=None,
                               raw_x=None, raw_y=None, tracked_x=None, tracked_y=None,
                               mean_residual=None, los_weight=None, nlos_weight=None,
                               updated=f.tracking.updated)
                    for prefix, estimate in (("raw", f.robust), ("tracked", f.tracking)):
                        if estimate.position is not None:
                            delta = np.array(estimate.position) - truth
                            row[f"{prefix}_x"], row[f"{prefix}_y"] = delta
                            row[f"{prefix}_nees"] = float(
                                delta @ np.linalg.solve(estimate.covariance, delta))
                            if prefix == "raw":
                                row["raw_robust"] = float(np.linalg.norm(delta))
                    if f.robust.residuals:
                        row["mean_residual"] = float(np.mean(list(f.robust.residuals.values())))
                        for kind in ("los", "nlos"):
                            weights = [f.robust.reliability[l.anchor_id] for l in available
                                       if l.kind == kind]
                            row[f"{kind}_weight"] = float(np.mean(weights)) if weights else None
                    group.append(row)
            result = dict(scenario=scenario, anchors=count)
            for name in ("baseline", "raw_robust", "proposed"):
                values = [r[name] for r in group if r[name] is not None]
                result[f"{name}_rmse"] = float(np.sqrt(np.mean(np.square(values))))
            for name in ("raw_nees", "tracked_nees", "biased_fraction", "mean_injected_bias",
                         "mean_residual", "los_weight", "nlos_weight", "updated"):
                values = [r[name] for r in group if r[name] is not None]
                result[name] = float(np.mean(values)) if values else None
            for prefix in ("raw", "tracked"):
                nees = [r[f"{prefix}_nees"] for r in group if r[f"{prefix}_nees"] is not None]
                result[f"{prefix}_coverage"] = float(np.mean(np.array(nees) <= 5.9914645471))
            for method in ("baseline", "proposed"):
                expected = next(r for r in reference["rows"] if r["scenario"] == scenario
                                and r["anchors"] == count and r["method"] == method)
                assert abs(result[f"{method}_rmse"] - expected["rmse"]) < 1e-9
            summary.append(result)
            rows.extend(group)
            print(json.dumps(result), flush=True)
    with (output / "diagnosis-frames.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "diagnosis.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
