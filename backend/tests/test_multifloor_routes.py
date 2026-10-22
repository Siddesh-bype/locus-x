"""Multi-floor route geometry validation, trajectory continuity and outage overlap."""

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.locus_x.building import Building
from backend.locus_x.multifloor import (
    DEFAULT_ROUTE_SPEED,
    RouteState,
    route_state,
    transition_windows,
)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_V3 = ROOT / "configs/environments/hospital_v3.json"

SPEED = DEFAULT_ROUTE_SPEED
SAMPLE = 0.05
OUTAGE = (12.0, 22.0)
OUTAGE_PERIOD = 40.0
ROUTE_IDS = [
    "ascent_stairs",
    "ascent_lift",
    "descent_stairs",
    "transition_pause",
    "cross_floor_survey",
]


@pytest.fixture(scope="module")
def building():
    raw = json.loads(CONFIG_V3.read_text(encoding="utf-8"))
    return Building.model_validate(raw)


@pytest.fixture(scope="module")
def routes(building):
    return {route.id: route for route in building.transition_routes}


@pytest.fixture
def raw_v3():
    return json.loads(CONFIG_V3.read_text(encoding="utf-8"))


def _floors(building):
    return {floor.id: floor for floor in building.floors}


# ------------------------------------------------------------- continuity -----


@pytest.mark.parametrize("route_id", ROUTE_IDS)
def test_configured_routes_are_continuous_over_three_periods(building, routes, route_id):
    route = routes[route_id]
    period = route_state(building, route, 0.0).period
    floors = _floors(building)
    vertical_rate = max(
        abs(floors[transition.to_floor_id].elevation - floors[transition.from_floor_id].elevation)
        / transition.travel_seconds
        for transition in route.transitions
    )
    horizontal_limit = SPEED * SAMPLE
    vertical_limit = vertical_rate * SAMPLE
    previous = route_state(building, route, 0.0)
    steps = int(3 * period / SAMPLE)
    for step in range(1, steps + 1):
        state = route_state(building, route, step * SAMPLE)
        horizontal = math.dist((previous.x, previous.y), (state.x, state.y))
        assert horizontal <= horizontal_limit + 1e-6, (
            f"{route_id} jumps {horizontal:.6f} m horizontally at t={state.timestamp:.3f} s"
        )
        assert abs(state.z - previous.z) <= vertical_limit + 1e-6, (
            f"{route_id} jumps {abs(state.z - previous.z):.6f} m vertically "
            f"at t={state.timestamp:.3f} s"
        )
        previous = state


@pytest.mark.parametrize("route_id", ROUTE_IDS)
def test_traverse_boundaries_are_continuous(building, routes, route_id):
    route = routes[route_id]
    period = route_state(building, route, 0.0).period
    floor_z = {
        floor.id: floor.elevation + building.target_device_height for floor in building.floors
    }
    for transition, (start, end) in zip(
        route.transitions, transition_windows(building, route), strict=True
    ):
        entering = route_state(building, route, start + 1e-9)
        leaving = route_state(building, route, end - 1e-9)
        # Core entries are identical on all three floors (Building enforces vertical
        # alignment and the fixture repeats the door midpoint), so a traverse moves z
        # only: horizontal position is *exactly* equal, not merely close.
        assert (entering.x, entering.y) == (leaving.x, leaving.y)
        assert entering.z == pytest.approx(floor_z[transition.from_floor_id], abs=1e-9)
        assert leaving.z == pytest.approx(floor_z[transition.to_floor_id], abs=1e-9)
        if start >= 1e-6:
            before = route_state(building, route, start - 1e-9)
            assert math.dist((before.x, before.y), (entering.x, entering.y)) <= 1e-6
            assert before.z == pytest.approx(entering.z, abs=1e-6)
        if end <= period - 1e-6:
            after = route_state(building, route, end + 1e-9)
            assert math.dist((after.x, after.y), (leaving.x, leaving.y)) <= 1e-6
            assert after.z == pytest.approx(leaving.z, abs=1e-6)


@pytest.mark.parametrize("route_id", ROUTE_IDS)
def test_floor_id_is_none_exactly_during_traversal(building, routes, route_id):
    route = routes[route_id]
    period = route_state(building, route, 0.0).period
    windows = transition_windows(building, route)
    assert len(windows) == len(route.transitions)
    for transition, (start, end) in zip(route.transitions, windows, strict=True):
        middle = route_state(building, route, (start + end) / 2)
        assert middle.phase == "transition"
        assert middle.floor_id is None
        assert middle.transition_id == transition.id
        assert middle.connection_id == transition.connection_id
        assert 0 < middle.progress < 1
        assert (middle.x, middle.y) == pytest.approx(
            _entry(building, transition.connection_id, transition.from_floor_id), abs=1e-9
        )
        if start >= 1e-6:
            before = route_state(building, route, start - 1e-9)
            assert before.floor_id == transition.from_floor_id
            assert before.transition_id is None
        if end <= period - 1e-6:
            after = route_state(building, route, end + 1e-9)
            assert after.floor_id == transition.to_floor_id
            assert after.transition_id is None


@pytest.mark.parametrize("route_id", ROUTE_IDS)
def test_z_is_the_linear_interpolation_at_the_midpoint_of_each_traverse(building, routes, route_id):
    route = routes[route_id]
    floors = _floors(building)
    height = building.target_device_height
    for transition, (start, end) in zip(
        route.transitions, transition_windows(building, route), strict=True
    ):
        middle = route_state(building, route, (start + end) / 2)
        expected = (
            floors[transition.from_floor_id].elevation + floors[transition.to_floor_id].elevation
        ) / 2 + height
        assert middle.z == pytest.approx(expected, abs=1e-9)
        assert middle.progress == pytest.approx(0.5, abs=1e-9)


def test_one_way_routes_clamp_at_the_final_waypoint(building, routes):
    for route_id in ["ascent_stairs", "ascent_lift", "descent_stairs", "transition_pause"]:
        route = routes[route_id]
        period = route_state(building, route, 0.0).period
        last = route.legs[-1]
        point = last.waypoints[-1].position
        elevation = _floors(building)[last.floor_id].elevation
        for timestamp in (period, period + 5, 10 * period):
            state = route_state(building, route, timestamp)
            assert state.phase == "dwell"
            assert state.floor_id == last.floor_id
            assert (state.x, state.y) == point
            assert state.z == pytest.approx(elevation + building.target_device_height)


def test_loop_routes_repeat_with_the_period(building, routes):
    route = routes["cross_floor_survey"]
    period = route_state(building, route, 0.0).period
    for timestamp in (0.0, 3.3, 17.0, 60.0, 120.0, period - 0.05):
        once = route_state(building, route, timestamp)
        twice = route_state(building, route, timestamp + period)
        assert (twice.phase, twice.floor_id) == (once.phase, once.floor_id)
        assert (twice.x, twice.y, twice.z) == pytest.approx((once.x, once.y, once.z), abs=1e-9)


def test_route_state_carries_the_contract_fields(building, routes):
    state = route_state(building, routes["ascent_stairs"], 20.0)
    assert isinstance(state, RouteState)
    assert (state.phase, state.floor_id) == ("transition", None)
    assert (state.transition_id, state.connection_id) == ("ascent-stairs-1", "west-stairs")
    assert 0 < state.progress < 1
    assert state.period == pytest.approx(117.083734, abs=1e-6)


@pytest.mark.parametrize("timestamp", [-0.001, -5])
def test_negative_timestamps_are_rejected(building, routes, timestamp):
    with pytest.raises(ValueError, match="Timestamp must be non-negative"):
        route_state(building, routes["ascent_stairs"], timestamp)


def test_nonpositive_speed_and_device_height_are_rejected(building, routes):
    route = routes["ascent_stairs"]
    with pytest.raises(ValueError, match="Speed must be positive"):
        route_state(building, route, 1.0, speed=0)
    with pytest.raises(ValueError, match="Device height must be positive"):
        route_state(building, route, 1.0, device_height=-1)


def test_device_height_override_shifts_every_elevation(building, routes):
    route = routes["ascent_stairs"]
    floors = _floors(building)
    ground = route_state(building, route, 0.0, device_height=1.7)
    assert ground.z == pytest.approx(floors["ground"].elevation + 1.7)
    start, end = transition_windows(building, route, device_height=1.7)[0]
    middle = route_state(building, route, (start + end) / 2, device_height=1.7)
    expected = (floors["ground"].elevation + floors["first"].elevation) / 2 + 1.7
    assert middle.z == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------- outage overlap ----


def _overlaps_outage(start: float, end: float) -> bool:
    """``[start, end)`` meets some ``k * 40 + [12, 22)``.

    The first outage window that can still be open after ``start`` begins at
    ``k = floor((start - 22) / 40) + 1``; its end is ``40k + 22``.
    """
    k = math.floor((start - OUTAGE[1]) / OUTAGE_PERIOD) + 1
    return OUTAGE_PERIOD * k + OUTAGE[0] < end


@pytest.mark.parametrize("route_id", ROUTE_IDS)
def test_every_traverse_overlaps_the_timed_outage(building, routes, route_id):
    """The simulator's fixed outage is ``12 <= timestamp % 40 < 22``.

    That window is scenario-gated (``simulation.py:26-31`` fires only for
    ``anchor_failure``/``chaos``), but the overlap is a property of the authored
    timings: a route whose traverse can never coincide with the outage cannot be
    used to exercise a vertical handover.

    Per route, at 1.2 m/s (windows modulo 40; a window crossing 40 is split):

        ascent_stairs     15.46 % 40 = 15.46 -> [15.46, 22) inside
                          51.30 % 40 = 11.30 -> [11.30, 31.30) covers [12, 22)
        ascent_lift       17.92 % 40 = 17.92 -> [17.92, 22) inside
                          53.68 % 40 = 13.68 -> [13.68, 28.68) covers [13.68, 22)
        descent_stairs    52.00 % 40 = 12.00 -> [12.00, 22) inside
                          87.84 % 40 =  7.84 -> [7.84, 27.84) covers [12, 22)
        transition_pause  15.46 % 40 = 15.46 -> [15.46, 22) inside
        cross_floor       15.84 % 40 = 15.84 -> [15.84, 22) inside
                          81.62 % 40 =  1.62 -> [1.62, 21.62) covers [12, 21.62)
                         117.46 % 40 = 37.46 -> [37.46, 40) + [0, 17.46) covers [12, 17.46)
                         159.88 % 40 = 39.88 -> [39.88, 40) + [0, 19.88) covers [12, 19.88)
    """
    route = routes[route_id]
    windows = transition_windows(building, route)
    assert windows
    for start, end in windows:
        assert _overlaps_outage(start, end), (
            f"{route_id} traverse [{start:.2f}, {end:.2f}) never meets the outage "
            f"{OUTAGE} modulo {OUTAGE_PERIOD}"
        )
        # The same fact, checked by sampling: at least one sampled instant inside the
        # traverse is in the outage phase of the cycle.
        assert any(
            OUTAGE[0] <= (start + offset) % OUTAGE_PERIOD < OUTAGE[1]
            for offset in [0.05 * step for step in range(int((end - start) / 0.05) + 1)]
        )


def test_outage_helper_rejects_a_window_that_misses_it():
    assert _overlaps_outage(15.0, 35.0)
    assert _overlaps_outage(35.0, 55.0)  # crosses 40: [35, 40) + [0, 15) meets [12, 15)
    assert not _overlaps_outage(39.0, 45.0)  # [39, 40) + [0, 5) misses 12..22
    assert not _overlaps_outage(0.0, 11.9)


# ------------------------------------------------------------- rejections -----


def _leg(floor_id, positions):
    return {
        "floor_id": floor_id,
        "waypoints": [{"position": list(point), "dwell": 0} for point in positions],
    }


def _transition(**overrides):
    payload = {
        "id": "probe-1",
        "kind": "stairs",
        "connection_id": "west-stairs",
        "from_floor_id": "ground",
        "to_floor_id": "first",
        "travel_seconds": 20,
        "dwell_before": 0,
        "dwell_after": 0,
    }
    return {**payload, **overrides}


def _probe_route(legs=None, transitions=None, loop=False):
    return {
        "id": "probe",
        "name": "Probe route",
        "loop": loop,
        "legs": legs
        if legs is not None
        else [_leg("ground", [(4, 14), (2.5, 22)]), _leg("first", [(2.5, 22), (4, 19)])],
        "transitions": transitions if transitions is not None else [_transition()],
    }


def test_the_probe_route_used_by_rejection_tests_is_valid(raw_v3):
    raw_v3["transition_routes"] = [_probe_route()]
    building = Building.model_validate(raw_v3)
    assert [route.id for route in building.transition_routes] == ["probe"]


@pytest.mark.parametrize(
    "route, message",
    [
        (
            _probe_route(transitions=[_transition(connection_id="roof-lift")]),
            "unknown connection 'roof-lift'",
        ),
        (_probe_route(transitions=[_transition(kind="elevator")]), "does not match connection"),
        (
            _probe_route(
                legs=[_leg("ground", [(4, 14), (4, 19)]), _leg("first", [(2.5, 22), (4, 19)])]
            ),
            "leg 0 on floor ground must end at the 'stair-west' entry",
        ),
        (
            _probe_route(
                legs=[_leg("ground", [(4, 14), (2.5, 22)]), _leg("first", [(4, 19), (4, 14)])]
            ),
            "leg 1 on floor first must start at the 'stair-west' entry",
        ),
        (
            # The corridor/east-wing wall at x = 8 stands between both endpoints.
            _probe_route(
                legs=[_leg("ground", [(10, 20), (2.5, 22)]), _leg("first", [(2.5, 22), (4, 19)])]
            ),
            "leg 0 on floor ground crosses a solid wall",
        ),
        (
            # Inside the stair shaft, which is an access core, never a movement region.
            _probe_route(
                legs=[_leg("ground", [(2.5, 26), (2.5, 22)]), _leg("first", [(2.5, 22), (4, 19)])]
            ),
            "leg 0 on floor ground leaves valid movement regions",
        ),
        (
            # ground -> second is two stops apart on west-stairs (ground, first, second).
            _probe_route(
                legs=[_leg("ground", [(4, 14), (2.5, 22)]), _leg("second", [(2.5, 22), (4, 19)])],
                transitions=[_transition(to_floor_id="second")],
            ),
            "must connect floors that are adjacent in connection 'west-stairs'",
        ),
        (
            # The transition names the right stops but the legs are on the wrong floors.
            _probe_route(transitions=[_transition(from_floor_id="first", to_floor_id="ground")]),
            "but joins leg 0 on floor 'ground'",
        ),
        (_probe_route(transitions=[]), "needs exactly 1 transitions"),
        (
            _probe_route(
                legs=[
                    _leg("ground", [(4, 14), (2.5, 22)]),
                    _leg("first", [(2.5, 22), (4, 19)]),
                    _leg("second", [(2.5, 22), (4, 19)]),
                ],
                transitions=[_transition()],
            ),
            "needs exactly 2 transitions",
        ),
    ],
    ids=[
        "unknown_connection",
        "kind_mismatch",
        "leg_not_ending_on_entry",
        "leg_not_starting_on_entry",
        "wall_crossing",
        "outside_movement_regions",
        "non_adjacent_stops",
        "transition_leg_floor_mismatch",
        "one_way_count",
        "three_leg_count",
    ],
)
def test_invalid_route_geometry_is_rejected(raw_v3, route, message):
    raw_v3["transition_routes"] = [route]
    with pytest.raises(ValidationError, match=message):
        Building.model_validate(raw_v3)


def test_connection_without_a_stop_on_the_named_floor_is_rejected(raw_v3):
    # A connection whose stop list omits the target floor cannot support the traverse;
    # drop the second stair stop so the elevation/linkage checks still pass.
    raw_v3["connections"][0]["stops"] = raw_v3["connections"][0]["stops"][:2]
    raw_v3["floors"][2]["access_cores"] = [
        core for core in raw_v3["floors"][2]["access_cores"] if core["id"] != "stair-west"
    ]
    raw_v3["floors"][2]["doors"] = [
        door for door in raw_v3["floors"][2]["doors"] if "stair-west" not in door["connects"]
    ]
    raw_v3["transition_routes"] = [
        _probe_route(
            legs=[_leg("ground", [(4, 14), (2.5, 22)]), _leg("second", [(2.5, 22), (4, 19)])],
            transitions=[_transition(to_floor_id="second")],
        )
    ]
    with pytest.raises(ValidationError, match="has no stop on floor 'second'"):
        Building.model_validate(raw_v3)


# ------------------------------------------------------------------ helpers ---


def _entry(building, connection_id, floor_id):
    connection = next(c for c in building.connections if c.id == connection_id)
    stop = next(stop for stop in connection.stops if stop.floor_id == floor_id)
    floor = next(floor for floor in building.floors if floor.id == floor_id)
    core = next(core for core in floor.access_cores if core.id == stop.access_id)
    return core.entry
