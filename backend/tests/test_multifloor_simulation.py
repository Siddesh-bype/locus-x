"""True 3D ranges, slab attenuation and per-floor faults.

The legacy 2D generator is pinned by golden values captured before the 3D model existed;
a "3D equals legacy" comparison would be wrong by design, because the 3D range includes
the vertical offset and the cross-floor bias.
"""

import json
import math
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.locus_x.building import Building, validate_anchor_keys
from backend.locus_x.environment import load_environments
from backend.locus_x.multifloor import (
    MAX_SLABS,
    OUTAGE_INDICES,
    RouteState,
    anchor_index,
    anchor_key,
    count_slab_hops,
    floor_anchors,
    generate_measurements_3d,
    resolve_anchor,
    route_state,
    transition_windows,
)
from backend.locus_x.schemas import Controls, Settings
from backend.locus_x.simulation import generate_measurements

ROOT = Path(__file__).resolve().parents[2]
CONFIG_V3 = ROOT / "configs/environments/hospital_v3.json"

# Device z = elevation + 1.2, anchor z = elevation + 2.6, floor spacing 4.2 m.
DEVICE_Z = {"ground": 1.2, "first": 5.4, "second": 9.6}

# Captured from the untouched legacy generator before this task, for
# hospital_floor_01 with seed 123. Exact floats: the draw order is the contract.
LEGACY_CLEAN_RANGES = [
    ("A1", 13.341664064126334),
    ("A2", 17.029386365926403),
    ("A3", 28.178005607210743),
    ("A4", 25.317977802344327),
    ("A5", 25.495097567963924),
    ("A6", 12.083045973594572),
    ("A7", 5.830951894845301),
    ("A8", 5.0),
]
LEGACY_CHAOS_RANGES = [
    ("A1", 29.81031911508251),
    ("A5", 9.05177663249196),
    ("A7", 24.428527072989397),
]
LEGACY_CHAOS_LABELS = [
    ("A1", "nlos", 6.0, ["south-2", "s-divider-1", "north-3", "n-divider-2"]),
    ("A2", "outage", 4.0, ["south-3", "north-3"]),
    ("A3", "packet_loss", 4.0, ["south-4", "north-3"]),
    ("A4", "outage", 2.0, ["north-3"]),
    ("A5", "los", 0.0, []),
    ("A6", "outage", 2.0, ["n-divider-2"]),
    ("A7", "nlos", 4.0, ["n-divider-1", "n-divider-2"]),
    ("A8", "outage", 4.0, ["north-3", "n-divider-2"]),
]
LEGACY_FAILURE_RANGES = [
    ("A1", 13.110644425147122),
    ("A3", 28.505020885555876),
    ("A5", 25.10964853418182),
    ("A7", 5.787707238894209),
]


@pytest.fixture(scope="module")
def building():
    return Building.model_validate(json.loads(CONFIG_V3.read_text(encoding="utf-8")))


@pytest.fixture
def raw_v3():
    return json.loads(CONFIG_V3.read_text(encoding="utf-8"))


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


def _observe(building, settings, state, frame_index=0, anchors=None):
    """Generate and re-index the result by ``"<floor_id>:<anchor_id>"``."""
    sequence = list(floor_anchors(building)) if anchors is None else list(anchors)
    observations, labels, slab_hops = generate_measurements_3d(
        building, settings, state, frame_index, anchors=anchors
    )
    keys = [anchor_key(anchor.floor_id, anchor.anchor_id) for anchor in sequence]
    by_key = dict(zip(keys, labels, strict=True))
    remaining = iter(observations)
    observed = {key: next(remaining) for key in keys if by_key[key].available}
    assert list(remaining) == []
    assert set(slab_hops) == set(keys)
    return observed, by_key, slab_hops


def _perimeter_outage(building):
    """The four perimeter anchors of every floor that fail during the timed outage."""
    return {f"{floor.id}:A{index + 1}" for floor in building.floors for index in OUTAGE_INDICES}


def _five_storey(raw_v3):
    """Ground floor cloned to five storeys so the slab attenuation can saturate."""
    floors = []
    for index in range(5):
        floor = deepcopy(raw_v3["floors"][0])
        floor["id"] = f"F{index}"
        floor["short_name"] = f"F{index}"
        floor["elevation"] = index * 4.2
        floors.append(floor)
    raw = {**raw_v3, "floors": floors, "default_floor_id": "F0", "transition_routes": []}
    raw["connections"] = [
        {
            "id": "west-stairs",
            "kind": "stairs",
            "stops": [{"floor_id": f"F{i}", "access_id": "stair-west"} for i in range(5)],
        },
        {
            "id": "bed-elevator",
            "kind": "elevator",
            "stops": [{"floor_id": f"F{i}", "access_id": "lift-west"} for i in range(5)],
        },
    ]
    return Building.model_validate(raw)


# ------------------------------------------------------------- 3D ranges ------


@pytest.mark.parametrize(
    "floor_id, device_z, key, expected_range, expected_hops",
    [
        ("ground", 1.2, "ground:A1", 23.8948, 0),
        ("ground", 1.2, "ground:A2", 13.0752, 0),
        ("ground", 1.2, "first:A1", 24.5022, 1),
        ("ground", 1.2, "second:A1", 25.7884, 2),
        ("first", 5.4, "ground:A1", 24.0175, 1),
        ("first", 5.4, "second:A1", 24.5022, 1),
        ("second", 9.6, "ground:A1", 24.8596, 2),
    ],
)
def test_true_range_is_the_3d_distance_including_the_vertical_offset(
    building, floor_id, device_z, key, expected_range, expected_hops
):
    anchor = resolve_anchor(building, *key.split(":"))
    horizontal = math.dist((21.0, 14.0), (anchor.x, anchor.y))
    settings = Settings(noise_std=0, nlos_bias=0)
    observed, labels, slab_hops = _observe(
        building, settings, _state(21.0, 14.0, device_z, floor_id)
    )
    label = labels[key]
    assert label.true_range == pytest.approx(expected_range, abs=1e-4)
    assert label.true_range == pytest.approx(math.hypot(horizontal, device_z - anchor.z), abs=1e-9)
    assert label.true_range > horizontal
    assert slab_hops[key] == expected_hops
    # No noise and no wall bias, so the observation is the geometry plus slab attenuation.
    assert observed[key].observed_range == pytest.approx(
        expected_range + building.slab_bias_per_slab * expected_hops, abs=1e-4
    )


def test_a_target_directly_under_an_anchor_reports_the_vertical_gap(building):
    settings = Settings(noise_std=0, nlos_bias=0)
    observed, labels, slab_hops = _observe(building, settings, _state(1.0, 1.0, 5.4, "first"))
    assert labels["first:A1"].true_range == pytest.approx(1.4)
    assert labels["first:A1"].injected_bias == 0
    assert slab_hops["first:A1"] == 0
    assert observed["first:A1"].observed_range == pytest.approx(1.4)


def test_measurements_keep_the_floor_local_two_dimensional_projection(building):
    settings = Settings(noise_std=0, nlos_bias=0)
    observed, _, _ = _observe(building, settings, _state(1.0, 1.0, 5.4, "first"))
    measurement = observed["first:A1"]
    assert measurement.anchor_position == (1.0, 1.0)
    assert len(measurement.anchor_position) == 2
    assert measurement.observed_range == pytest.approx(1.4)


# ------------------------------------------------- same-floor obstruction -----


def test_same_floor_walls_reproduce_the_legacy_bias_exactly(building):
    settings = Settings(scenario="nlos", seed=123)
    floor = building.floors[0]
    _, legacy_labels = generate_measurements(floor, settings, (21.0, 14.0), 0.0, 0)
    _, labels, _ = _observe(building, settings, _state(21.0, 14.0, 1.2, "ground"))
    for legacy, anchor in zip(legacy_labels, floor.anchors, strict=True):
        label = labels[f"ground:{anchor.id}"]
        assert (label.kind, label.available, label.injected_bias, label.intersected_walls) == (
            legacy.kind,
            legacy.available,
            legacy.injected_bias,
            legacy.intersected_walls,
        )
        assert label.injected_bias == pytest.approx(
            settings.nlos_bias * min(len(label.intersected_walls), 3) / 2
        )
    # At least one anchor crosses the three-wall cap, or the cap would be untested here.
    ground_walls = [len(labels[f"ground:A{index}"].intersected_walls) for index in range(1, 9)]
    assert max(ground_walls) == 3


def test_noise_draws_match_the_legacy_stream_for_same_floor_anchors(building):
    settings = Settings(scenario="chaos", seed=41)
    floor = building.floors[0]
    legacy_observations, legacy_labels = generate_measurements(
        floor, settings, (21.0, 14.0), 6.5, 3
    )
    observed, labels, _ = _observe(
        building, settings, _state(21.0, 14.0, 1.2, "ground", timestamp=6.5), 3
    )
    residuals = []
    legacy_by_id = {m.anchor_id: m for m in legacy_observations}
    for legacy, anchor in zip(legacy_labels, floor.anchors, strict=True):
        label = labels[f"ground:{anchor.id}"]
        assert label.kind == legacy.kind
        if not label.available:
            continue
        mine = observed[f"ground:{anchor.id}"].observed_range
        legacy_measurement = legacy_by_id[anchor.id]
        legacy_residual = (
            legacy_measurement.observed_range - legacy.true_range - legacy.injected_bias
        )
        assert mine - label.true_range - label.injected_bias == pytest.approx(
            legacy_residual, abs=1e-9
        )
        residuals.append(legacy_residual)
    assert residuals
    assert any(abs(residual) > 1e-6 for residual in residuals)


def test_cross_floor_anchors_report_no_wall_hits(building):
    settings = Settings(scenario="nlos", seed=123)
    first = building.floors[1]
    _, same_floor_labels = generate_measurements(first, settings, (21.0, 14.0), 0.0, 0)
    assert any(label.intersected_walls for label in same_floor_labels)
    _, labels, slab_hops = _observe(building, settings, _state(21.0, 14.0, 1.2, "ground"))
    for floor_id in ("first", "second"):
        for index in range(1, 9):
            label = labels[f"{floor_id}:A{index}"]
            assert label.intersected_walls == []
            assert label.kind == "nlos"
            assert label.injected_bias == pytest.approx(
                building.slab_bias_per_slab * slab_hops[f"{floor_id}:A{index}"]
            )


def test_disabling_walls_keeps_the_slab_term_and_drops_the_wall_term(building):
    state = _state(21.0, 14.0, 1.2, "ground")
    enabled = Settings(scenario="nlos", nlos_bias=2.5, noise_std=0)
    _, enabled_labels, _ = _observe(building, enabled, state)
    # (21, 14) -> ground:A2 crosses reception-e, so the wall term is live when enabled.
    assert enabled_labels["ground:A2"].injected_bias > 0
    assert enabled_labels["ground:A2"].kind == "nlos"

    settings = Settings(scenario="nlos", walls_enabled=False, nlos_bias=2.5, noise_std=0)
    observed, labels, _ = _observe(building, settings, state)
    blocked = labels["ground:A2"]
    assert (blocked.intersected_walls, blocked.injected_bias, blocked.kind) == ([], 0, "los")
    cross = labels["first:A1"]
    assert cross.intersected_walls == []
    assert cross.injected_bias == pytest.approx(building.slab_bias_per_slab)
    assert cross.kind == "nlos"
    assert observed["first:A1"].observed_range - cross.true_range == pytest.approx(
        building.slab_bias_per_slab, abs=1e-9
    )


# ------------------------------------------------------- slab attenuation -----


def test_cross_floor_range_adds_one_bias_step_per_slab(building):
    settings = Settings(noise_std=0, nlos_bias=0)
    observed, labels, slab_hops = _observe(building, settings, _state(21.0, 14.0, 1.2, "ground"))
    for index in range(1, 9):
        ground = labels[f"ground:A{index}"]
        one_floor = labels[f"first:A{index}"]
        two_floors = labels[f"second:A{index}"]
        assert (slab_hops[f"ground:A{index}"], slab_hops[f"first:A{index}"]) == (0, 1)
        assert slab_hops[f"second:A{index}"] == 2
        assert one_floor.injected_bias == pytest.approx(building.slab_bias_per_slab)
        assert two_floors.injected_bias == pytest.approx(2 * building.slab_bias_per_slab)
        assert observed[f"first:A{index}"].observed_range - one_floor.true_range == pytest.approx(
            building.slab_bias_per_slab, abs=1e-9
        )
        anchor = resolve_anchor(building, "ground", f"A{index}")
        assert ground.true_range == pytest.approx(
            math.dist((21.0, 14.0, 1.2), (anchor.x, anchor.y, anchor.z)), abs=1e-9
        )


@pytest.mark.parametrize(
    "z, expected_range, expected_hops",
    [
        (1.2, 21.1000, 0),
        (2.0, 21.0621, 0),
        (2.6, 21.0535, 0),
        (3.0, 21.0573, 0),
        (4.2, 21.1142, 0),
        (4.5, 21.1391, 1),
        (5.4, 21.2389, 1),
    ],
)
def test_slab_hops_step_as_the_target_crosses_a_datum(building, z, expected_range, expected_hops):
    settings = Settings(noise_std=0, nlos_bias=0)
    observed, labels, slab_hops = _observe(
        building,
        settings,
        _state(2.5, 22.0, z, None, timestamp=15.0, phase="transition"),
    )
    label = labels["ground:A1"]
    assert label.true_range == pytest.approx(expected_range, abs=1e-4)
    assert slab_hops["ground:A1"] == expected_hops
    assert observed["ground:A1"].observed_range == pytest.approx(
        expected_range + building.slab_bias_per_slab * expected_hops, abs=1e-4
    )


def test_range_is_minimal_at_the_anchor_elevation_not_monotonic(building):
    settings = Settings(noise_std=0, nlos_bias=0)
    ranges = {}
    for z in (1.2, 2.0, 2.6, 3.0, 4.2):
        _, labels, _ = _observe(
            building, settings, _state(2.5, 22.0, z, None, timestamp=15.0, phase="transition")
        )
        ranges[z] = labels["ground:A1"].true_range
    assert ranges[1.2] > ranges[2.0] > ranges[2.6]
    assert ranges[2.6] < ranges[3.0] < ranges[4.2]


def test_half_height_traverse_sees_one_slab_between_the_floor_bounds(building):
    settings = Settings(noise_std=0, nlos_bias=0)
    _, labels, slab_hops = _observe(
        building, settings, _state(2.5, 22.0, 2.7, None, timestamp=15.0, phase="transition")
    )
    ground_bound = _observe(building, settings, _state(2.5, 22.0, 1.2, "ground"))[1]
    first_bound = _observe(building, settings, _state(2.5, 22.0, 5.4, "first"))[1]
    mid = labels["first:A1"]
    assert mid.true_range == pytest.approx(21.449, abs=1e-3)
    assert first_bound["first:A1"].true_range < mid.true_range < ground_bound["first:A1"].true_range
    assert slab_hops["first:A1"] == 1
    assert slab_hops["ground:A1"] == 0


def test_a_mid_traverse_state_is_wall_free_and_counts_the_slab(building):
    route = next(route for route in building.transition_routes if route.id == "ascent_stairs")
    window = transition_windows(building, route)[0]
    state = route_state(building, route, (window.start + window.end) / 2)
    assert (state.phase, state.floor_id) == ("transition", None)
    assert state.z == pytest.approx((DEVICE_Z["ground"] + DEVICE_Z["first"]) / 2)
    _, labels, slab_hops = _observe(building, Settings(scenario="chaos", seed=5), state)
    assert all(label.intersected_walls == [] for label in labels.values())
    assert {slab_hops[f"ground:A{index}"] for index in range(1, 9)} == {0}
    assert {slab_hops[f"first:A{index}"] for index in range(1, 9)} == {1}
    assert {slab_hops[f"second:A{index}"] for index in range(1, 9)} == {2}


def test_count_slab_hops_uses_strict_inequalities():
    datums = [0.0, 4.2, 8.4, 12.6, 16.8]
    assert count_slab_hops(1.2, 19.4, datums) == 4
    assert count_slab_hops(19.4, 1.2, datums) == 4
    assert count_slab_hops(1.2, 16.8, datums) == 3
    assert count_slab_hops(4.2, 8.4, datums) == 0
    assert count_slab_hops(4.2, 4.21, datums) == 0


def test_slab_bias_saturates_at_three_hops(raw_v3):
    tall = _five_storey(raw_v3)
    settings = Settings(noise_std=0, nlos_bias=0)
    _, labels, slab_hops = _observe(tall, settings, _state(4.0, 14.0, 1.2, "F0"))
    assert slab_hops["F3:A1"] == 3
    assert slab_hops["F4:A1"] == 4
    assert labels["F3:A1"].injected_bias == pytest.approx(MAX_SLABS * tall.slab_bias_per_slab)
    assert labels["F4:A1"].injected_bias == pytest.approx(labels["F3:A1"].injected_bias)


# -------------------------------------------------------------- determinism ---


def test_same_settings_and_frame_reproduce_identical_draws(building):
    settings = Settings(scenario="chaos", seed=77)
    state = _state(9.0, 3.0, 1.2, "ground", timestamp=13.0)
    observations, labels, slab_hops = generate_measurements_3d(building, settings, state, 5)
    again_observations, again_labels, again_hops = generate_measurements_3d(
        building, settings, state, 5
    )
    assert [m.model_dump() for m in observations] == [m.model_dump() for m in again_observations]
    assert [label.model_dump() for label in labels] == [
        label.model_dump() for label in again_labels
    ]
    assert slab_hops == again_hops
    other_frame, _, _ = generate_measurements_3d(building, settings, state, 6)
    assert [m.model_dump() for m in other_frame] != [m.model_dump() for m in observations]


def test_disabling_one_floor_scoped_anchor_leaves_every_other_stream_intact(building):
    settings = Settings(scenario="nlos", seed=9)
    state = _state(21.0, 14.0, 1.2, "ground", timestamp=3.0)
    base_observed, base_labels, base_hops = _observe(building, settings, state, 2)
    scoped = settings.model_copy(update={"disabled_anchor_keys": ["first:A5"]})
    observed, labels, slab_hops = _observe(building, scoped, state, 2)
    assert slab_hops == base_hops
    assert labels["first:A5"].kind == "disabled"
    assert labels["first:A5"].available is False
    assert labels["first:A5"].true_range == base_labels["first:A5"].true_range
    assert "first:A5" not in observed
    assert set(observed) == set(base_observed) - {"first:A5"}
    for key, measurement in observed.items():
        assert measurement.model_dump() == base_observed[key].model_dump()
    for key, label in labels.items():
        if key != "first:A5":
            assert label.model_dump() == base_labels[key].model_dump()
    for key in ("ground:A5", "second:A5"):
        assert labels[key].available
        assert key in observed


def test_the_legacy_disabled_list_still_disables_an_id_on_every_floor(building):
    settings = Settings(scenario="nlos", seed=9, disabled_anchors=["A5"])
    _, labels, _ = _observe(building, settings, _state(21.0, 14.0, 1.2, "ground"))
    assert {key for key, label in labels.items() if label.kind == "disabled"} == {
        "ground:A5",
        "first:A5",
        "second:A5",
    }


@pytest.mark.parametrize(
    "key, message",
    [
        ("first", "Malformed floor-scoped anchor key 'first'"),
        ("first:", "Malformed floor-scoped anchor key 'first:'"),
        (":A1", "Malformed floor-scoped anchor key ':A1'"),
        ("", "Malformed floor-scoped anchor key ''"),
        ("roof:A1", "Unknown floor-scoped anchor key 'roof:A1'"),
        ("first:A99", "Unknown floor-scoped anchor key 'first:A99'"),
    ],
)
def test_invalid_floor_scoped_keys_are_rejected_at_the_point_of_use(building, key, message):
    settings = Settings(disabled_anchor_keys=[key])
    with pytest.raises(ValueError, match=message):
        generate_measurements_3d(building, settings, _state(4.0, 14.0, 1.2, "ground"), 0)


def test_duplicate_floor_scoped_keys_are_rejected_at_the_point_of_use(building):
    settings = Settings(disabled_anchor_keys=["first:A1", "first:A1"])
    with pytest.raises(ValueError, match="Floor-scoped anchor keys must be unique"):
        generate_measurements_3d(building, settings, _state(4.0, 14.0, 1.2, "ground"), 0)


def test_validate_anchor_keys_accepts_installed_pairs_and_rejects_the_rest(building):
    installed = set(anchor_index(building))
    validate_anchor_keys(["ground:A1", "second:A8"], installed)
    with pytest.raises(ValueError, match="must be unique"):
        validate_anchor_keys(["ground:A1", "ground:A1"], installed)
    with pytest.raises(ValueError, match="Malformed floor-scoped anchor key 'A1'"):
        validate_anchor_keys(["A1"], installed)
    with pytest.raises(ValueError, match="Unknown floor-scoped anchor key 'roof:A1'"):
        validate_anchor_keys(["roof:A1"], installed)


# ------------------------------------------------------------------ outage ----


def test_the_timed_outage_hits_the_same_perimeter_anchors_on_every_floor(building):
    settings = Settings(scenario="anchor_failure", noise_std=0, nlos_bias=0, seed=3)
    state = _state(4.0, 14.0, 1.2, "ground", timestamp=15.0)
    _, labels, _ = _observe(building, settings, state)
    expected = _perimeter_outage(building)
    assert {key for key, label in labels.items() if label.kind == "outage"} == expected
    assert expected == {
        f"{floor_id}:{position}"
        for floor_id in ("ground", "first", "second")
        for position in ("A2", "A4", "A6", "A8")
    }
    # Inclusive lower edge, exclusive upper edge of the scenario-gated window.
    for timestamp, fires in ((12.0, True), (11.999, False), (21.999, True), (22.0, False)):
        _, edge, _ = _observe(
            building, settings, _state(4.0, 14.0, 1.2, "ground", timestamp=timestamp)
        )
        assert any(label.kind == "outage" for label in edge.values()) is fires
    for timestamp in (5.0, 22.0, 30.0):
        _, quiet, _ = _observe(
            building, settings, _state(4.0, 14.0, 1.2, "ground", timestamp=timestamp)
        )
        assert not any(label.kind == "outage" for label in quiet.values())
    _, ungated, _ = _observe(
        building,
        Settings(scenario="nlos", noise_std=0, nlos_bias=0),
        _state(4.0, 14.0, 1.2, "ground", timestamp=15.0),
    )
    assert not any(label.kind == "outage" for label in ungated.values())


def test_the_timed_outage_also_fires_while_traversing(building):
    route = next(route for route in building.transition_routes if route.id == "ascent_stairs")
    window = transition_windows(building, route)[0]
    timestamp = window.start + 1.0
    assert 12.0 <= timestamp % 40 < 22.0
    state = route_state(building, route, timestamp)
    assert (state.phase, state.floor_id) == ("transition", None)
    _, labels, _ = _observe(
        building, Settings(scenario="anchor_failure", noise_std=0, nlos_bias=0, seed=3), state
    )
    assert {key for key, label in labels.items() if label.kind == "outage"} == _perimeter_outage(
        building
    )


# ------------------------------------------------------------ anchor subset ---


def test_an_anchor_override_restricts_the_returned_streams(building):
    settings = Settings(noise_std=0, nlos_bias=0)
    chosen = [resolve_anchor(building, "first", "A1"), resolve_anchor(building, "ground", "A2")]
    observed, labels, slab_hops = _observe(
        building, settings, _state(1.0, 1.0, 5.4, "first"), anchors=chosen
    )
    assert list(slab_hops) == ["first:A1", "ground:A2"]
    assert list(labels) == ["first:A1", "ground:A2"]
    assert set(observed) == {"first:A1", "ground:A2"}
    with pytest.raises(ValueError, match="Duplicate anchor key 'first:A1'"):
        generate_measurements_3d(
            building, settings, _state(1.0, 1.0, 5.4, "first"), 0, anchors=[chosen[0], chosen[0]]
        )


def test_an_anchor_subset_reproduces_the_full_run_for_the_anchors_it_keeps(building):
    """Seeding uses the building-wide index, so filtering must not renumber the streams."""
    settings = Settings(scenario="clean", noise_std=1.5, dropout=0.0, nlos_bias=0, seed=5)
    state = _state(1.0, 1.0, 5.4, "first")
    full, _, _ = _observe(building, settings, state, frame_index=3)
    chosen = [
        resolve_anchor(building, "second", "A3"),
        resolve_anchor(building, "ground", "A1"),
        resolve_anchor(building, "first", "A6"),
    ]
    subset, _, _ = _observe(building, settings, state, frame_index=3, anchors=chosen)
    assert set(subset) == {anchor_key(anchor.floor_id, anchor.anchor_id) for anchor in chosen}
    # The draws are seeded per anchor, so a filtered run must agree exactly with the full one.
    for key, measurement in subset.items():
        assert measurement.observed_range == full[key].observed_range


def test_floor_scoped_keys_are_available_to_settings_and_controls():
    assert Settings().disabled_anchor_keys == []
    assert Settings(disabled_anchor_keys=["first:A1"]).disabled_anchor_keys == ["first:A1"]
    controls = Controls(expected_revision=0, disabled_anchor_keys=["first:A1"])
    assert controls.model_dump(exclude_none=True) == {
        "expected_revision": 0,
        "disabled_anchor_keys": ["first:A1"],
    }
    with pytest.raises(ValidationError):
        Settings(disabled_anchor_keys=[f"first:A{index}" for index in range(33)])


# ------------------------------------------------------ legacy golden path ----


def test_legacy_generator_output_is_unchanged():
    """Field-for-field pin of the untouched 2D generator, captured before this task."""
    environment = load_environments()["hospital_floor_01"]
    observations, _ = generate_measurements(
        environment, Settings(scenario="clean", seed=123), (4.0, 14.0), 0.0, 0
    )
    assert [(m.anchor_id, m.observed_range) for m in observations] == LEGACY_CLEAN_RANGES

    observations, labels = generate_measurements(
        environment, Settings(scenario="chaos", seed=123), (21.0, 14.0), 14.5, 7
    )
    assert [(m.anchor_id, m.observed_range) for m in observations] == LEGACY_CHAOS_RANGES
    assert [
        (label.anchor_id, label.kind, label.injected_bias, label.intersected_walls)
        for label in labels
    ] == LEGACY_CHAOS_LABELS

    observations, _ = generate_measurements(
        environment, Settings(scenario="anchor_failure", seed=123), (4.0, 14.0), 15.0, 2
    )
    assert [(m.anchor_id, m.observed_range) for m in observations] == LEGACY_FAILURE_RANGES
