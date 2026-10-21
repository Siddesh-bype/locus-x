"""Author the fictional V3 hospital JSON. Runtime consumers load only the generated JSON.

Run from the repository root: python scripts/build_hospital_v3.py
This intentionally writes only configs/environments/hospital_v3.json.
"""

import json
from pathlib import Path

WIDTH, LENGTH, WALL_HEIGHT = 42, 28, 3.2


def room(identifier, name, kind, x0, x1, y0, y1):
    return {
        "id": identifier,
        "name": name,
        "kind": kind,
        "center": [(x0 + x1) / 2, (y0 + y1) / 2],
        "size": [x1 - x0, y1 - y0],
    }


DEPARTMENTS = [
    {
        "id": "ground",
        "name": "Ground floor",
        "short_name": "G",
        "elevation": 0,
        "color": "#318d87",
        "description": "Arrival, emergency and outpatient services",
        "south": [
            ("triage", "Triage", "triage", 0, 5),
            ("emergency", "Emergency", "emergency", 5, 12),
            ("reception", "Reception & waiting", "reception", 12, 23),
            ("pharmacy", "Pharmacy", "pharmacy", 23, 32),
            ("consult-01", "Consultation 01", "consultation", 32, 37),
            ("consult-02", "Consultation 02", "consultation", 37, 42),
        ],
        "north": [
            ("imaging", "Imaging", "imaging", 8, 18),
            ("laboratory", "Laboratory", "laboratory", 18, 27),
            ("consult-03", "Consultation 03", "consultation", 27, 35),
            ("support", "Clinical support", "support", 35, 42),
        ],
        "visits": ["reception", "emergency", "imaging", "pharmacy"],
        "evaluation_visits": ["consult-02", "laboratory", "triage", "consult-03"],
    },
    {
        "id": "first",
        "name": "First floor",
        "short_name": "01",
        "elevation": 4.2,
        "color": "#4e7eab",
        "description": "Inpatient care, nursing and treatment",
        "south": [
            ("ward-a", "Ward A", "ward", 0, 10),
            ("patient-101", "Patient room 101", "patient", 10, 16),
            ("patient-102", "Patient room 102", "patient", 16, 22),
            ("treatment", "Treatment", "treatment", 22, 31),
            ("isolation", "Isolation room", "isolation", 31, 37),
            ("linen", "Linen & support", "support", 37, 42),
        ],
        "north": [
            ("ward-b", "Ward B", "ward", 8, 18),
            ("nursing", "Nursing station", "station", 18, 27),
            ("patient-103", "Patient room 103", "patient", 27, 35),
            ("patient-104", "Patient room 104", "patient", 35, 42),
        ],
        "visits": ["nursing", "ward-a", "patient-103", "treatment"],
        "evaluation_visits": ["patient-102", "ward-b", "isolation", "patient-104"],
    },
    {
        "id": "second",
        "name": "Second floor",
        "short_name": "02",
        "elevation": 8.4,
        "color": "#a4784f",
        "description": "Critical care, surgery and recovery",
        "south": [
            ("icu", "Intensive care unit", "icu", 0, 14),
            ("theatre-01", "Operating theatre 01", "theatre", 14, 23),
            ("theatre-02", "Operating theatre 02", "theatre", 23, 32),
            ("clean-utility", "Clean utility", "laboratory", 32, 37),
            ("equipment", "Equipment store", "storage", 37, 42),
        ],
        "north": [
            ("recovery", "Recovery", "recovery", 8, 22),
            ("staff", "Staff & on-call", "staff", 22, 32),
            ("preparation", "Surgical preparation", "treatment", 32, 42),
        ],
        "visits": ["icu", "recovery", "theatre-01", "equipment"],
        "evaluation_visits": ["preparation", "theatre-02", "clean-utility", "staff"],
    },
]


def make_floor(spec):
    walls, doors = [], []

    def wall(identifier, a, b):
        if a != b:
            walls.append({"id": identifier, "a": a, "b": b, "height": WALL_HEIGHT})

    def opening_wall(identifier, y, x0, x1, door_x, door_width, connects):
        a, b = [door_x - door_width / 2, y], [door_x + door_width / 2, y]
        wall(f"{identifier}-w", [x0, y], a)
        wall(f"{identifier}-e", b, [x1, y])
        doors.append(
            {"id": f"door-{identifier}", "a": a, "b": b, "height": 2.3, "connects": connects}
        )

    if spec["id"] == "ground":
        opening_wall("ext-south", 0, 0, WIDTH, 17.5, 3.2, ["outside", "reception"])
    else:
        wall("ext-south", [0, 0], [WIDTH, 0])
    wall("ext-east", [WIDTH, 0], [WIDTH, LENGTH])
    wall("ext-north", [WIDTH, LENGTH], [0, LENGTH])
    wall("ext-west", [0, LENGTH], [0, 0])
    rooms = []
    for side, y0, y1, frontage in [("south", 0, 12, 12), ("north", 16, 28, 16)]:
        for i, (identifier, name, kind, x0, x1) in enumerate(spec[side]):
            rooms.append(room(identifier, name, kind, x0, x1, y0, y1))
            door_width = 2.2 if kind in ("emergency", "icu", "imaging", "recovery") else 1.6
            opening_wall(
                identifier,
                frontage,
                x0,
                x1,
                (x0 + x1) / 2,
                door_width,
                [identifier, "main-corridor"],
            )
            if i:
                wall(f"partition-{identifier}", [x0, y0], [x0, y1])
    wall("core-east", [8, 16], [8, 28])
    wall("core-divider", [5, 22], [5, 28])
    opening_wall("stair-entry", 22, 0, 5, 2.5, 1.6, ["core-lobby", "stair-west"])
    opening_wall("lift-entry", 22, 5, 8, 6.5, 1.8, ["core-lobby", "lift-west"])
    corridors = [
        {"id": "main-corridor", "name": "Main corridor", "center": [21, 14], "size": [42, 4]},
        {"id": "core-lobby", "name": "Lift & stair lobby", "center": [4, 19], "size": [8, 6]},
    ]
    cores = [
        {
            "id": "stair-west",
            "name": "West staircase",
            "kind": "stairs",
            "center": [2.5, 25],
            "size": [5, 6],
            "entry": [2.5, 22],
        },
        {
            "id": "lift-west",
            "name": "Bed lift",
            "kind": "elevator",
            "center": [6.5, 25],
            "size": [3, 6],
            "entry": [6.5, 22],
        },
    ]
    by_id = {r["id"]: r for r in rooms}

    def route(visits):
        points = [{"position": [4, 14], "dwell": 1}]
        for identifier in visits:
            x, y = by_id[identifier]["center"]
            points.extend(
                [
                    {"position": [x, 14], "dwell": 0},
                    {"position": [x, y], "dwell": 2},
                    {"position": [x, 14], "dwell": 0},
                ]
            )
        points.extend(
            [
                {"position": [4, 14], "dwell": 0},
                {"position": [4, 19], "dwell": 2},
                {"position": [4, 14], "dwell": 0},
            ]
        )
        return points

    installed = [[1, 1], [21, 1], [41, 1], [41, 14], [41, 27], [21, 27], [1, 27], [1, 14]]
    candidates = [[x, y] for y in [1, 27] for x in [1, 8, 15, 22, 29, 36, 41]]
    candidates += [[x, 14] for x in [2, 10, 18, 26, 34, 40]] + [[4, 20]]
    return {
        **{
            key: spec[key]
            for key in ["id", "name", "description", "short_name", "elevation", "color"]
        },
        "width": WIDTH,
        "height": LENGTH,
        "anchors": [{"id": f"A{i + 1}", "position": p} for i, p in enumerate(installed)],
        "walls": walls,
        "rooms": rooms,
        "routes": {
            "asset_round": route(spec["visits"]),
            "held_out": route(spec["evaluation_visits"]),
        },
        "route_names": {
            "asset_round": "Clinical service round",
            "held_out": "Evaluation service round",
        },
        "corridors": corridors,
        "doors": doors,
        "access_cores": cores,
        "movement_regions": [r["id"] for r in [*rooms, *corridors]],
        "candidate_anchors": [
            {"id": f"C{i + 1:02d}", "position": p} for i, p in enumerate(candidates)
        ],
        "anchor_layout_id": "perimeter-eight-v3.0.0",
    }


def build():
    return {
        "schema_version": "3.0",
        "version": "3.0.0",
        "id": "hospital_v3",
        "name": "Meridian Hospital",
        "description": "Fictional three-floor hospital for synthetic localization experiments.",
        "width": WIDTH,
        "height": LENGTH,
        "default_floor_id": "ground",
        "floors": [make_floor(spec) for spec in DEPARTMENTS],
        "connections": [
            {
                "id": identifier,
                "kind": kind,
                "stops": [{"floor_id": spec["id"], "access_id": access} for spec in DEPARTMENTS],
            }
            for identifier, kind, access in [
                ("west-stairs", "stairs", "stair-west"),
                ("bed-elevator", "elevator", "lift-west"),
            ]
        ],
        "validation": {
            "units": "metres",
            "source": "fictional-procedural",
            "geometry_revision": "meridian-2026-10-19.1",
            "notes": "Shared local origin; 4.2 m storeys; physical wall height 3.2 m. "
            "Rooms/corridors define walkable regions. Access cores align vertically. "
            "Routes remain on one known floor. No clinical building-code or real-hospital claim.",
        },
    }


if __name__ == "__main__":
    destination = Path(__file__).resolve().parents[1] / "configs/environments/hospital_v3.json"
    destination.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {destination}")
