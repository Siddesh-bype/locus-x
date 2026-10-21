"""V3 composes the existing floor contract; the V1 Environment remains unchanged."""

from math import dist
from typing import Literal

from pydantic import Field, model_validator

from .geometry import intersects, rectangle_bounds, segment_in_regions
from .schemas import Anchor, Environment, Point, Room, StrictModel


class BuildingRoom(Room):
    id: str = Field(min_length=1)


class Region(StrictModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    center: Point
    size: Point


class AccessCore(Region):
    kind: Literal["stairs", "elevator"]
    entry: Point


class Door(StrictModel):
    """An opening in the solid wall segments, never a second propagation wall."""

    id: str = Field(min_length=1)
    a: Point
    b: Point
    height: float = Field(default=2.2, gt=0)
    connects: tuple[str, str]


class Floor(Environment):
    short_name: str = Field(min_length=1)
    elevation: float = Field(ge=0)
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    rooms: list[BuildingRoom]
    corridors: list[Region]
    doors: list[Door]
    access_cores: list[AccessCore]
    # References avoid a second, divergent copy of room/corridor geometry.
    movement_regions: list[str] = Field(min_length=1)
    candidate_anchors: list[Anchor] = Field(default_factory=list)
    anchor_layout_id: str = Field(min_length=1)
    route_names: dict[str, str]

    @model_validator(mode="after")
    def validate_floor(self):
        groups = {
            "wall": self.walls,
            "room": self.rooms,
            "corridor": self.corridors,
            "door": self.doors,
            "access core": self.access_cores,
            "candidate anchor": self.candidate_anchors,
        }
        for kind, items in groups.items():
            if len({item.id for item in items}) != len(items):
                raise ValueError(f"{kind} IDs must be unique within a floor")
        if {a.id for a in self.anchors} & {a.id for a in self.candidate_anchors}:
            raise ValueError("Candidate anchor IDs must be separate from installed anchor IDs")
        regions = [*self.rooms, *self.corridors, *self.access_cores]
        by_id = {region.id: region for region in regions}
        if len(by_id) != len(regions) or "outside" in by_id:
            raise ValueError("Room, corridor and access IDs must be unique; outside is reserved")
        for region in regions:
            if any(v <= 0 for v in region.size):
                raise ValueError("Region sizes must be positive")
            x0, y0, x1, y1 = rectangle_bounds(region.center, region.size)
            if not (0 <= x0 < x1 <= self.width and 0 <= y0 < y1 <= self.height):
                raise ValueError("Region bounds must lie within the floor dimensions")
        for i, first in enumerate(regions):
            a = rectangle_bounds(first.center, first.size)
            for second in regions[i + 1 :]:
                b = rectangle_bounds(second.center, second.size)
                if (
                    min(a[2], b[2]) > max(a[0], b[0]) + 1e-9
                    and min(a[3], b[3]) > max(a[1], b[1]) + 1e-9
                ):
                    raise ValueError(f"Regions overlap: {first.id}, {second.id}")
        if len(set(self.movement_regions)) != len(self.movement_regions):
            raise ValueError("Movement region references must be unique")
        walkable = {r.id: r for r in [*self.rooms, *self.corridors]}
        if set(self.movement_regions) - walkable.keys():
            raise ValueError("Movement regions must reference rooms/corridors, never shafts")
        if set(self.route_names) != set(self.routes):
            raise ValueError("Every route must have exactly one display name")
        for anchor in self.candidate_anchors:
            if not (
                0 <= anchor.position[0] <= self.width and 0 <= anchor.position[1] <= self.height
            ):
                raise ValueError("Candidate anchors must lie within the floor dimensions")
        for wall in self.walls:
            if dist(wall.a, wall.b) < 1e-6:
                raise ValueError("Wall segments must have nonzero length")
        for door in self.doors:
            if dist(door.a, door.b) < 0.6:
                raise ValueError("Door openings must be at least 0.6 m wide")
            if len(set(door.connects)) != 2 or set(door.connects) - (by_id.keys() | {"outside"}):
                raise ValueError("Door connections must reference two distinct regions")
            for point in (door.a, door.b):
                if not (0 <= point[0] <= self.width and 0 <= point[1] <= self.height):
                    raise ValueError("Door openings must lie within the floor dimensions")
                if not any(dist(point, p) < 1e-6 for w in self.walls for p in (w.a, w.b)):
                    raise ValueError("Door endpoints must meet solid wall endpoints")
            # Endpoints touch jambs by design; only the open interior must be wall-free.
            inner_a = tuple(a + (b - a) * 1e-5 for a, b in zip(door.a, door.b, strict=True))
            inner_b = tuple(b + (a - b) * 1e-5 for a, b in zip(door.a, door.b, strict=True))
            if any(intersects(inner_a, inner_b, w.a, w.b) for w in self.walls):
                raise ValueError("Door opening overlaps a solid wall")
            midpoint = tuple((a + b) / 2 for a, b in zip(door.a, door.b, strict=True))
            for ref in door.connects:
                if ref == "outside":
                    if not (
                        door.a[0] == door.b[0] in (0, self.width)
                        or door.a[1] == door.b[1] in (0, self.height)
                    ):
                        raise ValueError("Outside doors must lie on the building perimeter")
                else:
                    r = by_id[ref]
                    bounds = rectangle_bounds(r.center, r.size)
                    if not (
                        bounds[0] - 1e-6 <= midpoint[0] <= bounds[2] + 1e-6
                        and bounds[1] - 1e-6 <= midpoint[1] <= bounds[3] + 1e-6
                        and min(
                            abs(midpoint[0] - bounds[0]),
                            abs(midpoint[0] - bounds[2]),
                            abs(midpoint[1] - bounds[1]),
                            abs(midpoint[1] - bounds[3]),
                        )
                        < 1e-6
                    ):
                        raise ValueError("Door must lie on the boundary of connected regions")
        for core in self.access_cores:
            if not any(
                core.id in d.connects
                and dist(core.entry, tuple((a + b) / 2 for a, b in zip(d.a, d.b, strict=True)))
                < 1e-6
                for d in self.doors
            ):
                raise ValueError("Access core entry must match a declared door opening")
        movement = [walkable[ref] for ref in self.movement_regions]
        for name, route in self.routes.items():
            for i, waypoint in enumerate(route):
                end = route[(i + 1) % len(route)].position
                if any(intersects(waypoint.position, end, wall.a, wall.b) for wall in self.walls):
                    raise ValueError(f"Route {name} crosses a solid wall")
                if not segment_in_regions(waypoint.position, end, movement):
                    raise ValueError(f"Route {name} leaves valid movement regions")
        return self


class ConnectionStop(StrictModel):
    floor_id: str
    access_id: str


class FloorConnection(StrictModel):
    id: str
    kind: Literal["stairs", "elevator"]
    stops: list[ConnectionStop] = Field(min_length=2)


class ValidationMetadata(StrictModel):
    units: Literal["metres"] = "metres"
    source: Literal["fictional-procedural"] = "fictional-procedural"
    geometry_revision: str
    notes: str


class Building(StrictModel):
    schema_version: Literal["3.0"]
    version: str = Field(min_length=1)
    id: str = Field(min_length=1)
    name: str
    description: str
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    default_floor_id: str
    floors: list[Floor] = Field(min_length=1)
    connections: list[FloorConnection]
    validation: ValidationMetadata

    @model_validator(mode="after")
    def validate_building(self):
        floors = {floor.id: floor for floor in self.floors}
        if len(floors) != len(self.floors) or self.default_floor_id not in floors:
            raise ValueError("Floor IDs must be unique and default_floor_id must exist")
        elevations = [floor.elevation for floor in self.floors]
        if elevations != sorted(set(elevations)):
            raise ValueError("Floors must have unique, ascending elevations")
        for i, floor in enumerate(self.floors):
            if floor.width > self.width or floor.height > self.height:
                raise ValueError("Floor dimensions exceed the building footprint")
            if (
                i + 1 < len(self.floors)
                and self.floors[i + 1].elevation - floor.elevation
                < max((wall.height for wall in floor.walls), default=0) + 0.2
            ):
                raise ValueError("Floor elevations must provide wall and slab clearance")
        if len({c.id for c in self.connections}) != len(self.connections):
            raise ValueError("Connection IDs must be unique")
        linked = set()
        for connection in self.connections:
            first = None
            stop_floors = set()
            for stop in connection.stops:
                if stop.floor_id not in floors or stop.floor_id in stop_floors:
                    raise ValueError("Connection stops must reference distinct existing floors")
                stop_floors.add(stop.floor_id)
                core = next(
                    (c for c in floors[stop.floor_id].access_cores if c.id == stop.access_id), None
                )
                if core is None or core.kind != connection.kind:
                    raise ValueError(
                        "Connection stop must reference an access core of the same kind"
                    )
                if first and (first.center != core.center or first.size != core.size):
                    raise ValueError("Connected stairs/elevators must align vertically")
                key = (stop.floor_id, stop.access_id)
                if key in linked:
                    raise ValueError("An access core may belong to only one connection")
                linked.add(key)
                first = core
        if linked != {(f.id, c.id) for f in self.floors for c in f.access_cores}:
            raise ValueError("Every access core must belong to a building connection")
        return self
