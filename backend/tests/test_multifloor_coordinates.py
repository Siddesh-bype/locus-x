"""Schema 3.1 optionals, one loader discriminator, and the 3D anchor projection."""

import hashlib
import importlib.util
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.locus_x.building import Building, config_model, is_building_config
from backend.locus_x.environment import load_environments
from backend.locus_x.multifloor import (
    Anchor3D,
    anchor_index,
    floor_anchors,
    resolve_anchor,
    target_z,
)
from backend.locus_x.optimization.layouts import validate_environment_config
from backend.locus_x.schemas import Environment

ROOT = Path(__file__).resolve().parents[2]
CONFIG_V1 = ROOT / "configs/environments/hospital_floor_01.json"
CONFIG_V3 = ROOT / "configs/environments/hospital_v3.json"
LAYOUTS = ROOT / "configs/environments/layouts"
SCRIPT = ROOT / "scripts/build_hospital_v3.py"

LEGACY_HASH = "fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39"

# sha256 of each legacy snapshot re-serialised through Environment. Adding any field
# to Environment or Anchor changes these hashes, so they are pinned as a regression.
LEGACY_SNAPSHOT_HASHES = {
    "hospital_floor_01-hospital_floor_01-grid-k3.json": (
        "54225ad6b3abd9f039376fcea8d42bab47d522249d37a967ec3f1f5048bf8eb5"
    ),
    "hospital_floor_01-hospital_floor_01-grid-k4.json": (
        "2f5f0c0d8c4b864473b2ea71e3318734515865cf968936dbd149e51a1b674978"
    ),
    "hospital_floor_01-hospital_floor_01-grid-k6.json": (
        "0b3bc8ad0451f499ab13f27d964715e920712506a5f39df6d81073c07486e3bb"
    ),
    "hospital_floor_01-hospital_floor_01-grid-k8.json": (
        "c0c4f3f3a76c8c64daf7c00c9fc33711bb42028a521f5f97128afc84f02b697c"
    ),
    "hospital_floor_01-hospital_floor_01-manual-k3.json": (
        "492cb60055d090a1c1e9636ade51dad672943bdb280025117a332cb483c4d3f3"
    ),
    "hospital_floor_01-hospital_floor_01-manual-k4.json": (
        "00471732ac0edfa3a8a9275edf26037592eceb0312b0a1642c7308870fe85baa"
    ),
    "hospital_floor_01-hospital_floor_01-manual-k6.json": (
        "6c3872b8a5281ef18a4bf9bdf5c9d9babd48da00e38bbe79b048fb30637b4f69"
    ),
    "hospital_floor_01-hospital_floor_01-manual-k8.json": (
        "f4cce60d201db540d47ff67903e0ed2c9de465c0ea639561c44f61361d04e736"
    ),
    "hospital_floor_01-hospital_floor_01-optimized-k3.json": (
        "c8d91280672a79a1851f6e2accc3c68aec8562e44e27b3176898d9d3af8cc6a7"
    ),
    "hospital_floor_01-hospital_floor_01-optimized-k4.json": (
        "61e139d51b8de19fbbace5eaa20f6dde7df85e94a381c35391b21b4e0c947dde"
    ),
    "hospital_floor_01-hospital_floor_01-optimized-k6.json": (
        "5ade74f772dac60d230a757826d4e17defffc470873f6484b2c7840c267023c0"
    ),
    "hospital_floor_01-hospital_floor_01-optimized-k8.json": (
        "f6f4355118cba0e1449dd694cc966e63d277c7340064ae247f3ff9dd4bdd7fed"
    ),
}


@pytest.fixture
def raw_v3():
    return json.loads(CONFIG_V3.read_text(encoding="utf-8"))


@pytest.fixture
def building(raw_v3):
    return Building.model_validate(raw_v3)


# --------------------------------------------------------------- 3D projection --


@pytest.mark.parametrize(
    "floor_id, expected_z",
    [("ground", 2.6), ("first", 6.8), ("second", 11.0)],
)
def test_known_anchor_positions_map_to_building_coordinates(building, floor_id, expected_z):
    anchor = resolve_anchor(building, floor_id, "A1")
    assert (anchor.anchor_id, anchor.floor_id) == ("A1", floor_id)
    assert (anchor.x, anchor.y) == (1.0, 1.0)
    assert anchor.z == pytest.approx(expected_z)


def test_room_centres_map_to_the_matching_device_elevation(building):
    for floor, device_z in zip(building.floors, [1.2, 5.4, 9.6], strict=True):
        room = floor.rooms[0]
        assert 0 <= room.center[0] <= floor.width
        assert 0 <= room.center[1] <= floor.height
        assert target_z(floor, building) == pytest.approx(device_z)
        assert target_z(floor, building, device_height=1.7) == pytest.approx(floor.elevation + 1.7)


def test_floor_anchors_returns_24_anchors_at_three_elevations(building):
    anchors = floor_anchors(building)
    assert len(anchors) == 24
    assert [anchor.floor_id for anchor in anchors] == [
        floor_id for floor_id in ["ground", "first", "second"] for _ in range(8)
    ]
    assert {round(anchor.z, 6) for anchor in anchors} == {2.6, 6.8, 11.0}
    index = anchor_index(building)
    assert set(index) == {
        f"{floor.id}:{anchor.id}" for floor in building.floors for anchor in floor.anchors
    }
    first = index["first:A1"]
    assert (first.floor_id, first.x, first.y) == ("first", 1.0, 1.0)
    assert first.z == pytest.approx(6.8)


def test_resolve_anchor_rejects_unknown_keys(building):
    with pytest.raises(ValueError, match="Unknown floor 'roof'"):
        resolve_anchor(building, "roof", "A1")
    with pytest.raises(ValueError, match="Unknown anchor 'A99' on floor 'ground'"):
        resolve_anchor(building, "ground", "A99")


def test_anchor3d_is_frozen(building):
    anchor = resolve_anchor(building, "ground", "A1")
    assert isinstance(anchor, Anchor3D)
    with pytest.raises(FrozenInstanceError):
        anchor.z = 0


def test_target_z_rejects_a_nonpositive_override(building):
    with pytest.raises(ValueError, match="Device height must be positive"):
        target_z(building.floors[0], building, device_height=0)


# -------------------------------------------------------------- 3.0 / 3.1 load --


def test_legacy_fixture_still_loads_as_environment_with_its_pinned_hash():
    legacy = load_environments()["hospital_floor_01"]
    assert type(legacy) is Environment
    assert hashlib.sha256(legacy.model_dump_json().encode()).hexdigest() == LEGACY_HASH


def test_every_layout_snapshot_still_loads_unchanged():
    paths = sorted(LAYOUTS.glob("*.json"))
    assert len(paths) == 15
    legacy, versioned = 0, 0
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        model = validate_environment_config(raw)
        if "schema_version" in raw:
            assert type(model) is Building
            assert model.schema_version == "3.0"
            assert len(model.floors) == 3
            versioned += 1
        else:
            assert type(model) is Environment
            assert set(model.model_dump()) == set(Environment.model_fields)
            digest = hashlib.sha256(model.model_dump_json().encode()).hexdigest()
            assert digest == LEGACY_SNAPSHOT_HASHES[path.name]
            legacy += 1
    assert (legacy, versioned) == (12, 3)


def test_schema_3_1_building_round_trips_through_both_loaders(raw_v3):
    assert raw_v3["schema_version"] == "3.1"
    assert is_building_config(raw_v3)
    assert not is_building_config({"id": "legacy"})
    rebuilt = validate_environment_config(raw_v3)
    assert type(rebuilt) is Building
    assert rebuilt.schema_version == "3.1"
    assert rebuilt.version == "3.1.0"
    assert rebuilt.target_device_height == 1.2
    assert rebuilt.slab_bias_per_slab == 1.5
    assert rebuilt.transition_routes == []
    assert rebuilt.model_dump() == Building.model_validate(raw_v3).model_dump()
    assert type(load_environments()["hospital_v3"]) is Building
    snapshot = json.loads((LAYOUTS / "hospital_v3-ground-manual-k8.json").read_text("utf-8"))
    assert type(validate_environment_config(snapshot)) is Building


def test_committed_fixture_is_exactly_the_authoring_script_output():
    spec = importlib.util.spec_from_file_location("build_hospital_v3", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert CONFIG_V3.read_text(encoding="utf-8") == json.dumps(module.build(), indent=2) + "\n"


@pytest.mark.parametrize("version", ["2.0", "4.0", "", None, 3.0, 3, ["3.0"], {"v": "3.1"}])
def test_non_building_schema_versions_resolve_to_environment(version):
    assert config_model({"id": "legacy", "schema_version": version}) is Environment


def test_missing_version_resolves_to_environment_and_30_to_building():
    assert config_model({"id": "legacy"}) is Environment
    assert config_model({"id": "hospital_v3", "schema_version": "3.0"}) is Building


# ------------------------------------------------------------- mount heights ---


def test_mount_height_override_is_used_by_the_projection(raw_v3):
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A1": 3.4}
    building = Building.model_validate(raw_v3)
    assert resolve_anchor(building, "ground", "A1").z == pytest.approx(3.4)
    assert resolve_anchor(building, "ground", "A2").z == pytest.approx(2.6)


def test_mount_height_at_the_slab_clearance_limit_is_accepted(raw_v3):
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A1": 4.0}
    building = Building.model_validate(raw_v3)
    assert resolve_anchor(building, "ground", "A1").z == pytest.approx(4.0)


def test_mount_height_above_the_slab_clearance_is_rejected(raw_v3):
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A1": 4.1}
    with pytest.raises(ValidationError, match="slab clearance"):
        Building.model_validate(raw_v3)


@pytest.mark.parametrize("height", [0, -0.5])
def test_mount_height_must_be_positive(raw_v3, height):
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A1": height}
    with pytest.raises(ValidationError, match="must be positive"):
        Building.model_validate(raw_v3)


def test_unknown_mount_height_anchor_is_rejected(raw_v3):
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A99": 2.6}
    with pytest.raises(ValidationError, match="unknown anchor 'A99'"):
        Building.model_validate(raw_v3)


def test_colocated_anchors_may_use_distinct_mount_heights(raw_v3):
    raw_v3["floors"][0]["anchors"][1]["position"] = [1, 1]
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A1": 2.6, "A2": 3.4}
    building = Building.model_validate(raw_v3)
    assert resolve_anchor(building, "ground", "A1").z == pytest.approx(2.6)
    assert resolve_anchor(building, "ground", "A2").z == pytest.approx(3.4)


def test_default_mount_height_is_checked_against_the_slab_bound(raw_v3):
    # Low walls and 2 m storeys make the 2.6 m default exceed the 1.8 m limit.
    for floor in raw_v3["floors"]:
        for wall in floor["walls"]:
            wall["height"] = 1.0
    for floor, elevation in zip(raw_v3["floors"], [0, 2.0, 4.0], strict=True):
        floor["elevation"] = elevation
    with pytest.raises(
        ValidationError, match="Mount height for anchor 'A1' on floor ground must not exceed 1.8"
    ):
        Building.model_validate(raw_v3)
    for floor in raw_v3["floors"]:
        floor["anchor_mount_heights"] = {f"A{i}": 1.5 for i in range(1, 9)}
    building = Building.model_validate(raw_v3)
    assert resolve_anchor(building, "ground", "A1").z == pytest.approx(1.5)


def test_mount_bound_uses_the_smallest_adjacent_gap(raw_v3):
    # Storeys 5.5 m and 3.5 m, so the middle floor's limit comes from the gap above
    # (3.5 - 0.2 = 3.3) and the topmost floor's from the gap below (also 3.3).
    for floor, elevation in zip(raw_v3["floors"], [0, 5.5, 9.0], strict=True):
        floor["elevation"] = elevation
    raw_v3["floors"][0]["anchor_mount_heights"] = {"A1": 5.3}
    ground = resolve_anchor(Building.model_validate(raw_v3), "ground", "A1")
    assert ground.z == pytest.approx(5.3)
    raw_v3["floors"][0]["anchor_mount_heights"] = {}
    raw_v3["floors"][1]["anchor_mount_heights"] = {"A1": 3.4}
    with pytest.raises(ValidationError, match="must not exceed 3.3 m"):
        Building.model_validate(raw_v3)
    raw_v3["floors"][1]["anchor_mount_heights"] = {"A1": 3.3}
    raw_v3["floors"][2]["anchor_mount_heights"] = {"A1": 3.4}
    with pytest.raises(ValidationError, match="must not exceed 3.3 m"):
        Building.model_validate(raw_v3)
    raw_v3["floors"][2]["anchor_mount_heights"] = {"A1": 3.3}
    top = resolve_anchor(Building.model_validate(raw_v3), "second", "A1")
    assert top.z == pytest.approx(12.3)


# --------------------------------------------------------- route structure -----


def _leg(floor_id):
    return {
        "floor_id": floor_id,
        "waypoints": [
            {"position": [4, 14], "dwell": 0},
            {"position": [2.5, 22], "dwell": 0},
        ],
    }


def _transition(
    identifier="t1",
    kind="stairs",
    connection_id="west-stairs",
    source="ground",
    target="first",
):
    return {
        "id": identifier,
        "kind": kind,
        "connection_id": connection_id,
        "from_floor_id": source,
        "to_floor_id": target,
        "travel_seconds": 20,
        "dwell_before": 0,
        "dwell_after": 0,
    }


def _route(legs, transitions, route_id="ascent", loop=False):
    return {
        "id": route_id,
        "name": "Ascent",
        "loop": loop,
        "legs": legs,
        "transitions": transitions,
    }


def test_one_way_route_structure_is_accepted(raw_v3):
    raw_v3["transition_routes"] = [
        _route([_leg("ground"), _leg("first")], [_transition()]),
    ]
    building = Building.model_validate(raw_v3)
    assert [leg.floor_id for leg in building.transition_routes[0].legs] == ["ground", "first"]
    assert building.transition_routes[0].transitions[0].connection_id == "west-stairs"


def test_loop_route_needs_one_transition_per_leg(raw_v3):
    raw_v3["transition_routes"] = [
        _route(
            [_leg("ground"), _leg("first")],
            [_transition(), _transition("t2", target="ground", source="first")],
            loop=True,
        )
    ]
    assert len(Building.model_validate(raw_v3).transition_routes[0].transitions) == 2


@pytest.mark.parametrize(
    "route, message",
    [
        (_route([_leg("ground"), _leg("first")], []), "needs exactly 1 transitions"),
        (
            _route([_leg("ground"), _leg("first"), _leg("second")], [_transition()]),
            "needs exactly 2 transitions",
        ),
        (
            _route([_leg("ground"), _leg("roof")], [_transition()]),
            "references unknown floor 'roof'",
        ),
        (
            _route(
                [_leg("ground"), _leg("first")],
                [_transition(connection_id="roof-lift")],
            ),
            "unknown connection 'roof-lift'",
        ),
        (
            _route(
                [_leg("ground"), _leg("first")],
                [_transition(kind="elevator")],
            ),
            "does not match connection 'west-stairs'",
        ),
        (
            _route(
                [_leg("ground"), _leg("first")],
                [_transition(), _transition()],
                loop=True,
            ),
            "unique within route",
        ),
    ],
    ids=["count", "count3", "unknown_floor", "unknown_connection", "kind", "duplicate_in_route"],
)
def test_invalid_route_structure_is_rejected(raw_v3, route, message):
    raw_v3["transition_routes"] = [route]
    with pytest.raises(ValidationError, match=message):
        Building.model_validate(raw_v3)


def test_route_and_transition_ids_are_unique_across_the_building(raw_v3):
    raw_v3["transition_routes"] = [
        _route([_leg("ground"), _leg("first")], [_transition()]),
        _route([_leg("ground"), _leg("first")], [_transition("t2")]),
    ]
    with pytest.raises(ValidationError, match="Route IDs must be unique"):
        Building.model_validate(raw_v3)
    raw_v3["transition_routes"] = [
        _route([_leg("ground"), _leg("first")], [_transition()]),
        _route([_leg("ground"), _leg("first")], [_transition()], route_id="ascent-lift"),
    ]
    with pytest.raises(ValidationError, match="Transition IDs must be unique across the building"):
        Building.model_validate(raw_v3)
