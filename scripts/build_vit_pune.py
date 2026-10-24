"""Author the approximate VIT Pune main-academic-block JSON (fictional-procedural).

Run from the repository root: python scripts/build_vit_pune.py
Writes configs/environments/university_vit_pune*.json, one environment per campus building.
Dimensions and room layout are estimates, not a survey: public sources give no per-building
plans. Footprint areas follow the OpenStreetMap outlines in scripts/build_vit_campus.py.
"""

import json
from pathlib import Path

from build_hospital_v3 import leg, reversed_leg, room, waypoint

WALL_HEIGHT = 3.2
TRAVEL_SECONDS, LIFT_TRAVEL_SECONDS = 20, 15

# (id, name, kind, x0, x1); south rooms run y 0-14, north rooms y 18-36 (x from 8).
FLOORS = [
    {
        "id": "ground", "name": "Ground floor", "short_name": "G", "elevation": 0,
        "color": "#0fb5a0", "description": "Entrance, administration, seminar hall and canteen",
        "south": [
            ("admin", "Administration office", "office", 0, 20),
            ("entrance", "Main entrance & lobby", "reception", 20, 40),
            ("classroom-g01", "Classroom G01", "classroom", 40, 50),
            ("classroom-g02", "Classroom G02", "classroom", 50, 60),
        ],
        "north": [
            ("principal", "Director's office", "office", 8, 20),
            ("staff-g", "Staff room", "staff", 20, 30),
            ("canteen", "Canteen", "canteen", 30, 45),
            ("seminar", "Seminar hall", "seminar", 45, 60),
        ],
        "visits": ["entrance", "admin", "canteen", "seminar"],
        "evaluation_visits": ["classroom-g02", "principal", "staff-g", "classroom-g01"],
    },
    {
        "id": "first", "name": "First floor", "short_name": "01", "elevation": 4.2,
        "color": "#3b82f6", "description": "Lecture classrooms",
        "south": [(f"classroom-1{i:02d}", f"Classroom 1{i:02d}", "classroom", i * 10 - 10, i * 10)
                  for i in range(1, 7)],
        "north": [
            ("classroom-107", "Classroom 107", "classroom", 8, 22),
            ("classroom-108", "Classroom 108", "classroom", 22, 36),
            ("classroom-109", "Classroom 109", "classroom", 36, 48),
            ("tutorial-1", "Tutorial room", "classroom", 48, 60),
        ],
        "visits": ["classroom-101", "classroom-104", "classroom-108", "tutorial-1"],
        "evaluation_visits": ["classroom-106", "classroom-102", "classroom-107", "classroom-109"],
    },
    {
        "id": "second", "name": "Second floor", "short_name": "02", "elevation": 8.4,
        "color": "#f59e0b", "description": "Computer and electronics laboratories",
        "south": [
            ("lab-201", "Computer lab 201", "lab", 0, 15),
            ("lab-202", "Computer lab 202", "lab", 15, 30),
            ("lab-203", "Electronics lab 203", "lab", 30, 45),
            ("lab-204", "Project lab 204", "lab", 45, 60),
        ],
        "north": [
            ("lab-205", "Networks lab 205", "lab", 8, 24),
            ("lab-206", "Embedded lab 206", "lab", 24, 40),
            ("server", "Server room", "support", 40, 48),
            ("staff-2", "Faculty room", "staff", 48, 60),
        ],
        "visits": ["lab-201", "lab-203", "lab-206", "staff-2"],
        "evaluation_visits": ["lab-204", "lab-202", "lab-205", "server"],
    },
    {
        "id": "third", "name": "Third floor", "short_name": "03", "elevation": 12.6,
        "color": "#a855f7", "description": "Library, reading hall and faculty offices",
        "south": [
            ("library", "Lohiya library", "library", 0, 30),
            ("reading", "Reading hall", "library", 30, 50),
            ("e-library", "E-library", "lab", 50, 60),
        ],
        "north": [
            ("hod", "Head of department", "office", 8, 22),
            ("faculty-3", "Faculty cabins", "staff", 22, 40),
            ("conference", "Conference room", "seminar", 40, 60),
        ],
        "visits": ["library", "reading", "conference", "hod"],
        "evaluation_visits": ["e-library", "faculty-3", "reading", "conference"],
    },
]


class Geo:
    """Double-loaded corridor along x; stair + lift cores in the north-west corner."""

    def __init__(self, width, length, south_depth):
        self.w, self.l, self.ds = width, length, south_depth
        self.cy = south_depth + 2  # corridor centre line
        self.front_n = south_depth + 4  # north rooms start here
        self.lobby_y = south_depth + 7
        self.core_y0 = south_depth + 10
        self.stair = [2.5, self.core_y0]
        self.lift = [6.5, self.core_y0]


def make_floor(spec, g):
    walls, doors = [], []

    def wall(identifier, a, b):
        if a != b:
            walls.append({"id": identifier, "a": a, "b": b, "height": WALL_HEIGHT})

    def opening_wall(identifier, y, x0, x1, door_x, door_width, connects):
        a, b = [door_x - door_width / 2, y], [door_x + door_width / 2, y]
        wall(f"{identifier}-w", [x0, y], a)
        wall(f"{identifier}-e", b, [x1, y])
        doors.append({"id": f"door-{identifier}", "a": a, "b": b, "height": 2.3, "connects": connects})

    if spec["id"] == "ground":
        door = next((x0 + x1) / 2 for i, _, _, x0, x1 in spec["south"] if i == "entrance")
        opening_wall("ext-south", 0, 0, g.w, door, 3.2, ["outside", "entrance"])
    else:
        wall("ext-south", [0, 0], [g.w, 0])
    wall("ext-east", [g.w, 0], [g.w, g.l])
    wall("ext-north", [g.w, g.l], [0, g.l])
    wall("ext-west", [0, g.l], [0, 0])
    rooms = []
    for side, y0, y1, frontage in [("south", 0, g.ds, g.ds), ("north", g.front_n, g.l, g.front_n)]:
        for i, (identifier, name, kind, x0, x1) in enumerate(spec[side]):
            rooms.append(room(identifier, name, kind, x0, x1, y0, y1))
            opening_wall(identifier, frontage, x0, x1, (x0 + x1) / 2, 1.6, [identifier, "main-corridor"])
            if i:
                wall(f"partition-{identifier}", [x0, y0], [x0, y1])
    wall("core-east", [8, g.front_n], [8, g.l])
    wall("core-divider", [5, g.core_y0], [5, g.l])
    opening_wall("stair-entry", g.core_y0, 0, 5, 2.5, 1.6, ["core-lobby", "stair-west"])
    opening_wall("lift-entry", g.core_y0, 5, 8, 6.5, 1.8, ["core-lobby", "lift-west"])
    corridors = [
        {"id": "main-corridor", "name": "Main corridor", "center": [g.w / 2, g.cy], "size": [g.w, 4]},
        {"id": "core-lobby", "name": "Lift & stair lobby", "center": [4, g.lobby_y], "size": [8, 6]},
    ]
    depth, mid = g.l - g.core_y0, (g.core_y0 + g.l) / 2
    cores = [
        {"id": "stair-west", "name": "West staircase", "kind": "stairs",
         "center": [2.5, mid], "size": [5, depth], "entry": g.stair},
        {"id": "lift-west", "name": "Lift", "kind": "elevator",
         "center": [6.5, mid], "size": [3, depth], "entry": g.lift},
    ]
    by_id = {r["id"]: r for r in rooms}

    def route(visits):
        points = [{"position": [4, g.cy], "dwell": 1}]
        for identifier in visits:
            x, y = by_id[identifier]["center"]
            points += [waypoint(x, g.cy), waypoint(x, y, 2), waypoint(x, g.cy)]
        points += [waypoint(4, g.cy), waypoint(4, g.lobby_y, 2), waypoint(4, g.cy)]
        return points

    installed = [[1, 1], [g.w / 2, 1], [g.w - 1, 1], [g.w - 1, g.cy], [g.w - 1, g.l - 1],
                 [g.w / 2, g.l - 1], [1, g.l - 1], [1, g.cy]]
    xs = [1 + i * (g.w - 2) / 6 for i in range(7)]
    candidates = [[round(x, 1), y] for y in [1, g.l - 1] for x in xs]
    candidates += [[round(x + 1, 1), g.cy] for x in xs[:-1]] + [[4, g.lobby_y]]
    return {
        **{k: spec[k] for k in ["id", "name", "description", "short_name", "elevation", "color"]},
        "width": g.w,
        "height": g.l,
        "anchors": [{"id": f"A{i + 1}", "position": p} for i, p in enumerate(installed)],
        "anchor_mount_heights": {},
        "walls": walls,
        "rooms": rooms,
        "routes": {"asset_round": route(spec["visits"]), "held_out": route(spec["evaluation_visits"])},
        "route_names": {"asset_round": "Campus service round", "held_out": "Evaluation service round"},
        "corridors": corridors,
        "doors": doors,
        "access_cores": cores,
        "movement_regions": [r["id"] for r in [*rooms, *corridors]],
        "candidate_anchors": [{"id": f"C{i + 1:02d}", "position": p} for i, p in enumerate(candidates)],
        "anchor_layout_id": "perimeter-eight-v3.0.0",
    }


def corridor_leg(floor_id, entry, g, first=False):
    """Corridor excursion that ends on the core entry; non-first legs also start on it."""
    points = [waypoint(4, g.cy, 1), waypoint(14, g.cy), waypoint(4, g.cy), waypoint(4, g.lobby_y), waypoint(*entry)]
    if not first:
        points = [waypoint(*entry), waypoint(4, g.lobby_y)] + points
    return leg(floor_id, points)


def transition(identifier, kind, source, target, travel):
    return {
        "id": identifier,
        "kind": kind,
        "connection_id": "west-stairs" if kind == "stairs" else "main-lift",
        "from_floor_id": source,
        "to_floor_id": target,
        "travel_seconds": travel,
        "dwell_before": 0,
        "dwell_after": 0,
    }


def transition_routes(floors, g):
    ids = [spec["id"] for spec in floors]
    top = floors[-1]["name"].lower()
    out = []
    for kind, route_id, name, entry, seconds in [
        ("stairs", "ascent_stairs", f"Stair ascent to the {top}", g.stair, TRAVEL_SECONDS),
        ("elevator", "ascent_lift", f"Lift ascent to the {top}", g.lift, LIFT_TRAVEL_SECONDS),
    ]:
        legs = [corridor_leg(f, entry, g, first=i == 0) for i, f in enumerate(ids)]
        out.append({
            "id": route_id, "name": name, "loop": False, "legs": legs,
            "transitions": [transition(f"{route_id}-{i + 1}", kind, ids[i], ids[i + 1], seconds)
                            for i in range(len(ids) - 1)],
        })
    out.append({
        "id": "descent_stairs", "name": "Stair descent to the ground floor", "loop": False,
        "legs": [reversed_leg(l) for l in reversed(out[0]["legs"])],
        "transitions": [transition(f"descent-stairs-{i + 1}", "stairs", ids[-1 - i], ids[-2 - i], TRAVEL_SECONDS)
                        for i in range(len(ids) - 1)],
    })
    return out


THEMES = [
    ("ground", "Ground floor", "G", "Entrance, offices and classrooms", "#0fb5a0", ["office", "reception", "classroom"], ["staff", "classroom", "seminar"]),
    ("first", "First floor", "01", "Lecture classrooms", "#3b82f6", ["classroom"], ["classroom"]),
    ("second", "Second floor", "02", "Laboratories", "#f59e0b", ["lab"], ["lab", "staff"]),
    ("third", "Third floor", "03", "Library and faculty rooms", "#a855f7", ["library"], ["office", "staff"]),
]


def generated_floors(prefix, g, count):
    """Evenly split rooms for the buildings without a hand-written layout."""
    floors = []
    for level, (fid, name, short, desc, color, south_kinds, north_kinds) in enumerate(THEMES[:count]):
        def split(side, kinds, x0):
            n = max(2, round((g.w - x0) / 11))
            step = (g.w - x0) / n
            return [(f"{side[0]}{level}{i}", f"{kinds[i % len(kinds)].title()} {prefix}{level}{side[0].upper()}{i + 1}",
                     kinds[i % len(kinds)], round(x0 + i * step, 1), round(x0 + (i + 1) * step, 1)) for i in range(n)]
        south, north = split("south", south_kinds, 0), split("north", north_kinds, 8)
        if fid == "ground":  # the entrance room is the one nearest the middle
            index = min(range(len(south)), key=lambda i: abs((south[i][3] + south[i][4]) / 2 - g.w / 2))
            south[index] = ("entrance", "Main entrance & lobby", "reception", south[index][3], south[index][4])
        pick = lambda rows, picks: [rows[i % len(rows)][0] for i in picks]
        floors.append({
            "id": fid, "name": name, "short_name": short, "elevation": [0, 4.2, 8.4, 12.6][level],
            "color": color, "description": desc, "south": south, "north": north,
            "visits": pick(south, [0, 2]) + pick(north, [1, 3]),
            "evaluation_visits": pick(south, [1, 3]) + pick(north, [0, 2]),
        })
    return floors


# id suffix, display name, geometry, floor count (None = hand-written FLOORS), footprint source.
BUILDINGS = [
    ("", "VIT Pune - Main block (approximate)", Geo(60, 36, 14), None, "main block"),
    ("_west", "VIT Pune - West block (approximate)", Geo(36, 30, 10), 3, "OSM way 223964767, ~816 m2"),
    ("_east", "VIT Pune - East block (approximate)", Geo(40, 30, 10), 3, "OSM way 223964773, ~1018 m2"),
    ("_north", "VIT Pune - North block (approximate)", Geo(72, 46, 18), 4, "OSM way 223964769, ~3389 m2"),
]


def build(suffix, name, g, count, source):
    floors = FLOORS if count is None else generated_floors(suffix[1:2].upper() or "M", g, count)
    return {
        "schema_version": "3.1",
        "version": "3.1.0",
        "id": f"vit_pune{suffix}_v3",
        "name": name,
        "description": f"Approximate {len(floors)}-floor academic building of VIT Pune, Bibwewadi, for synthetic localization experiments.",
        "width": g.w,
        "height": g.l,
        "default_floor_id": "ground",
        "floors": [make_floor(spec, g) for spec in floors],
        "connections": [
            {
                "id": identifier,
                "kind": kind,
                "stops": [{"floor_id": spec["id"], "access_id": access} for spec in floors],
            }
            for identifier, kind, access in [
                ("west-stairs", "stairs", "stair-west"),
                ("main-lift", "elevator", "lift-west"),
            ]
        ],
        "target_device_height": 1.2,
        "slab_bias_per_slab": 1.5,
        "transition_routes": transition_routes(floors, g),
        "validation": {
            "units": "metres",
            "source": "fictional-procedural",
            "geometry_revision": "vit-pune-2026-10-24.2",
            "notes": f"Approximate, not surveyed ({source}): room names are generic, sizes and layout are "
            "estimates. 4.2 m storeys; wall height 3.2 m. Routes remain on one known floor.",
        },
    }


if __name__ == "__main__":
    for suffix, name, g, count, source in BUILDINGS:
        destination = Path(__file__).resolve().parents[1] / f"configs/environments/university_vit_pune{suffix}.json"
        destination.write_text(json.dumps(build(suffix, name, g, count, source), indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {destination}")
