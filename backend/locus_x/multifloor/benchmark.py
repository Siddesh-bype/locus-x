"""Reproducible multi-floor protocol: python -m backend.locus_x.multifloor.benchmark.

One run samples a scenario x route x seed grid once and feeds every frame's observation
mapping to **both** localizers, so inferred and route-assisted results are produced from
one generated stream rather than two independently sampled ones. Generation happens once
per frame, never once per mode; that is the fairness contract. The two modes are reported
in separate rows and separate tables and are never combined into a single score.

The protocol model lives here, not in :mod:`locus_x.schemas`: ``BenchmarkRequest`` and the
legacy benchmark path are untouched, so the existing public contract does not grow a
multi-floor route concept or a new result shape.

Honesty notes that every number inherits:

* Ranges, routes and slab attenuation are synthetic. Nothing here is a real-radio,
  calibration or deployment claim.
* Route-assisted floor identity is the configured route, not an inference. Its floor
  accuracy is therefore ``None``; transition success in that mode only measures freshness
  and settling, not floor detection.
* Latency is measured compute time on the machine that ran the protocol. It is
  nondeterministic, unlike every other reported field. Wall-clock run duration is printed
  to stdout and deliberately not written to the artifact, so ``results.json`` reproduces.
"""

import argparse
import csv
import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from time import perf_counter
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from ..building import Building, MultiFloorRoute, RouteTransition
from ..environment import load_environments
from ..schemas import PRESETS, FaultLabel, Measurement, Scenario, Settings, StrictModel
from .coordinates import anchor_key, floor_anchors
from .evaluation import (
    FrameRecord,
    evaluate_multifloor,
    recovery_delays,
    summarize_errors,
    transition_resolutions,
)
from .route import RouteState, route_state, transition_windows
from .simulation import generate_measurements_3d
from .tracking import MultiFloorLocalizer, RouteAssist

MAX_FRAMES = 25_000
"""Same protocol budget as the legacy benchmark."""

MODES: tuple[Literal["inferred", "route_assisted"], ...] = ("inferred", "route_assisted")

TRANSITION_TOLERANCE = 5.0
"""Seconds after a traverse ends in which the target storey must be fresh and correct."""

PROGRESS_EVERY = 40
"""Frames between progress callbacks, matching the legacy benchmark cadence."""


class MultiFloorBenchmarkConfig(StrictModel):
    """One multi-floor protocol. ``mode`` names the protocol, not a filter.

    Both modes are always evaluated on the same stream (the fairness contract), so the
    result contains one row per mode regardless of which protocol file was loaded. The
    field identifies which mode the file is filed under and orders the printed report; it
    never hides the other mode's evidence and never lets the two be averaged together.
    """

    mode: Literal["inferred", "route_assisted"]
    environment_id: str = "hospital_v3"
    scenarios: list[Scenario] = Field(
        default_factory=lambda: list(PRESETS), min_length=1, max_length=7
    )
    routes: list[str] = Field(min_length=1)
    seeds: list[int] = Field(default_factory=lambda: [37, 43], min_length=1, max_length=4)
    steps: int = Field(default=240, ge=10, le=400)
    dt: float = Field(default=0.5, ge=0.1, le=1)

    @model_validator(mode="after")
    def bound_work(self):
        if any(seed < 0 or seed > 2**31 - 1 for seed in self.seeds):
            raise ValueError("Seeds must be unsigned 31-bit integers")
        if len(set(self.routes)) != len(self.routes):
            raise ValueError("Routes must be unique")
        if len(set(self.scenarios)) != len(self.scenarios):
            raise ValueError("Scenarios must be unique")
        if len(self.scenarios) * len(self.routes) * len(self.seeds) * self.steps > MAX_FRAMES:
            raise ValueError("Limit benchmark to 25,000 frames per run")
        return self


def config_sha256(config: MultiFloorBenchmarkConfig) -> str:
    """Canonical protocol hash: sha256 of sorted, unspaced JSON of the model dump.

    The model carries no wall-clock field, so the same protocol always hashes the same;
    run duration and latency are result fields and are deliberately outside this hash.
    """
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def environment_sha256(building: Building) -> str:
    """Hash of the validated building document, matching the legacy convention."""
    return hashlib.sha256(building.model_dump_json().encode()).hexdigest()


def _observations(
    keys: Sequence[str], measurements: Sequence[Measurement], labels: Sequence[FaultLabel]
) -> dict[str, Measurement]:
    """Key one frame's measurements by ``"<floor_id>:<anchor_id>"``.

    ``Measurement.anchor_id`` repeats on every storey, so the k-th available label in
    :func:`floor_anchors` order is the k-th measurement. The alignment is checked rather
    than assumed: a mismatch would silently file ranges under the wrong floor.
    ``_run_stream`` also proves the generator's floor-qualified ``slab_hops`` key order
    equals ``keys``, so a storey-order divergence fails loudly instead of filing ranges
    under the wrong storey. The mapping is built once per frame and the same object is
    handed to both localizers.
    """
    available = [key for key, label in zip(keys, labels, strict=True) if label.available]
    if len(available) != len(measurements):
        raise RuntimeError(
            "Observation alignment broke: "
            f"{len(available)} available labels but {len(measurements)} measurements"
        )
    return dict(zip(available, measurements, strict=True))


def _assist(state: RouteState, transitions: dict[str, RouteTransition]) -> RouteAssist:
    """Route assistance derived from the truth trajectory, never from an estimate."""
    if state.floor_id is not None:
        return RouteAssist(floor_id=state.floor_id)
    if state.transition_id is None:
        raise RuntimeError(
            f"Route state at {state.timestamp!r} s names neither a floor nor a transition"
        )
    transition = transitions[state.transition_id]
    return RouteAssist(
        floor_id=None,
        transition_id=transition.id,
        connection_id=transition.connection_id,
        from_floor_id=transition.from_floor_id,
        to_floor_id=transition.to_floor_id,
        progress=state.progress,
    )


def _transition_events(
    building: Building, route: MultiFloorRoute, *, last_timestamp: float, speed: float
) -> list[tuple[float, str, str]]:
    """Every traverse whose full post-transition window is sampled, as end/target/id.

    A loop route repeats its windows every period; a one-way route stops at its last
    waypoint, so its transitions occur once. Windows truncated by the horizon are dropped
    rather than scored against an incomplete tolerance window. Both choices are recorded in
    a row's ``transitions`` count so a low count is visible instead of silently inflating a
    success rate.
    """
    period = route_state(building, route, 0.0, speed=speed).period
    targets = {transition.id: transition.to_floor_id for transition in route.transitions}
    events: list[tuple[float, str, str]] = []
    for window in transition_windows(building, route, speed=speed):
        start, end = window.start, window.end
        while start < last_timestamp:
            if end + TRANSITION_TOLERANCE <= last_timestamp:
                events.append((end, targets[window.transition_id], window.transition_id))
            if not route.loop:
                break
            start += period
            end += period
    return sorted(events)


def _run_stream(
    building: Building,
    config: MultiFloorBenchmarkConfig,
    route: MultiFloorRoute,
    scenario: Scenario,
    seed: int,
    keys: Sequence[str],
    *,
    on_frame: Callable[[], None] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Sample one (scenario, route, seed) stream and evaluate both modes on it."""
    settings = Settings(scenario=scenario, **PRESETS[scenario], seed=seed, route=route.id)
    localizers = {mode: MultiFloorLocalizer(building, mode=mode) for mode in MODES}
    transitions = {transition.id: transition for transition in route.transitions}
    last_timestamp = (config.steps - 1) * config.dt
    events = _transition_events(
        building, route, last_timestamp=last_timestamp, speed=settings.speed
    )
    records: list[dict] = []
    frames: dict[str, list[FrameRecord]] = {mode: [] for mode in MODES}
    outage_ends: list[float] = []
    in_outage = False
    for frame_index in range(config.steps):
        timestamp = frame_index * config.dt
        state = route_state(building, route, timestamp, speed=settings.speed)
        measurements, labels, slab_hops = generate_measurements_3d(
            building, settings, state, frame_index
        )
        # Floor identity survives only in the generator's anchor order: ``labels`` and
        # ``slab_hops`` are aligned with it, and anchor IDs repeat across storeys. If that
        # order ever diverged from the observation keys, ranges would be filed under the
        # wrong storey and every metric would be wrong with no failure.
        if list(slab_hops) != list(keys):
            raise RuntimeError(
                "Floor alignment broke: the generator's slab_hops key order diverges from "
                "the observation key order, so ranges would be filed under the wrong storey"
            )
        observations = _observations(keys, measurements, labels)
        outage = any(label.kind == "outage" for label in labels)
        if outage and not in_outage:
            in_outage = True
        elif in_outage and not outage:
            outage_ends.append(timestamp)
            in_outage = False
        for mode, localizer in localizers.items():
            assist = _assist(state, transitions) if mode == "route_assisted" else None
            result = localizer.update(observations, timestamp=timestamp, assist=assist)
            evaluation = evaluate_multifloor(state, result.tracking, result.scores, state.z)
            frames[mode].append(
                FrameRecord(
                    timestamp=evaluation.timestamp,
                    floor_id=evaluation.floor_id,
                    updated=evaluation.updated,
                    ambiguous=evaluation.ambiguous,
                )
            )
            records.append(
                {
                    "timestamp": evaluation.timestamp,
                    "phase": evaluation.phase,
                    "horizontal_error": evaluation.horizontal_error,
                    "vertical_error": evaluation.vertical_error,
                    "floor_correct": evaluation.floor_correct,
                    "covered_horizontal_95": evaluation.covered_horizontal_95,
                    "updated": evaluation.updated,
                    "ambiguous": evaluation.ambiguous,
                    "floor_id": evaluation.floor_id,
                    "truth_floor_id": state.floor_id,
                    "mode": mode,
                    "scenario": scenario,
                    "route": route.id,
                    "seed": seed,
                    "runtime_ms": result.estimate.runtime_ms + result.tracking.runtime_ms,
                }
            )
        if on_frame is not None:
            on_frame()

    transition_records: list[dict] = []
    recovery_records: list[dict] = []
    for mode in MODES:
        resolutions = transition_resolutions(
            frames[mode], [(end, target) for end, target, _ in events]
        )
        for (end, _, transition_id), seconds in zip(events, resolutions, strict=True):
            transition_records.append(
                {
                    "mode": mode,
                    "scenario": scenario,
                    "route": route.id,
                    "seed": seed,
                    "transition_id": transition_id,
                    "end": end,
                    "resolution_seconds": seconds,
                }
            )
        for end, seconds in zip(
            outage_ends, recovery_delays(frames[mode], outage_ends), strict=True
        ):
            recovery_records.append(
                {
                    "mode": mode,
                    "scenario": scenario,
                    "route": route.id,
                    "seed": seed,
                    "outage_end": end,
                    "delay_seconds": seconds,
                }
            )
    return records, transition_records, recovery_records


def _mean(values: Sequence[float]) -> float | None:
    """Mean over finite values only; a ``nan`` must not discard the artifact at JSON time."""
    finite = [value for value in values if isfinite(value)]
    return float(np.mean(finite)) if finite else None


def _p95(values: Sequence[float]) -> float | None:
    """P95 over finite values only, for the same reason as :func:`_mean`."""
    finite = [value for value in values if isfinite(value)]
    return float(np.percentile(finite, 95)) if finite else None


def _aggregate(
    records: Sequence[dict],
    transition_records: Sequence[dict],
    recovery_records: Sequence[dict],
    *,
    mode: Literal["inferred", "route_assisted"],
) -> dict:
    """The protocol's aggregate definitions, in one auditable place.

    * ``horizontal_rmse``/``median``/``p95`` are over frames where the tracker reported a
      position; a withheld position is missing, never an error of zero.
    * ``vertical_rmse`` is over frames where ``z`` was reported, against the frame's true
      device elevation.
    * ``floor_accuracy`` is the mean of ``floor_correct`` (reported storey equals the truth
      storey) over frames with a truth storey. A withheld storey counts as a miss, so it
      must be read together with ``ambiguity_rate``. It is ``None`` in route-assisted mode
      because that storey comes from the route, not from the ranges.
    * ``transition_success_rate`` is the fraction of fully observed transitions followed by
      a fresh, unambiguous target-floor frame within :data:`TRANSITION_TOLERANCE` seconds
      of the transition ending.
    * ``recovery_mean_seconds``/``recovery_p95_seconds`` are over resolved outages only:
      the delay from the first non-outage frame to the first accepted measurement update.
      ``recovery_unresolved`` counts outages with no accepted update before the horizon.
    * ``ellipse_coverage_95`` is the empirical horizontal chi-square coverage, not a
      calibration claim.
    * ``latency_ms`` is measured 3D solve plus tracker-update time; floor scoring is not
      instrumented. It is the only nondeterministic field.
    """
    frames = len(records)
    horizontal = summarize_errors(record["horizontal_error"] for record in records)
    vertical = summarize_errors(record["vertical_error"] for record in records)
    correct = [record["floor_correct"] for record in records if record["floor_correct"] is not None]
    resolutions = [record["resolution_seconds"] for record in transition_records]
    successes = sum(seconds is not None for seconds in resolutions)
    delays = [
        record["delay_seconds"]
        for record in recovery_records
        if record["delay_seconds"] is not None
    ]
    covered = [
        record["covered_horizontal_95"]
        for record in records
        if record["covered_horizontal_95"] is not None
    ]
    updated = sum(1 for record in records if record["updated"])
    ambiguous = sum(1 for record in records if record["ambiguous"])
    latency = [record["runtime_ms"] for record in records]
    return {
        "frames": frames,
        "valid_frames": horizontal["count"],
        "valid_rate": horizontal["count"] / frames if frames else None,
        "fresh_frames": updated,
        "fresh_update_rate": updated / frames if frames else None,
        "horizontal_rmse": horizontal["rmse"],
        "horizontal_median": horizontal["median"],
        "horizontal_p95": horizontal["p95"],
        "vertical_rmse": vertical["rmse"],
        "floor_frames": len(correct),
        "floor_accuracy": (sum(correct) / len(correct) if mode == "inferred" and correct else None),
        "transitions": len(resolutions),
        "transition_successes": successes,
        "transition_success_rate": successes / len(resolutions) if resolutions else None,
        "outages": len(recovery_records),
        "recoveries": len(delays),
        "recovery_mean_seconds": _mean(delays),
        "recovery_p95_seconds": _p95(delays),
        "recovery_unresolved": len(recovery_records) - len(delays),
        "latency_ms": _mean(latency),
        "ellipse_coverage_95": (
            float(np.mean([bool(value) for value in covered])) if covered else None
        ),
        "ambiguous_frames": ambiguous,
        "ambiguity_rate": ambiguous / frames if frames else None,
    }


def _group(
    records: Sequence[dict],
    transitions: Sequence[dict],
    recoveries: Sequence[dict],
    *,
    mode: str,
    scenario: str | None = None,
    route: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    def keep(record: dict) -> bool:
        return (
            record["mode"] == mode
            and (scenario is None or record["scenario"] == scenario)
            and (route is None or record["route"] == route)
        )

    return (
        [record for record in records if keep(record)],
        [record for record in transitions if keep(record)],
        [record for record in recoveries if keep(record)],
    )


def run_multifloor_benchmark(
    config: MultiFloorBenchmarkConfig,
    *,
    building: Building | None = None,
    progress: Callable[[float], None] | None = None,
) -> dict:
    """Run one protocol over the full fault matrix, evaluating both modes per frame.

    ``building`` overrides configuration loading (tests and callers with an already
    validated document); it must be a :class:`~locus_x.building.Building`, because the
    multi-floor protocol has no meaning on a single-floor ``Environment``.
    """
    if building is None:
        environments = load_environments()
        configuration = environments.get(config.environment_id)
        if configuration is None:
            raise ValueError(
                f"Unknown environment {config.environment_id!r}; available environments are "
                f"{', '.join(sorted(environments))}"
            )
    else:
        configuration = building
    if not isinstance(configuration, Building):
        raise ValueError(
            "The multi-floor protocol requires a Building with transition_routes; "
            f"environment {getattr(configuration, 'id', config.environment_id)!r} is not one"
        )
    building = configuration
    routes = {route.id: route for route in building.transition_routes}
    unknown = [route_id for route_id in config.routes if route_id not in routes]
    if unknown:
        raise ValueError(f"Unknown multi-floor route(s): {', '.join(unknown)}")
    keys = [anchor_key(anchor.floor_id, anchor.anchor_id) for anchor in floor_anchors(building)]
    total = len(config.scenarios) * len(config.routes) * len(config.seeds) * config.steps
    completed = 0

    def on_frame() -> None:
        nonlocal completed
        completed += 1
        if progress is not None and completed % PROGRESS_EVERY == 0:
            progress(completed / total)

    records: list[dict] = []
    transitions: list[dict] = []
    recoveries: list[dict] = []
    for scenario in config.scenarios:
        for route_id in config.routes:
            for seed in config.seeds:
                run_records, run_transitions, run_recoveries = _run_stream(
                    building,
                    config,
                    routes[route_id],
                    scenario,
                    seed,
                    keys,
                    on_frame=on_frame,
                )
                records.extend(run_records)
                transitions.extend(run_transitions)
                recoveries.extend(run_recoveries)
    if progress is not None:
        progress(1.0)

    rows: list[dict] = []
    mode_order = [config.mode, *(mode for mode in MODES if mode != config.mode)]
    for mode in mode_order:
        for scenario in config.scenarios:
            for route_id in config.routes:
                group_records, group_transitions, group_recoveries = _group(
                    records, transitions, recoveries, mode=mode, scenario=scenario, route=route_id
                )
                rows.append(
                    {
                        "mode": mode,
                        "scenario": scenario,
                        "route": route_id,
                        "seeds": ",".join(map(str, config.seeds)),
                        "steps": config.steps,
                        "dt": config.dt,
                        **_aggregate(
                            group_records,
                            group_transitions,
                            group_recoveries,
                            mode=mode,
                        ),
                    }
                )
    totals = []
    for mode in mode_order:
        group_records, group_transitions, group_recoveries = _group(
            records, transitions, recoveries, mode=mode
        )
        totals.append(
            {
                "mode": mode,
                **_aggregate(group_records, group_transitions, group_recoveries, mode=mode),
            }
        )
    return {
        "result_version": "3.1.0",
        "config": config.model_dump(mode="json"),
        "config_sha256": config_sha256(config),
        "protocol_mode": config.mode,
        "modes": list(MODES),
        "environment_id": building.id,
        "environment_version": building.version,
        "environment_sha256": environment_sha256(building),
        "protocol_frames": total,
        "rows": rows,
        "mode_totals": totals,
        "notes": (
            "Synthetic ranges, routes and slab attenuation; no real-radio, calibration or "
            "deployment claim. Both modes consumed one generated observation stream per "
            "frame. Modes are reported separately and never combined into one score. "
            "Route-assisted floor identity comes from the configured route, not inference, "
            "so its floor accuracy is null and its transition success only measures "
            "freshness and settling. Floor accuracy counts a withheld storey as a miss; "
            "read it with the ambiguity rate. Ellipse coverage is empirical under the "
            "synthetic noise model. Latency is measured compute time and is the only "
            "nondeterministic field; everything else reproduces for the same config and "
            "seeds. Wall-clock run duration is printed to stdout and deliberately not "
            "stored here."
        ),
    }


def _format(value: float | int | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _print_report(result: dict) -> None:
    """Print one table per mode, configured mode first; no cross-mode aggregate exists."""
    primary = result["protocol_mode"]
    order = [primary, *(mode for mode in MODES if mode != primary)]
    labels = {
        "inferred": "storey inferred from ranges only",
        "route_assisted": "storey from the configured route; not inference, floor accuracy n/a",
    }
    print(
        f"Multi-floor protocol {result['config']['mode']!r}: {result['protocol_frames']} frames, "
        f"environment {result['environment_id']}@{result['environment_version']}, "
        f"config_sha256={result['config_sha256'][:16]}..."
    )
    print()
    print(
        "Caveat: synthetic ranges, routes and slab attenuation; no real-radio, calibration "
        "or deployment claim. Floor probabilities are likelihood scores, not calibrated "
        "posteriors."
    )
    print(
        "Route-assisted transition success is freshness by construction (the storey follows "
        "the configured route and settling only), not floor inference: never read it as "
        "better than inferred."
    )
    header = (
        f"{'scenario':<15}{'route':<20}{'frames':>7}{'h_rmse':>8}{'h_p95':>8}{'v_rmse':>8}"
        f"{'floor_acc':>10}{'trans':>9}{'recovery':>10}{'ambig':>7}"
    )
    for mode in order:
        print()
        print(f"Mode: {mode} ({labels[mode]})")
        print(header)
        for row in result["rows"]:
            if row["mode"] != mode:
                continue
            transitions = f"{row['transition_successes']}/{row['transitions']}"
            print(
                f"{row['scenario']:<15}{row['route']:<20}{row['frames']:>7}"
                f"{_format(row['horizontal_rmse']):>8}{_format(row['horizontal_p95']):>8}"
                f"{_format(row['vertical_rmse']):>8}{_format(row['floor_accuracy']):>10}"
                f"{transitions:>9}{_format(row['recovery_mean_seconds']):>10}"
                f"{_format(row['ambiguity_rate']):>7}"
            )
        total = next(item for item in result["mode_totals"] if item["mode"] == mode)
        print(
            f"{'mode total':<15}{'':<20}{total['frames']:>7}"
            f"{_format(total['horizontal_rmse']):>8}{_format(total['horizontal_p95']):>8}"
            f"{_format(total['vertical_rmse']):>8}{_format(total['floor_accuracy']):>10}"
            f"{str(total['transition_successes']) + '/' + str(total['transitions']):>9}"
            f"{_format(total['recovery_mean_seconds']):>10}"
            f"{_format(total['ambiguity_rate']):>7}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmarks/v3/multifloor-inferred.json"),
    )
    parser.add_argument(
        "--output", type=Path, help="New output directory; existing results are protected"
    )
    args = parser.parse_args()
    config = MultiFloorBenchmarkConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    if args.output is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = Path("artifacts/multifloor-benchmark-runs") / f"{config.mode}-{stamp}"
    if any((args.output / name).exists() for name in ("results.json", "metrics.csv")):
        parser.error(
            "Output contains existing experiment artifacts; choose a new --output directory"
        )
    start = perf_counter()
    result = run_multifloor_benchmark(config)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(
        json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
    )
    with (args.output / "metrics.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(result["rows"][0]))
        writer.writeheader()
        writer.writerows(result["rows"])
    _print_report(result)
    print()
    print(f"Saved {args.output / 'metrics.csv'} and results.json in {perf_counter() - start:.1f}s")


if __name__ == "__main__":
    main()
