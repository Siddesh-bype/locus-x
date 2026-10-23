"""Multi-floor metrics and the reproducible protocol.

The metric helpers are exercised on hand-constructed frames, then the protocol is checked
for its two fairness properties: one observation stream per frame shared by both localizers,
and a floor accuracy that exists only where the storey is inferred.
"""

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.locus_x.building import Building
from backend.locus_x.environment import load_environments
from backend.locus_x.multifloor import (
    FloorState,
    FrameRecord,
    RouteState,
    Tracking3D,
    evaluate_multifloor,
    recovery_delays,
    summarize_errors,
    transition_resolutions,
)
from backend.locus_x.multifloor import benchmark as benchmark_module
from backend.locus_x.multifloor.benchmark import (
    MAX_FRAMES,
    MultiFloorBenchmarkConfig,
    config_sha256,
    run_multifloor_benchmark,
)
from backend.locus_x.multifloor.benchmark import (
    main as benchmark_main,
)
from backend.locus_x.multifloor.tracking import MultiFloorLocalizer
from backend.locus_x.schemas import PRESETS

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DIR = ROOT / "configs/benchmarks/v3"
SCENARIOS = [
    "clean",
    "noise",
    "high_noise",
    "nlos",
    "packet_loss",
    "anchor_failure",
    "chaos",
]
ROUTES = [
    "ascent_stairs",
    "ascent_lift",
    "descent_stairs",
    "transition_pause",
    "cross_floor_survey",
]


def _truth(
    floor_id: str | None = "ground",
    *,
    x: float = 4.0,
    y: float = 14.0,
    z: float = 1.2,
    timestamp: float = 12.0,
    phase: str = "move",
) -> RouteState:
    return RouteState(
        timestamp=timestamp,
        period=100.0,
        phase=phase,  # type: ignore[arg-type]
        floor_id=floor_id,
        x=x,
        y=y,
        z=z,
        connection_id=None,
        transition_id=None,
        progress=0.0,
    )


def _tracking(
    *,
    position: tuple[float, float, float] | None = (4.0, 14.0, 1.2),
    z: float | None = 1.2,
    covariance: list[list[float]] | None = None,
    floor_id: str | None = "ground",
    source: str = "inferred",
    ambiguous: bool = False,
    updated: bool = True,
) -> Tracking3D:
    return Tracking3D(
        position=position,
        z=z,
        velocity=None,
        covariance=covariance,
        ellipse=None,
        floor_state=FloorState(  # type: ignore[arg-type]
            floor_id=floor_id, source=source, confidence=None, ambiguous=ambiguous
        ),
        transition=None,
        status="tracking",
        reason="test fixture",
        updated=updated,
        outage_seconds=0.0,
        runtime_ms=1.0,
    )


def _short_config(**overrides) -> MultiFloorBenchmarkConfig:
    payload = {
        "mode": "inferred",
        "scenarios": ["clean"],
        "routes": ["ascent_stairs"],
        "seeds": [37],
        "steps": 12,
        "dt": 0.5,
    }
    payload.update(overrides)
    return MultiFloorBenchmarkConfig(**payload)


# ------------------------------------------------------------ metric maths -----


def test_error_summary_reports_rmse_median_and_p95_over_finite_values_only():
    summary = summarize_errors([3.0, 4.0, None, float("inf"), float("nan")])
    assert summary["count"] == 2
    assert summary["rmse"] == pytest.approx(math.sqrt((9 + 16) / 2))
    assert summary["median"] == pytest.approx(3.5)
    assert summary["p95"] == pytest.approx(3.95)
    assert summarize_errors([None, None]) == {
        "count": 0,
        "rmse": None,
        "median": None,
        "p95": None,
    }


def test_horizontal_and_vertical_errors_use_the_frame_truth():
    truth = _truth("ground", x=4.0, y=14.0, z=1.2)
    evaluation = evaluate_multifloor(
        truth,
        _tracking(position=(6.0, 17.0, 2.0), z=2.0),
        None,
        vertical_truth=1.2,
    )
    assert evaluation.horizontal_error == pytest.approx(math.hypot(2.0, 3.0))
    assert evaluation.vertical_error == pytest.approx(0.8)
    assert evaluation.phase == "move"
    assert evaluation.updated is True
    assert evaluation.hypothesis_floor is None and evaluation.hypothesis_ambiguous is None

    # vertical_truth is explicit: the same z can be 3.0 m above a different reference.
    reference = evaluate_multifloor(
        truth, _tracking(position=(4.0, 14.0, 1.2), z=1.2), None, vertical_truth=4.2
    )
    assert reference.vertical_error == pytest.approx(3.0)


def test_a_withheld_position_produces_no_error_instead_of_a_zero():
    evaluation = evaluate_multifloor(
        _truth("first", z=5.4),
        _tracking(position=None, z=None, floor_id="first"),
        None,
        vertical_truth=5.4,
    )
    assert evaluation.horizontal_error is None
    assert evaluation.vertical_error is None
    assert evaluation.covered_horizontal_95 is None
    # The storey can still be right while position is withheld; that is not an error of 0.
    assert evaluation.floor_correct is True


def test_floor_correct_is_identity_except_where_the_route_owns_the_storey():
    assert (
        evaluate_multifloor(_truth("ground"), _tracking(floor_id="ground"), None, 1.2).floor_correct
        is True
    )
    assert (
        evaluate_multifloor(_truth("ground"), _tracking(floor_id="first"), None, 1.2).floor_correct
        is False
    )
    # Route-assisted identity is knowledge by construction, never a success.
    assisted = evaluate_multifloor(
        _truth("ground"),
        _tracking(floor_id="ground", source="route_assisted"),
        None,
        1.2,
    )
    assert assisted.floor_correct is None
    assert assisted.floor_source == "route_assisted"
    # Mid-traverse there is no truth storey to be correct about.
    assert (
        evaluate_multifloor(
            _truth(None, phase="transition"), _tracking(floor_id=None), None, 2.7
        ).floor_correct
        is None
    )
    # A held storey is still identity-correct; ambivalence is reported separately.
    held = evaluate_multifloor(
        _truth("ground"), _tracking(floor_id="ground", ambiguous=True), None, 1.2
    )
    assert held.floor_correct is True
    assert held.ambiguous is True


def test_horizontal_coverage_ignores_the_vertical_component_and_covariance_row():
    covariance = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1e9]]
    covered = evaluate_multifloor(
        _truth("ground", x=4.0, y=14.0, z=1.2),
        _tracking(position=(5.0, 14.0, 101.2), z=101.2, covariance=covariance),
        None,
        vertical_truth=1.2,
    )
    # 1 m horizontal error, so chi-square 1 <= 5.99 even though z is 100 m off.
    assert covered.covered_horizontal_95 is True
    uncovered = evaluate_multifloor(
        _truth("ground", x=4.0, y=14.0, z=1.2),
        _tracking(position=(7.0, 14.0, 1.2), covariance=covariance),
        None,
        vertical_truth=1.2,
    )
    # 3 m horizontal error gives chi-square 9 > 5.99.
    assert uncovered.covered_horizontal_95 is False


def _frames(*entries: tuple[float, str | None, bool, bool]) -> list[FrameRecord]:
    return [
        FrameRecord(timestamp=timestamp, floor_id=floor_id, updated=updated, ambiguous=ambiguous)
        for timestamp, floor_id, updated, ambiguous in entries
    ]


def test_transition_success_requires_a_fresh_unambiguous_target_floor():
    frames = _frames(
        (10.5, "second", True, False),  # wrong floor
        (11.0, "first", False, False),  # right floor, stale
        (12.0, "first", True, True),  # right floor, fresh, but ambiguous
        (12.5, "first", True, False),  # first valid resolution, 2.5 s after the end
        (13.0, "first", True, False),
    )
    resolutions = transition_resolutions(
        frames, [(10.0, "first"), (20.0, "second"), (10.0, "ground")]
    )
    assert resolutions[0] == pytest.approx(2.5)
    assert resolutions[1] is None  # nothing after that transition end
    assert resolutions[2] is None  # the target storey is never reported

    # A resolution outside the tolerance window does not count.
    assert transition_resolutions(_frames((16.0, "first", True, False)), [(10.0, "first")]) == [
        None
    ]


def test_recovery_delay_is_measured_from_the_first_non_outage_frame():
    frames = _frames(
        (21.5, "ground", True, False),  # still inside the outage
        (22.0, "ground", False, False),  # first frame after the outage, no accepted update
        (22.5, "ground", True, False),
        (23.0, "ground", True, False),
    )
    assert recovery_delays(frames, [22.0]) == [pytest.approx(0.5)]
    assert recovery_delays(frames, [99.0]) == [None]


# ------------------------------------------------- both modes, one stream ------


def test_both_modes_consume_one_observation_stream_per_frame(monkeypatch):
    frames = _short_config().steps
    generated = []
    original_generate = benchmark_module.generate_measurements_3d

    def spy_generate(*args, **kwargs):
        generated.append(args[3])
        return original_generate(*args, **kwargs)

    updates = []
    original_update = MultiFloorLocalizer.update

    def spy_update(self, observations, *, timestamp, assist=None):
        updates.append((self.mode, timestamp, id(observations)))
        return original_update(self, observations, timestamp=timestamp, assist=assist)

    monkeypatch.setattr(benchmark_module, "generate_measurements_3d", spy_generate)
    monkeypatch.setattr(MultiFloorLocalizer, "update", spy_update)

    result = run_multifloor_benchmark(_short_config())

    # One generator call per frame, not one per mode.
    assert generated == list(range(frames))
    # Each frame reached both localizers, and both were handed the identical mapping object.
    assert len(updates) == 2 * frames
    assert {mode for mode, _, _ in updates} == {"inferred", "route_assisted"}
    identities: dict[float, set[int]] = defaultdict(set)
    for _, timestamp, identity in updates:
        identities[timestamp].add(identity)
    assert len(identities) == frames
    assert all(len(found) == 1 for found in identities.values())
    assert all(row["frames"] == frames for row in result["rows"])


def test_route_assisted_never_reports_floor_accuracy_and_inferred_always_does():
    result = run_multifloor_benchmark(_short_config())
    inferred = [row for row in result["rows"] if row["mode"] == "inferred"]
    assisted = [row for row in result["rows"] if row["mode"] == "route_assisted"]
    assert inferred and assisted
    assert all(isinstance(row["floor_accuracy"], float) for row in inferred)
    assert all(row["floor_frames"] > 0 for row in inferred)
    # Route-assisted identity is the configured route, so it is never scored.
    assert all(row["floor_accuracy"] is None for row in assisted)
    assert all(row["floor_frames"] == 0 for row in assisted)


# --------------------------------------------------------- protocol end to end --


def test_a_short_protocol_round_trips_through_strict_json():
    result = run_multifloor_benchmark(_short_config(steps=10))
    encoded = json.dumps(result, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["result_version"] == "3.1.0"
    assert decoded["environment_version"] == "3.1.0"
    assert decoded["protocol_frames"] == 10
    assert len(decoded["config_sha256"]) == 64
    assert len(decoded["environment_sha256"]) == 64
    assert decoded["modes"] == ["inferred", "route_assisted"]
    assert {row["mode"] for row in decoded["rows"]} == {"inferred", "route_assisted"}
    assert {row["route"] for row in decoded["rows"]} == {"ascent_stairs"}
    assert [total["mode"] for total in decoded["mode_totals"]] == [
        "inferred",
        "route_assisted",
    ]


def test_the_config_rejects_a_matrix_over_the_frame_budget():
    with pytest.raises(ValidationError, match="25,000 frames"):
        MultiFloorBenchmarkConfig(
            mode="inferred",
            scenarios=SCENARIOS,
            routes=ROUTES,
            seeds=[37, 43],
            steps=MAX_FRAMES // (len(SCENARIOS) * len(ROUTES) * 2) + 1,
            dt=0.5,
        )
    # The shipped protocols sit inside the budget (arithmetic pinned here on purpose).
    assert len(SCENARIOS) * len(ROUTES) * 2 * 240 == 16_800 <= MAX_FRAMES


def test_both_protocol_files_carry_the_identical_matrix_and_name_one_mode():
    inferred = MultiFloorBenchmarkConfig.model_validate_json(
        (PROTOCOL_DIR / "multifloor-inferred.json").read_text(encoding="utf-8")
    )
    assisted = MultiFloorBenchmarkConfig.model_validate_json(
        (PROTOCOL_DIR / "multifloor-route-assisted.json").read_text(encoding="utf-8")
    )
    assert inferred.mode == "inferred"
    assert assisted.mode == "route_assisted"
    assert inferred.scenarios == assisted.scenarios == list(PRESETS) == SCENARIOS
    assert inferred.routes == assisted.routes == ROUTES
    assert inferred.seeds == assisted.seeds == [37, 43]
    assert inferred.model_dump(exclude={"mode"}) == assisted.model_dump(exclude={"mode"})
    # The same work, a different protocol identity.
    assert config_sha256(inferred) != config_sha256(assisted)


def test_unknown_routes_and_single_floor_environments_are_rejected():
    with pytest.raises(ValueError, match="Unknown multi-floor route"):
        run_multifloor_benchmark(_short_config(routes=["not-a-route"]))
    single_floor = load_environments()["hospital_floor_01"]
    with pytest.raises(ValueError, match="requires a Building"):
        run_multifloor_benchmark(_short_config(), building=single_floor)


def test_the_cli_refuses_to_overwrite_existing_artifacts(tmp_path, monkeypatch, capsys):
    config_path = tmp_path / "protocol.json"
    config_path.write_text(json.dumps(_short_config().model_dump(mode="json")), encoding="utf-8")
    output = tmp_path / "run"
    output.mkdir()
    (output / "results.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["benchmark", "--config", str(config_path), "--output", str(output)]
    )
    with pytest.raises(SystemExit) as exit_info:
        benchmark_main()
    assert exit_info.value.code == 2
    assert "existing experiment artifacts" in capsys.readouterr().err


def test_the_environment_hash_is_the_validated_building_document():
    building = Building.model_validate(
        json.loads((ROOT / "configs/environments/hospital_v3.json").read_text(encoding="utf-8"))
    )
    result = run_multifloor_benchmark(_short_config(steps=10), building=building)
    assert result["environment_id"] == "hospital_v3"
    assert result["environment_sha256"] == benchmark_module.environment_sha256(building)
