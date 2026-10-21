"""DIAGNOSTIC ONLY, NOT A FEATURE.

Phase 2 packet-gap and calibration diagnostics use simulator truth for scoring
only. This module is intentionally separate from inference: ``reliability.py``,
``session.py`` and the API must never import it. No indicator or other Phase 2
diagnostic is added to the v1 feature schema.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from .ai_ablation import (
    FEATURE_NAMES,
    OUTPUT,
    load_phase2_manifest,
    load_phase2_split,
    metric_row,
    reliability_bins,
    write_csv,
    write_json,
)
from .reliability import ReliabilityModel

V1_MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "ai-reliability-v1"


def _v1_probabilities(matrix: np.ndarray) -> np.ndarray:
    model = ReliabilityModel.load(V1_MODEL_DIR)
    if not model.available:
        raise RuntimeError("Frozen v1 model is unavailable")
    normalized = (matrix - model.mean) / model.scale
    logits = model._forward(normalized)
    slope, intercept = model.calibration
    return 1.0 / (1.0 + np.exp(-np.clip(slope * logits + intercept, -60, 60)))


def _bucket_gap(gap: np.ndarray) -> np.ndarray:
    result = np.full(len(gap), "4+", dtype=object)
    result[gap == 0] = "0"
    result[gap == 1] = "1"
    result[(gap >= 2) & (gap <= 3)] = "2-3"
    return result


def _metrics_for_mask(y: np.ndarray, probabilities: np.ndarray, mask: np.ndarray) -> dict:
    row = metric_row(y[mask], probabilities[mask])
    row["frames"] = int(np.sum(mask))
    row["mean_predicted_nlos"] = float(np.mean(probabilities[mask]))
    row["true_nlos_rate"] = float(np.mean(y[mask]))
    row["false_los_rate_among_nlos"] = float(
        np.mean(probabilities[mask][y[mask] == 1] < 0.5)
    ) if np.any(y[mask] == 1) else None
    return row


def _stratified_ece(
    y: np.ndarray,
    probabilities: np.ndarray,
    strata: np.ndarray,
    weights: np.ndarray | None = None,
) -> tuple[float | None, list[dict]]:
    rows = []
    total = 0.0
    total_weight = 0.0
    for value in sorted(set(strata.tolist())):
        mask = strata == value
        if not np.any(mask):
            continue
        ece = metric_row(y[mask], probabilities[mask])["ece"]
        weight = float(np.mean(weights[mask])) if weights is not None else float(np.mean(mask))
        rows.append(
            {
                "stratum": str(value),
                "frames": int(np.sum(mask)),
                "ece": ece,
                "weight": weight,
            }
        )
        total += ece * weight
        total_weight += weight
    return (total / total_weight if total_weight else None), rows


def _svg_reliability(bins: list[dict], title: str) -> str:
    width, height = 520, 420
    margin = 55
    points = [
        (float(bin["confidence"]), float(bin["frequency"]))
        for bin in bins
        if bin["count"] and bin["confidence"] is not None
    ]
    def x(value):
        return margin + value * (width - 2 * margin)
    def y(value):
        return height - margin - value * (height - 2 * margin)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        (
            f'<text x="{width/2}" y="24" text-anchor="middle" '
            f'font-family="sans-serif" font-size="16">{title}</text>'
        ),
        (
            f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(1)}" '
            'stroke="#999" stroke-dasharray="4 4"/>'
        ),
        f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(0)}" stroke="#333"/>',
        f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(0)}" y2="{y(1)}" stroke="#333"/>',
        (
            f'<text x="{width/2}" y="{height-12}" text-anchor="middle" '
            'font-family="sans-serif" font-size="12">mean predicted P(NLOS)</text>'
        ),
        (
            f'<text x="15" y="{height/2}" transform="rotate(-90 15 {height/2})" '
            'text-anchor="middle" font-family="sans-serif" font-size="12">'
            'observed NLOS rate</text>'
        ),
    ]
    for confidence, frequency in points:
        parts.append(
            f'<circle cx="{x(confidence):.1f}" cy="{y(frequency):.1f}" '
            'r="4" fill="#2563eb"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def run_diagnostics() -> dict:
    data = load_phase2_split("validation")
    manifest = load_phase2_manifest("validation")
    X = data["X"].astype(np.float64)
    y = data["y"].astype(int)
    probabilities = _v1_probabilities(X)
    gap = data["frames_since_gap"].astype(int)
    gap_bucket = _bucket_gap(gap)
    dropout = data["has_dropout"].astype(bool)
    available = data["available_count"].astype(int)
    layout_names = manifest["layouts"]
    layout_index = data["layout_index"].astype(int)

    packet_rows = []
    for bucket in ("0", "1", "2-3", "4+"):
        mask = gap_bucket == bucket
        row = {"gap_bucket": bucket, **_metrics_for_mask(y, probabilities, mask)}
        packet_rows.append(row)
    write_csv(OUTPUT / "diagnostics" / "packet-gap-buckets.csv", packet_rows)

    gap_summary = []
    for bucket in ("0", "1", "2-3", "4+"):
        mask = gap_bucket == bucket
        nlos_mask = mask & (y == 1)
        gap_summary.append(
            {
                "gap_bucket": bucket,
                "frames": int(np.sum(mask)),
                "nlos_frames": int(np.sum(nlos_mask)),
                "mean_predicted_nlos": float(np.mean(probabilities[mask])),
                "true_nlos_rate": float(np.mean(y[mask])),
                "mean_predicted_nlos_when_true_nlos": (
                    float(np.mean(probabilities[nlos_mask])) if np.any(nlos_mask) else None
                ),
                "false_los_rate_among_nlos": (
                    float(np.mean(probabilities[nlos_mask] < 0.5)) if np.any(nlos_mask) else None
                ),
            }
        )
    reset_mask = (gap >= 1) & (
        np.isclose(X[:, FEATURE_NAMES.index("previous_residual_norm")], 0.0)
    )
    write_json(
        OUTPUT / "diagnostics" / "packet-gap-summary.json",
        {
            "diagnostic_only": True,
            "note": "Truth is used only to score; no feature or calibrator was changed.",
            "buckets": (
                "frames since the same anchor's previous observation: "
                "0=first/no prior, 1=adjacent, 2-3, 4+"
            ),
            "buckets_table": gap_summary,
            "first_observation_frames": int(np.sum(gap == 0)),
            "post_gap_frames": int(np.sum(gap >= 1)),
            "zero_previous_residual_after_gap": int(np.sum(reset_mask)),
            "zero_previous_residual_after_gap_rate": float(
                np.sum(reset_mask) / max(1, np.sum(gap >= 1))
            ),
            "over_trust_flag": {
                "definition": "true NLOS with predicted p(NLOS) < 0.5",
                "fraction_of_nlos_missed_after_gap": float(
                    np.mean(probabilities[(gap >= 1) & (y == 1)] < 0.5)
                ) if np.any((gap >= 1) & (y == 1)) else None,
                "mean_predicted_nlos_on_true_nlos_after_gap": float(
                    np.mean(probabilities[(gap >= 1) & (y == 1)])
                ) if np.any((gap >= 1) & (y == 1)) else None,
            },
        },
    )

    no_loss = ~dropout
    packet_loss = dropout
    reliability = []
    for name, mask in (("no_loss", no_loss), ("packet_loss", packet_loss)):
        bins = reliability_bins(y[mask], probabilities[mask])
        metrics = metric_row(y[mask], probabilities[mask])
        reliability.append(
            {
                "condition": name,
                "frames": int(np.sum(mask)),
                "metrics": metrics,
                "bins": bins,
            }
        )
        (OUTPUT / "diagnostics" / f"reliability-{name}.svg").write_text(
            _svg_reliability(bins, f"Reliability: {name.replace('_', ' ')}"),
            encoding="utf-8",
        )
    write_json(OUTPUT / "diagnostics" / "reliability.json", reliability)
    write_csv(
        OUTPUT / "diagnostics" / "reliability-bins.csv",
        [
            {"condition": item["condition"], **bin}
            for item in reliability
            for bin in item["bins"]
        ],
    )

    available_strata = available
    gap_strata = gap_bucket
    geometry = X[:, FEATURE_NAMES.index("condition_log")]
    geometry_strata = np.digitize(
        geometry, np.quantile(geometry, [0.2, 0.4, 0.6, 0.8])
    )
    stratifications = []
    for name, strata in (
        ("available_anchors", available_strata),
        ("frames_since_gap", gap_strata),
        ("geometry_quintile", geometry_strata),
    ):
        no_loss_ece, no_loss_rows = _stratified_ece(
            y[no_loss], probabilities[no_loss], strata[no_loss]
        )
        packet_ece, packet_rows = _stratified_ece(
            y[packet_loss], probabilities[packet_loss], strata[packet_loss]
        )
        # Reweight no-loss ECE to the packet-loss stratum distribution.
        packet_weights = {
            str(value): float(np.mean(strata[packet_loss] == value))
            for value in sorted(set(strata[packet_loss].tolist()))
        }
        weights_no_loss = np.asarray(
            [packet_weights.get(str(value), 0.0) for value in strata[no_loss]]
        )
        reweighted_ece, reweighted_rows = _stratified_ece(
            y[no_loss], probabilities[no_loss], strata[no_loss], weights_no_loss
        )
        stratifications.append(
            {
                "stratifier": name,
                "no_loss_ece": no_loss_ece,
                "packet_loss_ece": packet_ece,
                "no_loss_reweighted_to_packet": reweighted_ece,
                "no_loss_rows": no_loss_rows,
                "packet_loss_rows": packet_rows,
                "reweighted_rows": reweighted_rows,
            }
        )

    # Layout-aware check: is the shift explained by which layouts carry drops?
    layout_rows = []
    for layout_value, layout_name in enumerate(layout_names):
        for condition, mask in (("no_loss", no_loss), ("packet_loss", packet_loss)):
            selected = mask & (layout_index == layout_value)
            if not np.any(selected):
                continue
            layout_rows.append(
                {
                    "layout": layout_name,
                    "condition": condition,
                    "frames": int(np.sum(selected)),
                    **_metrics_for_mask(y, probabilities, selected),
                }
            )

    write_csv(OUTPUT / "diagnostics" / "calibration-by-layout.csv", layout_rows)
    write_json(
        OUTPUT / "diagnostics" / "calibration-summary.json",
        {
            "diagnostic_only": True,
            "model": "frozen v1; probabilities are not recalibrated",
            "overall": {
                "no_loss": metric_row(y[no_loss], probabilities[no_loss]),
                "packet_loss": metric_row(y[packet_loss], probabilities[packet_loss]),
            },
            "stratifications": stratifications,
            "layout_rows": layout_rows,
            "distribution_shift": {
                "no_loss_available_counts": {
                    str(value): int(np.sum(available[no_loss] == value))
                    for value in sorted(set(available[no_loss].tolist()))
                },
                "packet_loss_available_counts": {
                    str(value): int(np.sum(available[packet_loss] == value))
                    for value in sorted(set(available[packet_loss].tolist()))
                },
            },
        },
    )
    return {
        "packet": gap_summary,
        "reliability": reliability,
        "stratifications": stratifications,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        print(json.dumps(run_diagnostics(), indent=2))


if __name__ == "__main__":
    main()
