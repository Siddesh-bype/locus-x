"""V3 contract, topology, floor lifecycle, truth boundary and V1 regression."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.locus_x.benchmark import run_benchmark
from backend.locus_x.building import Building, Floor
from backend.locus_x.environment import load_environments, resolve_floor
from backend.locus_x.geometry import intersects, segment_in_regions
from backend.locus_x.main import app, sessions
from backend.locus_x.schemas import BenchmarkRequest, Environment, Measurement, Settings
from backend.locus_x.session import SimulationSession

CONFIG = Path("configs/environments/hospital_v3.json")


@pytest.fixture
def raw_building():
    return json.loads(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture
def building(raw_building):
    return Building.model_validate(raw_building)


def test_catalog_preserves_legacy_geometry_and_hash(building):
    catalog = load_environments()
    legacy = catalog["hospital_floor_01"]
    assert type(legacy) is Environment
    assert hashlib.sha256(legacy.model_dump_json().encode()).hexdigest() == (
        "fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39"
    )
    assert [f.id for f in building.floors] == ["ground", "first", "second"]
    assert [f.elevation for f in building.floors] == [0, 4.2, 8.4]
    assert all(isinstance(f, Environment) for f in building.floors)
    assert resolve_floor(legacy) is legacy
    assert resolve_floor(building).id == "ground"
    assert resolve_floor(building, "second").elevation == 8.4


@pytest.mark.parametrize("index", [0, 1, 2])
def test_complete_routes_and_door_openings_are_walkable(building, index):
    floor = building.floors[index]
    regions = {r.id: r for r in [*floor.rooms, *floor.corridors]}
    for route in floor.routes.values():
        for i, waypoint in enumerate(route):
            end = route[(i + 1) % len(route)].position
            assert segment_in_regions(waypoint.position, end, regions.values())
            assert not any(intersects(waypoint.position, end, w.a, w.b) for w in floor.walls)
    for door in floor.doors:
        middle = tuple((a + b) / 2 for a, b in zip(door.a, door.b, strict=True))
        assert not any(intersects(middle, middle, w.a, w.b) for w in floor.walls)


@pytest.mark.parametrize(
    "case, message",
    [
        ("duplicate_floor", "Floor IDs"),
        ("elevation", "ascending elevations"),
        ("clearance", "clearance"),
        ("default", "default_floor_id"),
        ("room_bounds", "Region bounds"),
        ("room_overlap", "Regions overlap"),
        ("candidate_id", "Candidate anchor IDs"),
        ("candidate_bounds", "Candidate anchors"),
        ("wall_zero", "nonzero length"),
        ("door_solid", "overlaps a solid wall"),
        ("door_reference", "Door connections"),
        ("movement", "Movement regions"),
        ("route_wall", "crosses a solid wall"),
        ("route_region", "leaves valid movement regions"),
        ("core_alignment", "align vertically"),
        ("core_unlinked", "Every access core"),
        ("connection_floor", "existing floors"),
    ],
)
def test_invalid_building_is_rejected(raw_building, case, message):
    floor = raw_building["floors"][0]
    if case == "duplicate_floor":
        raw_building["floors"][1]["id"] = "ground"
    elif case == "elevation":
        raw_building["floors"][1]["elevation"] = 0
    elif case == "clearance":
        raw_building["floors"][1]["elevation"] = 3
    elif case == "default":
        raw_building["default_floor_id"] = "roof"
    elif case == "room_bounds":
        floor["rooms"][0]["center"] = [-1, 6]
    elif case == "room_overlap":
        floor["rooms"][1]["center"] = floor["rooms"][0]["center"]
        floor["rooms"][1]["size"] = floor["rooms"][0]["size"]
    elif case == "candidate_id":
        floor["candidate_anchors"][0]["id"] = "A1"
    elif case == "candidate_bounds":
        floor["candidate_anchors"][0]["position"] = [43, 1]
    elif case == "wall_zero":
        floor["walls"][0]["b"] = floor["walls"][0]["a"]
    elif case == "door_solid":
        door = floor["doors"][0]
        floor["walls"].append({"id": "blocked-door", "a": door["a"], "b": door["b"], "height": 3.2})
    elif case == "door_reference":
        floor["doors"][0]["connects"] = ["unknown", "reception"]
    elif case == "movement":
        floor["movement_regions"].append("lift-west")
    elif case == "route_wall":
        floor["routes"]["asset_round"][1]["position"] = [21, 25]
    elif case == "route_region":
        floor["movement_regions"].remove("reception")
    elif case == "core_alignment":
        core = raw_building["floors"][1]["access_cores"][0]
        core["center"][1] = 24.75
        core["size"][1] = 5.5
    elif case == "core_unlinked":
        raw_building["connections"].pop()
    elif case == "connection_floor":
        raw_building["connections"][0]["stops"][0]["floor_id"] = "roof"
    with pytest.raises(ValidationError, match=message):
        Building.model_validate(raw_building)


def test_no_nonfinite_coordinates_or_unknown_schema_fields(raw_building):
    raw_building["floors"][0]["elevation"] = float("nan")
    with pytest.raises(ValidationError):
        Building.model_validate(raw_building)
    with pytest.raises(ValidationError):
        Measurement(
            anchor_id="A1", anchor_position=(0, 0), timestamp=0, observed_range=1, truth=(1, 1)
        )


def test_region_union_rejects_gap_between_valid_endpoints(building):
    corridor = building.floors[0].corridors[0]
    a = corridor.model_copy(update={"center": (1, 1), "size": (2, 2)})
    b = corridor.model_copy(update={"center": (4, 1), "size": (2, 2)})
    assert not segment_in_regions((1, 1), (4, 1), [a, b])
    b = b.model_copy(update={"center": (3, 1)})
    assert segment_in_regions((1, 1), (3, 1), [a, b])


def test_candidates_do_not_change_observations_or_inference(building):
    first = SimulationSession(building, Settings()).frame
    data = building.model_dump()
    data["floors"][0]["candidate_anchors"] = []
    second = SimulationSession(Building.model_validate(data), Settings()).frame
    assert first.measurements == second.measurements
    for method in ("baseline", "robust", "tracking"):
        assert getattr(first, method).model_dump(exclude={"runtime_ms"}) == getattr(
            second, method
        ).model_dump(exclude={"runtime_ms"})


def test_floor_switch_is_atomic_and_resets_tracker_then_reset_restores_creation_floor():
    client = TestClient(app)
    original = client.post(
        "/api/v1/sessions",
        json={
            "environment_id": "hospital_v3",
            "floor_id": "first",
            "settings": {"scenario": "noise"},
        },
    ).json()
    path = f"/api/v1/sessions/{original['session_id']}"
    try:
        assert original["floor_id"] == "first" and original["floor_elevation"] == 4.2
        frame = client.post(f"{path}/advance", json={"expected_revision": 0, "dt": 1}).json()
        for payload, status in [
            ({"expected_revision": 0, "floor_id": "second"}, 409),
            ({"expected_revision": 1, "floor_id": "roof"}, 422),
        ]:
            assert client.post(f"{path}/floor", json=payload).status_code == status
            assert client.get(path).json() == frame
        selected = client.post(
            f"{path}/floor",
            json={
                "expected_revision": frame["revision"],
                "floor_id": "second",
            },
        ).json()
        assert selected["timestamp"] == selected["frame_index"] == 0
        assert selected["floor_id"] == "second" and selected["floor_elevation"] == 8.4
        assert selected["settings"] == frame["settings"]
        fresh = SimulationSession(
            load_environments()["hospital_v3"], Settings(scenario="noise"), "second"
        ).frame
        assert selected["measurements"] == fresh.model_dump(mode="json")["measurements"]
        assert selected["tracking"]["covariance"] == fresh.tracking.covariance
        restored = client.post(
            f"{path}/reset", json={"expected_revision": selected["revision"]}
        ).json()
        assert restored["floor_id"] == "first"
        assert restored["measurements"] == original["measurements"]
        assert restored["revision"] > selected["revision"]
    finally:
        client.delete(path)


def test_legacy_default_api_and_unknown_floor_requests():
    client = TestClient(app)
    legacy = client.post("/api/v1/sessions", json={}).json()
    assert legacy["environment_id"] == legacy["floor_id"] == "hospital_floor_01"
    assert legacy["floor_elevation"] == 0
    assert legacy["localization_mode"] == "2d_on_known_floor"
    client.delete(f"/api/v1/sessions/{legacy['session_id']}")
    for endpoint in ("sessions", "benchmarks"):
        assert (
            client.post(
                f"/api/v1/{endpoint}",
                json={
                    "environment_id": "hospital_v3",
                    "floor_id": "roof",
                },
            ).status_code
            == 422
        )
    catalog = client.get("/api/v1/environments").json()["environments"]
    assert len(next(e for e in catalog if e["id"] == "hospital_v3")["floors"]) == 3


@pytest.mark.parametrize("floor_id", ["ground", "first", "second"])
def test_v3_benchmark_uses_selected_floor_and_preserves_input_isolation(
    floor_id, building, monkeypatch
):
    import backend.locus_x.session as module

    inputs = []
    solve = module.solve

    def spy(observations, **kwargs):
        inputs.append(deepcopy(observations))
        return solve(observations, **kwargs)

    monkeypatch.setattr(module, "solve", spy)
    result = run_benchmark(
        BenchmarkRequest(
            environment_id=building.id,
            floor_id=floor_id,
            scenarios=["clean"],
            anchor_counts=[8],
            seeds=[777],
            steps=10,
        )
    )
    assert result["environment_id"] == building.id
    assert result["floor_id"] == floor_id
    assert result["result_version"] == "3.0"
    assert result["rows"][0]["rmse"] < 1e-6
    assert len(inputs) % 3 == 0
    assert all(inputs[i] == inputs[i + 1] == inputs[i + 2] for i in range(0, len(inputs), 3))
    assert all(
        set(m.model_dump()) == set(Measurement.model_fields) for stream in inputs for m in stream
    )
    assert len(next(f for f in building.floors if f.id == floor_id).rooms) >= 8


def test_all_floors_reuse_the_existing_environment_interface(building):
    assert all(
        isinstance(floor, Floor) and isinstance(floor, Environment) for floor in building.floors
    )
    assert all(
        len(SimulationSession(building, Settings(), f.id).frame.anchors) == 8
        for f in building.floors
    )
    # No API session leaked by this module's read-only helpers.
    assert len(sessions) < 64


def test_benchmark_cli_protects_existing_experiment_artifacts(tmp_path, monkeypatch):
    from backend.locus_x import benchmark

    saved = tmp_path / "results.json"
    saved.write_text('{"historical":true}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["benchmark", "--output", str(tmp_path)])
    with pytest.raises(SystemExit) as exit_info:
        benchmark.main()
    assert exit_info.value.code == 2
    assert saved.read_text(encoding="utf-8") == '{"historical":true}'
