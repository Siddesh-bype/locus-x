"""3D estimation, floor inference and floor-aware tracking.

The honesty properties are the point of this module:

* a 3D fix is only claimed where the anchors span three dimensions;
* a floor is only named when the vertical uncertainty interval fits inside that
  floor's Voronoi cell -- the mid-stairwell case must stay ambiguous even though its
  hypothesis margin is huge;
* ``inferred`` mode never sees a truth floor, structurally;
* the tracker refuses a storey change that the configured vertical speed cannot
  explain.
"""

import json
import math
from pathlib import Path

import pytest

from backend.locus_x.building import Building
from backend.locus_x.multifloor import (
    FIT_RATIO_LIMIT,
    Estimate3D,
    FloorAwareTracker,
    FloorDecision,
    MultiFloorLocalizer,
    RouteAssist,
    RouteState,
    anchor_index,
    anchor_key,
    decide_floor,
    floor_anchors,
    floor_scores,
    generate_measurements_3d,
    route_state,
    solve_3d,
    transition_windows,
    vertical_speed_limit,
)
from backend.locus_x.schemas import Settings

ROOT = Path(__file__).resolve().parents[2]
CONFIG_V3 = ROOT / "configs/environments/hospital_v3.json"

# Device z = elevation + target_device_height (1.2 m); anchors sit at elevation + 2.6 m.
DEVICE_Z = {"ground": 1.2, "first": 5.4, "second": 9.6}
FLOORS = list(DEVICE_Z.items())
MID_TRAVERSE_Z = 3.3
"""Halfway between the ground device height (1.2 m) and the first (5.4 m)."""


@pytest.fixture(scope="module")
def building():
    return Building.model_validate(json.loads(CONFIG_V3.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def anchors(building):
    return anchor_index(building)


def _state(x, y, z, floor_id, *, timestamp=0.0, phase="move"):
    return RouteState(
        timestamp=timestamp,
        period=100.0,
        phase=phase,
        floor_id=floor_id,
        x=x,
        y=y,
        z=z,
        connection_id=None,
        transition_id=None,
        progress=0.0,
    )


def _observe(building, settings, state, frame_index=0, subset=None):
    """Generate and key the observations by ``"<floor_id>:<anchor_id>"``."""
    sequence = list(floor_anchors(building)) if subset is None else list(subset)
    observations, labels, _ = generate_measurements_3d(
        building, settings, state, frame_index, anchors=subset
    )
    keys = [anchor_key(anchor.floor_id, anchor.anchor_id) for anchor in sequence]
    by_key = dict(zip(keys, labels, strict=True))
    remaining = iter(observations)
    return {key: next(remaining) for key in keys if by_key[key].available}


def _solve(building, observations, anchors):
    return solve_3d(
        observations,
        anchors,
        slab_bias_per_slab=building.slab_bias_per_slab,
        datums=[floor.elevation for floor in building.floors],
    )


def _estimate(z, *, x=21.0, y=14.0, observable=True):
    if not observable:
        return Estimate3D(
            position=None,
            horizontal=None,
            status="insufficient",
            reason="No accepted range fix",
            observable=False,
        )
    return Estimate3D(
        position=(x, y, z),
        horizontal=(x, y),
        status="valid",
        reason="Synthetic fix",
        observable=True,
        covariance=[[0.02, 0.0, 0.0], [0.0, 0.02, 0.0], [0.0, 0.0, 0.02]],
        condition_number=5.0,
        vertical_sigma=0.14,
        noise_sigma=0.15,
    )


def _decision(floor_id, *, ambiguous=False, confidence=0.99):
    return FloorDecision(
        floor_id=floor_id,
        ambiguous=ambiguous,
        traversing=floor_id is None,
        reason="Synthetic decision",
        confidence=confidence,
    )


# ------------------------------------------------------------------ 3D solve ----


@pytest.mark.parametrize(("floor_id", "z"), FLOORS)
def test_solve_3d_recovers_the_truth_on_every_floor_at_zero_noise(building, anchors, floor_id, z):
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=0, nlos_bias=0),
        _state(21.0, 14.0, z, floor_id),
    )
    assert len(observed) == 24
    estimate = _solve(building, observed, anchors)
    assert estimate.status == "valid"
    assert estimate.observable is True
    assert estimate.position == pytest.approx((21.0, 14.0, z), abs=1e-6)
    assert estimate.condition_number is not None
    assert math.isfinite(estimate.condition_number)
    assert estimate.condition_number <= 1000
    assert estimate.vertical_sigma is not None
    assert math.isfinite(estimate.vertical_sigma)
    assert max(abs(value) for value in estimate.residuals.values()) < 1e-4


@pytest.mark.parametrize(("floor_id", "z"), FLOORS)
def test_solve_3d_gains_vertical_accuracy_under_noise(building, anchors, floor_id, z):
    errors_z = []
    errors_horizontal = []
    for seed in range(5):
        for frame_index in range(2):
            observed = _observe(
                building,
                Settings(scenario="clean", noise_std=0.15, nlos_bias=0, seed=seed),
                _state(21.0, 14.0, z, floor_id),
                frame_index,
            )
            estimate = _solve(building, observed, anchors)
            assert estimate.observable is True
            assert estimate.position is not None
            errors_z.append(abs(estimate.position[2] - z))
            errors_horizontal.append(
                math.hypot(estimate.position[0] - 21.0, estimate.position[1] - 14.0)
            )
    # 24 anchors at 0.15 m: measured maxima were 0.34 m vertical and 0.13 m horizontal.
    assert max(errors_z) < 0.5
    assert sum(errors_z) / len(errors_z) < 0.25
    assert max(errors_horizontal) < 0.25


def test_coplanar_anchors_report_a_mirror_offset_and_never_a_storey(building, anchors):
    """One floor's anchors cannot say which side of the ceiling the device is on."""
    first_only = [anchor for anchor in floor_anchors(building) if anchor.floor_id == "first"]
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=0, nlos_bias=0),
        _state(21.0, 14.0, DEVICE_Z["first"], "first"),
        subset=first_only,
    )
    estimate = solve_3d(observed, anchors)
    assert estimate.observable is False
    assert estimate.status == "vertical_unobservable"
    assert estimate.position is None
    assert "vertical_unobservable" in estimate.reason
    assert estimate.horizontal == pytest.approx((21.0, 14.0), abs=1e-6)
    # The device is 1.4 m below the anchor plane, and a mirrored device 1.4 m above it
    # (z = 4.0 m) fits the same ranges -- an elevation between the two floor datums.
    assert estimate.mirror_offset == pytest.approx(1.4, abs=1e-6)

    scores = floor_scores(observed, building, anchors)
    decision = decide_floor(estimate, scores, building)
    assert decision.floor_id is None
    assert decision.ambiguous is True
    assert "vertical_unobservable" in decision.reason


# -------------------------------------------------------------- floor scoring ---


@pytest.mark.parametrize("noise", [0.0, 0.15, 0.4])
@pytest.mark.parametrize(("floor_id", "z"), FLOORS)
def test_floor_scores_pick_the_true_floor_on_clean_and_moderate_noise(
    building, anchors, floor_id, z, noise
):
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=noise, nlos_bias=0, seed=11),
        _state(21.0, 14.0, z, floor_id),
    )
    estimate = _solve(building, observed, anchors)
    scores = floor_scores(observed, building, anchors, noise_sigma=estimate.noise_sigma)
    assert scores.best_floor == floor_id
    assert scores.ambiguous is False
    assert scores.probability[floor_id] == max(scores.probability.values())
    assert scores.fit_ratio is not None
    assert scores.fit_ratio <= FIT_RATIO_LIMIT
    if noise <= 0.15:
        # At 0.4 m the scoring is still decisive but the 0.5 m absolute sanity bound on
        # the 3D fix can legitimately be exceeded by a single noisy frame, so only the
        # clean and moderate cases are asserted to name the floor end to end.
        decision = decide_floor(estimate, scores, building)
        assert decision.floor_id == floor_id
        assert decision.ambiguous is False


def test_three_same_floor_anchors_cannot_name_a_floor(building, anchors):
    """Deliberately poor geometry: three coplanar anchors are not a floor decision."""
    ground = [anchor for anchor in floor_anchors(building) if anchor.floor_id == "ground"]
    nearest = sorted(ground, key=lambda anchor: (anchor.x - 21.0) ** 2 + (anchor.y - 14.0) ** 2)[:3]
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=0, nlos_bias=0),
        _state(21.0, 14.0, DEVICE_Z["ground"], "ground"),
        subset=nearest,
    )
    estimate = solve_3d(observed, anchors)
    assert estimate.observable is False
    assert estimate.position is None
    assert estimate.status == "insufficient"

    scores = floor_scores(observed, building, anchors)
    assert scores.observations == 3
    decision = decide_floor(estimate, scores, building)
    assert decision.floor_id is None
    assert decision.ambiguous is True
    assert "fewer than 4" in decision.reason


def test_a_target_halfway_up_the_stairwell_is_not_a_confident_floor(building, anchors):
    """The false-confidence regression: a big margin must not outweigh a bad fit.

    Measured: the hypothesis margin is ~75 m^2 and the 3D fix sits at z = 3.3 m, but
    the best hypothesis is 28-32 noise variances away from the observations. Margin
    alone reports a confident (and wrong) storey here; the fit ratio does not.
    """
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=0.15, nlos_bias=0, seed=0),
        _state(2.5, 22.0, MID_TRAVERSE_Z, None, timestamp=15.0, phase="transition"),
    )
    estimate = _solve(building, observed, anchors)
    assert estimate.observable is True
    assert estimate.position is not None
    assert DEVICE_Z["ground"] < estimate.position[2] < DEVICE_Z["first"]

    scores = floor_scores(observed, building, anchors, noise_sigma=estimate.noise_sigma)
    assert scores.margin > 20.0, "the margin alone looks decisive; that is exactly the trap"
    assert scores.fit_ratio is not None
    assert scores.fit_ratio > FIT_RATIO_LIMIT, "the absolute fit must expose the misfit"
    assert scores.ambiguous is True
    assert "fit ratio" in scores.reason

    decision = decide_floor(estimate, scores, building)
    assert decision.floor_id is None
    assert decision.ambiguous is True
    assert decision.traversing is True

    localizer = MultiFloorLocalizer(building, mode="inferred")
    tracking = localizer.update(observed, timestamp=15.0).tracking
    assert tracking.floor_state.floor_id is None
    assert tracking.floor_state.ambiguous is True
    assert tracking.floor_state.confidence is None


@pytest.mark.parametrize(("floor_id", "z"), FLOORS)
def test_high_range_noise_makes_the_storey_undecidable(building, anchors, floor_id, z):
    """With 1.2 m noise 3 * sigma_z exceeds the 2.1 m half-gap: ambiguity is honest."""
    observed = _observe(
        building,
        Settings(scenario="high_noise", noise_std=1.2, nlos_bias=0, seed=2),
        _state(21.0, 14.0, z, floor_id),
    )
    estimate = _solve(building, observed, anchors)
    assert estimate.observable is True
    assert estimate.vertical_sigma is not None
    decision = decide_floor(
        estimate,
        floor_scores(observed, building, anchors, noise_sigma=estimate.noise_sigma),
        building,
    )
    assert decision.floor_id is None
    assert decision.ambiguous is True


# ------------------------------------------------------------------- leakage ----


def test_inferred_mode_cannot_see_the_truth_floor(building):
    """Changing the simulator's floor label must not change a single output byte."""
    settings = Settings(scenario="clean", noise_std=0.15, nlos_bias=0, walls_enabled=False, seed=3)
    # Same position and elevation, different truth label: only labels can differ.
    ground = _observe(
        building, settings, _state(9.0, 14.0, DEVICE_Z["ground"], "ground", timestamp=5.0)
    )
    second = _observe(
        building, settings, _state(9.0, 14.0, DEVICE_Z["ground"], "second", timestamp=5.0)
    )
    assert {key: value.model_dump() for key, value in ground.items()} == {
        key: value.model_dump() for key, value in second.items()
    }

    first_run = MultiFloorLocalizer(building, mode="inferred").update(ground, timestamp=5.0)
    second_run = MultiFloorLocalizer(building, mode="inferred").update(second, timestamp=5.0)
    assert first_run.scores.probability == second_run.scores.probability
    assert first_run.scores.sse == second_run.scores.sse
    assert first_run.decision == second_run.decision
    assert first_run.tracking.floor_state == second_run.tracking.floor_state
    assert first_run.tracking.position == second_run.tracking.position
    assert first_run.tracking.status == second_run.tracking.status

    with pytest.raises(ValueError, match="cannot receive route assistance"):
        MultiFloorLocalizer(building, mode="inferred").update(
            ground, timestamp=5.2, assist=RouteAssist(floor_id="second")
        )


# ------------------------------------------------------------------- tracker ----


def test_an_unphysical_floor_jump_is_rejected_with_the_previous_floor_held(building):
    limit = vertical_speed_limit(building)
    # The lift (4.2 m in 15 s) is the fastest configured traverse, so it is the bound.
    assert limit == pytest.approx(4.2 / 15.0)
    # 4.2 m in 0.2 s would need 21 m/s, far above any authored traverse.
    assert 4.2 > limit * 0.2

    tracker = FloorAwareTracker(building)
    tracker.update(_estimate(DEVICE_Z["ground"]), 0.0, decision=_decision("ground"))
    result = tracker.update(_estimate(DEVICE_Z["first"]), 0.2, decision=_decision("first"))

    assert result.floor_state.floor_id == "ground"
    assert result.floor_state.ambiguous is True
    assert result.floor_state.confidence is None
    events = tracker.drain_events()
    assert len(events) == 1
    assert events[0].level == "warning"
    assert "rejected" in events[0].message
    assert "ground -> first" in events[0].message


def test_updated_is_false_while_the_tracker_coasts(building):
    tracker = FloorAwareTracker(building)
    tracker.update(_estimate(DEVICE_Z["ground"]), 0.0, decision=_decision("ground"))

    coasting = tracker.update(
        _estimate(DEVICE_Z["ground"], observable=False), 0.2, decision=_decision("ground")
    )
    assert coasting.updated is False
    assert coasting.status == "degraded"
    assert coasting.position is not None
    assert coasting.z == pytest.approx(DEVICE_Z["ground"])

    withheld = tracker.update(
        _estimate(DEVICE_Z["ground"], observable=False), 3.0, decision=_decision("ground")
    )
    assert withheld.updated is False
    assert withheld.position is None
    assert withheld.z is None
    assert withheld.status == "unavailable"


def test_position_is_reacquired_after_a_long_outage(building):
    tracker = FloorAwareTracker(building)
    tracker.update(_estimate(DEVICE_Z["ground"]), 0.0, decision=_decision("ground"))
    for timestamp in (1.0, 3.2):
        tracker.update(
            _estimate(DEVICE_Z["ground"], observable=False),
            timestamp,
            decision=_decision("ground"),
        )

    recovered = tracker.update(
        _estimate(DEVICE_Z["ground"] + 0.05), 3.4, decision=_decision("ground")
    )
    assert recovered.updated is True
    assert recovered.status == "tracking"
    assert recovered.position is not None
    assert recovered.position[2] == pytest.approx(DEVICE_Z["ground"] + 0.05)
    assert any("reacquired" in event.message.lower() for event in tracker.drain_events())


def test_the_floor_is_never_a_confident_value_while_ambiguous(building):
    tracker = FloorAwareTracker(building)
    tracker.update(_estimate(DEVICE_Z["ground"]), 0.0, decision=_decision("ground"))
    ambiguous = tracker.update(
        _estimate(MID_TRAVERSE_Z), 0.2, decision=_decision(None, ambiguous=True)
    )
    assert ambiguous.floor_state.floor_id is None
    assert ambiguous.floor_state.ambiguous is True
    assert ambiguous.floor_state.confidence is None


# ---------------------------------------------------------------- localizer -----


def test_both_modes_produce_a_frame_ready_tracking_state(building):
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=0.15, nlos_bias=0, seed=4),
        _state(21.0, 14.0, DEVICE_Z["ground"], "ground"),
    )

    inferred = MultiFloorLocalizer(building, mode="inferred")
    tracking = inferred.update(observed, timestamp=0.0).tracking
    assert tracking.position is not None
    assert len(tracking.position) == 3
    assert tracking.z == pytest.approx(tracking.position[2])
    assert tracking.velocity is not None and len(tracking.velocity) == 3
    assert tracking.covariance is not None
    assert len(tracking.covariance) == 3
    assert all(len(row) == 3 for row in tracking.covariance)
    assert tracking.ellipse is not None
    assert tracking.floor_state.floor_id == "ground"
    assert tracking.floor_state.source == "inferred"
    assert tracking.floor_state.ambiguous is False
    assert tracking.transition is None
    assert tracking.updated is True
    assert tracking.status == "tracking"

    assisted = MultiFloorLocalizer(building, mode="route_assisted")
    result = assisted.update(observed, timestamp=0.0, assist=RouteAssist(floor_id="ground"))
    assert result.tracking.floor_state.floor_id == "ground"
    assert result.tracking.floor_state.source == "route_assisted"
    assert result.tracking.floor_state.ambiguous is False


def test_route_assisted_mode_reports_the_traverse_without_naming_a_storey(building):
    route = next(route for route in building.transition_routes if route.id == "ascent_stairs")
    window = transition_windows(building, route)[0]
    timestamp = (window.start + window.end) / 2
    state = route_state(building, route, timestamp)
    assert state.floor_id is None
    observed = _observe(
        building,
        Settings(scenario="clean", noise_std=0.15, nlos_bias=0, seed=1),
        state,
        frame_index=0,
    )
    assist = RouteAssist(
        floor_id=None,
        transition_id=state.transition_id,
        connection_id=state.connection_id,
        from_floor_id="ground",
        to_floor_id="first",
        progress=state.progress,
    )
    tracking = (
        MultiFloorLocalizer(building, mode="route_assisted")
        .update(observed, timestamp=timestamp, assist=assist)
        .tracking
    )
    assert tracking.floor_state.floor_id is None
    assert tracking.floor_state.source == "route_assisted"
    assert tracking.floor_state.ambiguous is True
    assert tracking.transition is not None
    assert tracking.transition.phase == "traversing"
    assert tracking.transition.transition_id == state.transition_id
    assert tracking.transition.progress == pytest.approx(0.5)
    assert tracking.position is not None
