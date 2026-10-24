"""Approximate VIT Pune buildings: schema validity, walkable routes and a multi-floor session."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.locus_x.building import Building
from backend.locus_x.geometry import intersects, segment_in_regions
from backend.locus_x.main import app, sessions

FILES = sorted(Path("configs/environments").glob("university_vit_pune*.json"))
client = TestClient(app)


def load(path):
    return Building.model_validate(json.loads(path.read_text(encoding="utf-8")))


def test_four_buildings_are_in_the_catalog():
    assert len(FILES) == 4
    ids = {e["id"] for e in client.get("/api/v1/environments").json()["environments"]}
    assert {load(f).id for f in FILES} <= ids


def test_main_block_floors():
    building = load(Path("configs/environments/university_vit_pune.json"))
    assert [f.id for f in building.floors] == ["ground", "first", "second", "third"]
    assert [f.elevation for f in building.floors] == [0, 4.2, 8.4, 12.6]


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_routes_are_walkable(path):
    for floor in load(path).floors:
        regions = [*floor.rooms, *floor.corridors]
        for route in floor.routes.values():
            for i, waypoint in enumerate(route):
                end = route[(i + 1) % len(route)].position
                assert segment_in_regions(waypoint.position, end, regions)
                assert not any(intersects(waypoint.position, end, w.a, w.b) for w in floor.walls)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_multifloor_session_runs(path):
    sessions.clear()
    response = client.post(
        "/api/v1/sessions",
        json={"environment_id": load(path).id, "multifloor": {"mode": "inferred", "route_id": "ascent_stairs"}},
    )
    assert response.status_code == 201, response.text
    sessions.clear()
