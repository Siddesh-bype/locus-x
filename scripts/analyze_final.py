"""Post-test analysis for the frozen LOCUS-X final run.

This script only reads the frozen observations and writes derived tables. It
never regenerates observations or changes the frozen policy.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from backend.locus_x.ai_ablation import metric_row, reliability_bins
from backend.locus_x.ai_final import LAYOUTS, METHODS, _metric_row, final_variants
from backend.locus_x.ai_phase3 import _nlos_group

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "artifacts" / "ai-reliability" / "final-test"
ANALYSIS = FINAL / "analysis"
GROUPS = ("one", "minority", "half", "majority", "all")


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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(payload), indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0])
    lines = [",".join(fields)]
    for row in rows:
        values = []
        for field in fields:
            value = row.get(field)
            text = "" if value is None else str(value)
            if any(character in text for character in ',"\n'):
                text = '"' + text.replace('"', '""') + '"'
            values.append(text)
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _classifier_scopes(samples: dict, variant_names: list[str]) -> list[tuple[str, np.ndarray]]:
    scopes = [
        ("all", np.ones(len(samples["y"]), dtype=bool)),
        ("condition/no_loss", samples["condition"] == "no_loss"),
        ("condition/packet_loss", samples["condition"] == "packet_loss"),
    ]
    for index, (_, _, name) in enumerate(LAYOUTS):
        scopes.append((f"layout/{name}", samples["layout_index"] == index))
    for count in range(3, 9):
        scopes.append((f"count/{count}", samples["count"] == count))
        for condition in ("no_loss", "packet_loss"):
            scopes.append(
                (
                    f"count/{count}/{condition}",
                    (samples["count"] == count) & (samples["condition"] == condition),
                )
            )
    for index, name in enumerate(variant_names):
        scopes.append((f"variant/{name}", samples["variant_index"] == index))
    return scopes


def main() -> None:
    frames = {key: value for key, value in np.load(FINAL / "frames.npz").items()}
    samples = {key: value for key, value in np.load(FINAL / "classifier-samples.npz").items()}
    summary = json.loads((FINAL / "summary.json").read_text(encoding="utf-8"))
    variant_names = [variant["id"] for variant in final_variants()]

    group = _nlos_group(frames["n_nlos"], frames["n_available"])
    group_rows = []
    for index, name in enumerate(GROUPS):
        mask = (group == index) & (frames["n_available"] >= 3)
        for method in METHODS:
            row = _metric_row(frames, name, mask, method)
            row["scope"] = "nlos_group"
            row["name"] = name
            group_rows.append(row)

    fallback_rows = []
    scopes = [
        ("all", "all", np.ones(len(frames["run_id"]), dtype=bool)),
        *[("anchor_count", str(count), frames["count"] == count) for count in range(3, 9)],
        *[
            ("layout", name, frames["layout_index"] == index)
            for index, (_, _, name) in enumerate(LAYOUTS)
        ],
        *[
            ("variant", name, frames["variant_index"] == index)
            for index, name in enumerate(variant_names)
        ],
        (
            "condition",
            "packet_loss",
            frames["has_dropout"].astype(bool),
        ),
        (
            "condition",
            "no_packet_loss",
            ~frames["has_dropout"].astype(bool),
        ),
    ]
    for scope, name, mask in scopes:
        if not np.any(mask):
            continue
        fallback = frames["v2_fallback"][mask].astype(bool)
        changed = frames["v2_changed"][mask].astype(bool)
        fallback_rows.append(
            {
                "scope": scope,
                "name": name,
                "frames": int(np.sum(mask)),
                "fallback": int(np.sum(fallback)),
                "fallback_rate": float(np.mean(fallback)),
                "active_rate": float(np.mean(~fallback)),
                "changed": int(np.sum(changed)),
                "changed_rate": float(np.mean(changed)),
            }
        )

    classifier_rows = []
    for scope, mask in _classifier_scopes(samples, variant_names):
        if not np.any(mask):
            continue
        for model, key in (("v1", "p_v1"), ("v2", "p_v2")):
            classifier_rows.append(
                {
                    "scope": scope,
                    "model": model,
                    **metric_row(samples["y"][mask], samples[key][mask]),
                }
            )

    calibration_rows = []
    for scope, mask in [
        ("all", np.ones(len(samples["y"]), dtype=bool)),
        *[(f"count/{count}", samples["count"] == count) for count in range(3, 9)],
        ("condition/no_loss", samples["condition"] == "no_loss"),
        ("condition/packet_loss", samples["condition"] == "packet_loss"),
    ]:
        if not np.any(mask):
            continue
        for model, key in (("v1", "p_v1"), ("v2", "p_v2")):
            for row in reliability_bins(samples["y"][mask], samples[key][mask]):
                calibration_rows.append({"scope": scope, "model": model, **row})

    _write_csv(ANALYSIS / "nlos-groups.csv", group_rows)
    _write_csv(ANALYSIS / "fallback.csv", fallback_rows)
    _write_csv(ANALYSIS / "classifier.csv", classifier_rows)
    _write_csv(ANALYSIS / "calibration.csv", calibration_rows)
    _write_json(
        ANALYSIS / "summary.json",
        {
            "source": "frozen final-test observations; no regeneration",
            "runs": summary["runs"],
            "frames": summary["frames"],
            "classifier_samples": summary["classifier_samples"],
            "nlos_groups": group_rows,
            "fallback": fallback_rows,
            "classifier": classifier_rows,
            "calibration": calibration_rows,
            "notes": [
                "The frozen summary.json omitted group names and used duplicate "
                "condition scope labels in its derived tables.",
                "This analysis adds names and condition labels without changing "
                "observations, decisions, metrics or the frozen policy.",
            ],
        },
    )
    print(
        json.dumps(
            {
                "groups": len(group_rows),
                "fallback": len(fallback_rows),
                "classifier": len(classifier_rows),
                "calibration": len(calibration_rows),
            }
        )
    )


if __name__ == "__main__":
    main()
