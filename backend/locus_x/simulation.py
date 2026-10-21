"""Synthetic radio model. Fault labels are returned separately and never reach estimators."""

import numpy as np

from .environment import intersects
from .schemas import Environment, FaultLabel, Measurement, Point, Settings


def generate_measurements(
    environment: Environment,
    settings: Settings,
    position: Point,
    timestamp: float,
    frame_index: int,
) -> tuple[list[Measurement], list[FaultLabel]]:
    observations, labels = [], []
    for i, anchor in enumerate(environment.anchors):
        # Per-anchor streams keep surviving observations identical when an anchor is disabled.
        rng = np.random.default_rng(np.random.SeedSequence([settings.seed, frame_index, i]))
        true_range = float(np.linalg.norm(np.array(position) - anchor.position))
        walls = [w.id for w in environment.walls if intersects(position, anchor.position, w.a, w.b)]
        bias = settings.nlos_bias * min(len(walls), 3) / 2 if settings.walls_enabled else 0
        kind = "nlos" if bias > 0 else "los"
        if anchor.id in settings.disabled_anchors:
            kind = "disabled"
        elif (
            settings.scenario in ("anchor_failure", "chaos")
            and 12 <= timestamp % 40 < 22
            and i in (1, 3, 5, 7)
        ):
            kind = "outage"
        elif rng.random() < settings.dropout:
            kind = "packet_loss"
        available = kind in ("los", "nlos")
        noise = float(rng.normal(0, settings.noise_std))
        if available:
            observations.append(
                Measurement(
                    anchor_id=anchor.id,
                    anchor_position=anchor.position,
                    timestamp=timestamp,
                    observed_range=max(0.01, true_range + bias + noise),
                )
            )
        labels.append(
            FaultLabel(
                anchor_id=anchor.id,
                available=available,
                kind=kind,
                intersected_walls=walls if settings.walls_enabled else [],
                true_range=true_range,
                injected_bias=bias,
            )
        )
    return observations, labels
