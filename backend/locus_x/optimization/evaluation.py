"""Layout evaluation on frozen splits with a *fixed* estimator.

The same machinery the benchmark uses (``generate_measurements`` + ``solve``)
is replayed for a candidate anchor layout. Fault schedules are per-anchor
*index*, so every layout with the same budget receives the identical fault and
noise stream; only anchor geometry changes. Runtime values are never part of
any comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..building import Building, Floor
from ..environment import route_position
from ..estimators import solve
from ..schemas import Environment, Point, Settings
from ..simulation import generate_measurements
from ..tracking import MotionTracker
from .geometry_scores import layout_observability, sample_route
from .protocol import LayoutMetrics, SplitConfig
from .sites import region_at

FAULT_SCENARIOS = ("anchor_failure", "chaos")


def build_floor_environment(
    configuration: Environment | Building, floor_id: str | None, anchors
) -> Environment:
    """Return a copy of the floor with ``anchors`` installed (never mutates the catalog)."""
    if isinstance(configuration, Building):
        selected_floor = floor_id or configuration.default_floor_id
        floor = next((f for f in configuration.floors if f.id == selected_floor), None)
        if floor is None:
            raise ValueError(f"Unknown floor: {floor_id}")
        return floor.model_copy(update={"anchors": list(anchors)})
    if floor_id is not None and floor_id != configuration.id:
        raise ValueError(f"Unknown floor: {floor_id}")
    return configuration.model_copy(update={"anchors": list(anchors)})


@dataclass
class FrameRecord:
    scenario: str
    truth: Point
    error: float | None
    updated: bool
    status: str
    has_nlos: bool
    has_outage: bool
    has_dropout: bool


def replay_split(
    environment: Environment,
    split: SplitConfig,
    estimator: str = "baseline",
    regions=None,
) -> tuple[LayoutMetrics, dict[str, LayoutMetrics], list[FrameRecord]]:
    """Replay a layout on one split. ``estimator`` is fixed for the whole call.

    Returns (pooled metrics, per-scenario metrics, frame records).
    """
    records: list[FrameRecord] = []
    route = environment.routes[split.route]
    for scenario in split.scenarios:
        for seed in split.seeds:
            settings = Settings(
                scenario=scenario,
                seed=seed,
                route=split.route,
                disabled_anchors=[],
            )
            tracker = MotionTracker() if estimator == "locus" else None
            trust: dict[str, float] = {}
            for frame in range(1, split.steps + 1):
                timestamp = frame * split.dt
                truth = route_position(route, timestamp, settings.speed)
                measurements, labels = generate_measurements(
                    environment, settings, truth, timestamp, frame
                )
                if estimator == "baseline":
                    estimate = solve(measurements)
                    position = estimate.position
                    updated = position is not None
                    status = estimate.status
                else:
                    robust = solve(measurements, robust=True, trust=trust)
                    tracking = tracker.update(robust, timestamp)
                    position = tracking.position
                    updated = tracking.updated
                    status = tracking.status
                records.append(
                    FrameRecord(
                        scenario=scenario,
                        truth=truth,
                        error=(
                            None
                            if position is None
                            else float(np.linalg.norm(np.asarray(position) - truth))
                        ),
                        updated=updated,
                        status=status,
                        has_nlos=any(label.kind == "nlos" for label in labels),
                        has_outage=any(label.kind == "outage" for label in labels),
                        has_dropout=any(label.kind == "packet_loss" for label in labels),
                    )
                )
    per_scenario = {
        scenario: summarize([r for r in records if r.scenario == scenario], regions)
        for scenario in split.scenarios
    }
    return summarize(records, regions), per_scenario, records


def _stats(errors: list[float]) -> dict:
    if not errors:
        return {"rmse": None, "median": None, "p95": None}
    values = np.asarray(errors, dtype=float)
    return {
        "rmse": float(np.sqrt(np.mean(values**2))),
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
    }


def summarize(records: list[FrameRecord], regions=None) -> LayoutMetrics:
    errors = [r.error for r in records if r.error is not None]
    nlos = [r.error for r in records if r.has_nlos and r.error is not None]
    outage = [r.error for r in records if r.has_outage and r.error is not None]
    shells = list(regions or [])
    per_region: dict[str, dict] = {}
    for record in records:
        name = region_at(record.truth, shells) or "corridor"
        slot = per_region.setdefault(name, {"frames": 0, "errors": []})
        slot["frames"] += 1
        if record.error is not None:
            slot["errors"].append(record.error)
    return LayoutMetrics(
        **_stats(errors),
        valid_rate=len(errors) / len(records) if records else 0.0,
        update_rate=(sum(r.updated for r in records) / len(records)) if records else 0.0,
        nlos_rmse=_stats(nlos)["rmse"],
        outage_rmse=_stats(outage)["rmse"],
        frames=len(records),
        valid_frames=len(errors),
        per_region={
            name: {
                **_stats(slot["errors"]),
                "frames": slot["frames"],
                "valid_frames": len(slot["errors"]),
            }
            for name, slot in sorted(per_region.items())
        },
        observability=0.0,
        poor_geometry_fraction=0.0,
        mean_condition=None,
    )


@dataclass
class SplitEvaluation:
    pooled: LayoutMetrics
    per_scenario: dict[str, LayoutMetrics]
    observability: dict


def evaluate_layout(
    configuration: Environment | Building,
    floor_id: str | None,
    anchors,
    split: SplitConfig,
    estimator: str = "baseline",
    coverage_split: SplitConfig | None = None,
) -> SplitEvaluation:
    """Evaluate one layout under one frozen split with a fixed estimator."""
    environment = build_floor_environment(configuration, floor_id, anchors)
    floor = environment if isinstance(environment, Floor) else None
    regions = (
        [*environment.rooms, *environment.corridors]
        if floor is not None
        else list(environment.rooms)
    )
    pooled, per_scenario, _ = replay_split(environment, split, estimator, regions)
    anchor_pairs = [(anchor.id, anchor.position) for anchor in environment.anchors]
    sample_source = coverage_split or split
    samples = sample_route(environment.routes[sample_source.route])
    observability = layout_observability(environment, anchor_pairs, samples, regions)
    pooled = pooled.model_copy(
        update={
            "observability": observability["observability"],
            "poor_geometry_fraction": observability["poor_geometry_fraction"],
            "mean_condition": observability["mean_condition"],
        }
    )
    return SplitEvaluation(pooled=pooled, per_scenario=per_scenario, observability=observability)
