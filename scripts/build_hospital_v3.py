"""Author the fictional V3 hospital JSON. Runtime consumers load only the generated JSON.

Run from the repository root: python scripts/build_hospital_v3.py
This intentionally writes only configs/environments/hospital_v3.json.
"""

import json
from pathlib import Path

WIDTH, LENGTH, WALL_HEIGHT = 42, 28, 3.2

# Route timings. Both core entries are the door midpoints on the y = 22 lobby edge and
# are identical on all three floors, so a vertical traverse changes z only. Every leg
# duration quoted below assumes the simulator's 1.2 m/s route speed.
STAIR_ENTRY = [2.5, 22]
LIFT_ENTRY = [6.5, 22]
TRAVEL_SECONDS = 20
LIFT_TRAVEL_SECONDS = 15


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
        # Empty means every anchor uses the default 2.6 m mount height.
        "anchor_mount_heights": {},
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


def waypoint(x, y, dwell=0):
    return {"position": [x, y], "dwell": dwell}


def leg(floor_id, waypoints):
    return {"floor_id": floor_id, "waypoints": waypoints}


def reversed_leg(source):
    """Reverse a leg; every waypoint keeps its own dwell, so the dwell list reverses too."""
    return leg(source["floor_id"], list(reversed(source["waypoints"])))


def transition(identifier, kind, source, target, travel, dwell_before=0, dwell_after=0):
    # The fixture has exactly one connection per kind, so the kind selects it.
    return {
        "id": identifier,
        "kind": kind,
        "connection_id": "west-stairs" if kind == "stairs" else "bed-elevator",
        "from_floor_id": source,
        "to_floor_id": target,
        "travel_seconds": travel,
        "dwell_before": dwell_before,
        "dwell_after": dwell_after,
    }


def ground_leg(entry):
    """Short corridor excursion and back, then across the lobby to one core entry."""
    return leg(
        "ground",
        [
            waypoint(4, 14, 1),
            waypoint(8.5, 14),
            waypoint(4, 14),
            waypoint(4, 19),
            waypoint(*entry),
        ],
    )


def ground_loop_leg(entry):
    """Closed ground leg for loop routes: starts and ends on the same core entry."""
    return leg(
        "ground",
        [
            waypoint(*entry),
            waypoint(4, 19),
            waypoint(4, 14, 1),
            waypoint(8.5, 14),
            waypoint(4, 14),
            waypoint(4, 19),
            waypoint(*entry),
        ],
    )


def first_leg(entry):
    return leg(
        "first",
        [
            waypoint(*entry),
            waypoint(4, 19),
            waypoint(5, 14),
            waypoint(4, 14, 1),
            waypoint(4, 19),
            waypoint(*entry),
        ],
    )


def second_leg(entry):
    return leg(
        "second",
        [
            waypoint(*entry),
            waypoint(4, 19),
            waypoint(7, 14),
            waypoint(15, 14),
            waypoint(15, 22, 2),
            waypoint(15, 14),
            waypoint(4, 14),
            waypoint(4, 19),
            waypoint(*entry),
        ],
    )


def transition_routes():
    """Five authoring routes over the two vertical connections.

    Timings at 1.2 m/s. Leg durations (sum of dwells plus straight-line segments):

        ground G     15.4618 s   ground GL    15.9209 s   ground GL-loop  22.4235 s
        first F1S    15.8394 s   first F1SL   16.7577 s
        second S2    45.7826 s   second S2L   46.7010 s

    Ascent/descent windows in *height* order, not modulo 40 (each traverse is 20 s
    except the lift's 15 s):

        ascent_stairs     [15.46, 35.46)  [51.30, 71.30)     period 117.08
        ascent_lift       [17.92, 32.92)  [53.68, 68.68)     period 117.38
        descent_stairs    [52.00, 72.00)  [87.84, 107.84)    period 123.30
        transition_pause  [15.46, 35.46)                     period  57.30
        cross_floor       [15.84, 35.84) [81.62, 101.62) [117.46, 137.46) [159.88, 179.88)
                                                                          period 179.88

    The simulator's timed outage is 12 <= timestamp % 40 < 22. Every traverse above
    overlaps it once per period (interval arithmetic, `w % 40` maps a window onto the
    circle of length 40; a window that crosses a multiple of 40 is split):

        ascent_stairs     15.46 % 40 = 15.46 -> [15.46, 22) is inside the outage
                          51.30 % 40 = 11.30 -> [11.30, 31.30) covers [12, 22)
        ascent_lift       17.92 % 40 = 17.92 -> [17.92, 22) is inside the outage
                          53.68 % 40 = 13.68 -> [13.68, 28.68) covers [13.68, 22)
        descent_stairs    52.00 % 40 = 12.00 -> [12.00, 22) is inside the outage
                          87.84 % 40 =  7.84 -> [7.84, 27.84) covers [12, 22)
        transition_pause  15.46 % 40 = 15.46 -> [15.46, 22) is inside the outage
        cross_floor       15.84 % 40 = 15.84 -> [15.84, 22) is inside the outage
                          81.62 % 40 =  1.62 -> [1.62, 21.62) covers [12, 21.62)
                         117.46 % 40 = 37.46 -> wraps, [37.46, 40) + [0, 17.46) covers [12, 17.46)
                         159.88 % 40 = 39.88 -> wraps, [39.88, 40) + [0, 19.88) covers [12, 19.88)

    The 6.22 s `dwell_before` on the first descent traverse delays it from 45.78 s to
    52.00 s, which is what moves it from 5.78 % 40 (outside) into the outage window.
    `descent_stairs` is `ascent_stairs` reversed; reversing keeps each waypoint's dwell.

    `cross_floor_survey` is looped with one transition per leg, as the schema requires
    (a loop needs exactly len(legs) transitions). The requested ground -> first ->
    second -> first -> ground cycle is therefore four closed legs, read cyclically
    from the last leg: ground -> first -> second -> first -> ground. Every leg starts
    and ends on a core entry, so the wrap from the ground leg into the first-floor leg
    is the fourth traverse, not a horizontal jump.
    """
    ground_stairs = ground_leg(STAIR_ENTRY)
    ground_lift = ground_leg(LIFT_ENTRY)
    first_stairs = first_leg(STAIR_ENTRY)
    first_lift = first_leg(LIFT_ENTRY)
    second_stairs = second_leg(STAIR_ENTRY)
    second_lift = second_leg(LIFT_ENTRY)
    return [
        {
            "id": "ascent_stairs",
            "name": "Stair ascent to the second floor",
            "loop": False,
            "legs": [ground_stairs, first_stairs, second_stairs],
            "transitions": [
                transition("ascent-stairs-1", "stairs", "ground", "first", TRAVEL_SECONDS),
                transition("ascent-stairs-2", "stairs", "first", "second", TRAVEL_SECONDS),
            ],
        },
        {
            "id": "ascent_lift",
            "name": "Lift ascent to the second floor",
            "loop": False,
            "legs": [ground_lift, first_lift, second_lift],
            "transitions": [
                transition(
                    "ascent-lift-1",
                    "elevator",
                    "ground",
                    "first",
                    LIFT_TRAVEL_SECONDS,
                    dwell_before=2,
                    dwell_after=2,
                ),
                transition(
                    "ascent-lift-2",
                    "elevator",
                    "first",
                    "second",
                    LIFT_TRAVEL_SECONDS,
                    dwell_before=2,
                    dwell_after=2,
                ),
            ],
        },
        {
            "id": "descent_stairs",
            "name": "Stair descent to the ground floor",
            "loop": False,
            "legs": [
                reversed_leg(second_stairs),
                reversed_leg(first_stairs),
                reversed_leg(ground_stairs),
            ],
            "transitions": [
                transition(
                    "descent-stairs-1",
                    "stairs",
                    "second",
                    "first",
                    TRAVEL_SECONDS,
                    dwell_before=6.22,
                ),
                transition("descent-stairs-2", "stairs", "first", "ground", TRAVEL_SECONDS),
            ],
        },
        {
            "id": "transition_pause",
            "name": "Stair ascent with a pause on the first floor",
            "loop": False,
            "legs": [ground_stairs, first_stairs],
            "transitions": [
                transition(
                    "transition-pause-1",
                    "stairs",
                    "ground",
                    "first",
                    TRAVEL_SECONDS,
                    dwell_after=6,
                )
            ],
        },
        {
            "id": "cross_floor_survey",
            "name": "Looping survey of all three floors",
            "loop": True,
            "legs": [first_stairs, second_stairs, first_stairs, ground_loop_leg(STAIR_ENTRY)],
            "transitions": [
                transition("cross-floor-1", "stairs", "first", "second", TRAVEL_SECONDS),
                transition("cross-floor-2", "stairs", "second", "first", TRAVEL_SECONDS),
                transition("cross-floor-3", "stairs", "first", "ground", TRAVEL_SECONDS),
                transition("cross-floor-4", "stairs", "ground", "first", TRAVEL_SECONDS),
            ],
        },
    ]


def build():
    return {
        "schema_version": "3.1",
        "version": "3.1.0",
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
        # Metres above the selected floor datum; used by the 3D projection.
        "target_device_height": 1.2,
        # Synthetic model assumption: metres of added range per intervening floor slab.
        "slab_bias_per_slab": 1.5,
        # Route models are declared in the schema; cost/outage arithmetic is in transition_routes().
        "transition_routes": transition_routes(),
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
