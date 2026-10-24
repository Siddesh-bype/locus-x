"""Full placement experiment for the LOCUS-X anchor optimizer.

Runs the frozen protocol 1.0.0 end-to-end:
  * V1 hospital_floor_01: budgets 3, 4, 6, 8
  * V3 hospital_v3 / ground: budget 8
Writes versioned artifacts under artifacts/anchor-optimizer/ (gitignored) and
installs example environment configurations under configs/environments/layouts/
for the dashboard. Never touches existing configurations or benchmark artifacts.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\priya\Desktop\locus-x-main")
sys.path.insert(0, str(ROOT))

from backend.locus_x.optimization import run_optimization  # noqa: E402
from backend.locus_x.optimization.protocol import OptimizerConfig  # noqa: E402

config = OptimizerConfig()  # frozen protocol defaults
artifacts = ROOT / "artifacts" / "anchor-optimizer"
layouts_dir = ROOT / "configs" / "environments" / "layouts"
layouts_dir.mkdir(parents=True, exist_ok=True)

runs = [
    ("v1-k3", {"environment_id": "hospital_floor_01", "budgets": [3]}),
    ("v1-k4", {"environment_id": "hospital_floor_01", "budgets": [4]}),
    ("v1-k6", {"environment_id": "hospital_floor_01", "budgets": [6]}),
    ("v1-k8", {"environment_id": "hospital_floor_01", "budgets": [8]}),
    ("v3-ground-k8", {"environment_id": "hospital_v3", "floor_id": "ground", "budgets": [8]}),
]

summary = {}
for name, kwargs in runs:
    output = artifacts / name
    if (output / "manifest.json").exists():
        # Completed historical run: keep it, reload its payload for the summary.
        print(f"=== {name} (cached, manifest present — not overwritten) ===", flush=True)
        payload = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    else:
        if output.exists():
            # Partial output from a crashed run: safe to clear.
            shutil.rmtree(output)
        print(f"=== {name} ===", flush=True)
        payload = run_optimization(config=config, output=output, **kwargs)
    summary[name] = {
        "runtime_seconds": round(payload["runtime_seconds"], 1),
        "selected": {
            str(b): {
                "layout_id": payload["optimization"][str(b)]["selected"]["layout_id"],
                "site_ids": payload["optimization"][str(b)]["selected"]["site_ids"],
                "objective_total": payload["optimization"][str(b)]["selected"]["objective"][
                    "total"
                ],
            }
            for b in payload["budgets"]
        },
        "held_out_pooled_baseline": {
            f"{row['budget']}/{row['layout']}": row["rmse"]
            for row in payload["comparisons"][f"{payload['budgets'][0]}/baseline"]["rows"]
            if row["scenario"] == "pooled"
        },
        "held_out_pooled_locus": {
            f"{row['budget']}/{row['layout']}": row["rmse"]
            for row in payload["comparisons"][f"{payload['budgets'][0]}/locus"]["rows"]
            if row["scenario"] == "pooled"
        },
    }
    # Install example configs for the dashboard (never overwriting).
    for budget_kind, raw in payload["environment_configs"].items():
        budget, kind = budget_kind.split("/")
        stem = f"{payload['environment_id']}-{payload['floor_id']}-{kind}-k{budget}"
        target = layouts_dir / f"{stem}.json"
        if target.exists():
            # Never overwrite an installed configuration; the run is
            # deterministic, so an existing file is the same bytes anyway.
            print(f"  kept existing {target.name}", flush=True)
            continue
        target.write_text(json.dumps(raw, indent=2, allow_nan=False), encoding="utf-8")
        print(f"  wrote {target.name}", flush=True)
    print(
        f"  runtime {summary[name]['runtime_seconds']}s, selected {summary[name]['selected']}",
        flush=True,
    )
    print(
        f"  held-out pooled baseline RMSE: {summary[name]['held_out_pooled_baseline']}",
        flush=True,
    )
    print(f"  held-out pooled locus RMSE: {summary[name]['held_out_pooled_locus']}", flush=True)

(artifacts / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print("DONE", flush=True)
