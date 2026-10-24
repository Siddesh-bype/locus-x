"""V3 composes the existing floor contract; the V1 Environment remains unchanged."""

from collections.abc import Collection
from math import dist
from typing import Literal, get_args

from pydantic import Field, model_validator

from .geometry import intersects, rectangle_bounds, segment_in_regions
from .schemas import Anchor, Environment, Point, Room, StrictModel, Waypoint

# 3.1 only adds optional fields, so 3.0 documents keep validating byte-for-byte.
SchemaVersion = Literal["3.0", "3.1"]
BUILDING_SCHEMA_VERSIONS: frozenset[str] = frozenset(get_args(SchemaVersion))

DEFAULT_MOUNT_HEIGHT = 2.6
"""Metres above the floor datum for anchors absent from ``anchor_mount_heights``."""


def is_building_config(raw: dict) -> bool:
    """Single loader discriminator, derived from the literal so it cannot go stale."""
    version = raw.get("schema_version")
    return isinstance(version, str) and version in BUILDING_SCHEMA_VERSIONS


def parse_anchor_key(key: str) -> tuple[str, str]:
    """Split a floor-scoped ``"<floor_id>:<anchor_id>"`` key, rejecting empty parts."""
    floor_id, separator, anchor_id = key.partition(":")
    if not separator or not floor_id or not anchor_id:
        raise ValueError(
            f"Malformed floor-scoped anchor key {key!r}; expected '<floor_id>:<anchor_id>'"
        )
    return floor_id, anchor_id


def validate_anchor_keys(keys: Collection[str], installed: Collection[str]) -> None:
    """Reject duplicate, malformed or unknown floor-scoped anchor keys.

    ``installed`` holds the configuration's valid keys. A 2D environment has no floors, so
    it has nothing to pass here and must reject floor-scoped keys outright instead.
    """
    if len(keys) != len(set(keys)):
        raise ValueError("Floor-scoped anchor keys must be unique")
    known = set(installed)
    for key in keys:
        floor_id, anchor_id = parse_anchor_key(key)
        if f"{floor_id}:{anchor_id}" not in known:
            raise ValueError(
                f"Unknown floor-scoped anchor key {key!r}; no such floor/anchor pair is installed"
            )


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
    # Anchor ID -> metres above the floor datum; unlisted anchors use DEFAULT_MOUNT_HEIGHT.
    anchor_mount_heights: dict[str, float] = Field(default_factory=dict)

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
        installed = {anchor.id for anchor in self.anchors}
        for anchor_id, mount_height in self.anchor_mount_heights.items():
            if anchor_id not in installed:
                raise ValueError(
                    f"anchor_mount_heights on floor {self.id} references unknown anchor "
                    f"{anchor_id!r}; installed anchors are {sorted(installed)}"
                )
            if not mount_height > 0:
                raise ValueError(
                    f"Mount height for anchor {anchor_id!r} on floor {self.id} must be positive"
                )
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


class RouteLeg(StrictModel):
    """One floor-local leg; the vertical traverse is a RouteTransition, never a leg."""

    floor_id: str = Field(min_length=1)
    waypoints: list[Waypoint] = Field(min_length=2)


class RouteTransition(StrictModel):
    """A vertical traverse between two floors through one declared connection."""

    id: str = Field(min_length=1)
    kind: Literal["stairs", "elevator"]
    connection_id: str = Field(min_length=1)
    from_floor_id: str = Field(min_length=1)
    to_floor_id: str = Field(min_length=1)
    travel_seconds: float = Field(gt=0)
    dwell_before: float = Field(ge=0)
    dwell_after: float = Field(ge=0)


class MultiFloorRoute(StrictModel):
    """Structural route contract only; floor and connector geometry is checked by Building."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    loop: bool = False
    legs: list[RouteLeg] = Field(min_length=2)
    transitions: list[RouteTransition]

    @model_validator(mode="after")
    def validate_structure(self):
        expected = len(self.legs) if self.loop else len(self.legs) - 1
        if len(self.transitions) != expected:
            kind = "loop" if self.loop else "one-way"
            raise ValueError(
                f"Route {self.id} is {kind} with {len(self.legs)} legs and needs exactly "
                f"{expected} transitions, got {len(self.transitions)}"
            )
        if len({transition.id for transition in self.transitions}) != len(self.transitions):
            raise ValueError(f"Transition IDs must be unique within route {self.id}")
        return self


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
    """A versioned building. Every 3.1 addition is optional so 3.0 documents keep loading."""

    schema_version: SchemaVersion
    version: str = Field(min_length=1)
    id: str = Field(min_length=1)
    name: str
    description: str
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    default_floor_id: str
    floors: list[Floor] = Field(min_length=1)
    connections: list[FloorConnection]
    # Device height above the selected floor datum, used by the 3D projection only.
    target_device_height: float = Field(default=1.2, gt=0, lt=4)
    # Synthetic attenuation: metres of added range per intervening floor slab.
    slab_bias_per_slab: float = Field(default=1.5, ge=0)
    # Populated by the route task; the models and their structural rules live here.
    transition_routes: list[MultiFloorRoute] = Field(default_factory=list)
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
            gaps = []
            if i:
                gaps.append(floor.elevation - self.floors[i - 1].elevation)
            if i + 1 < len(self.floors):
                gaps.append(self.floors[i + 1].elevation - floor.elevation)
            if gaps:
                limit = min(gaps) - 0.2
                for anchor in floor.anchors:
                    mount_height = floor.anchor_mount_heights.get(anchor.id, DEFAULT_MOUNT_HEIGHT)
                    if mount_height > limit + 1e-9:
                        raise ValueError(
                            f"Mount height for anchor {anchor.id!r} on floor {floor.id} must not "
                            f"exceed {limit:g} m (0.2 m slab clearance below the adjacent floor)"
                        )
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
        route_ids = [route.id for route in self.transition_routes]
        if len(set(route_ids)) != len(route_ids):
            raise ValueError("Route IDs must be unique")
        transition_ids = [
            transition.id for route in self.transition_routes for transition in route.transitions
        ]
        if len(set(transition_ids)) != len(transition_ids):
            raise ValueError("Transition IDs must be unique across the building")
        connections = {connection.id: connection for connection in self.connections}
        for route in self.transition_routes:
            for leg in route.legs:
                if leg.floor_id not in floors:
                    raise ValueError(f"Route {route.id} references unknown floor {leg.floor_id!r}")
            for transition in route.transitions:
                if transition.from_floor_id not in floors or transition.to_floor_id not in floors:
                    raise ValueError(
                        f"Transition {transition.id} references an unknown floor "
                        f"({transition.from_floor_id!r} -> {transition.to_floor_id!r})"
                    )
                connection = connections.get(transition.connection_id)
                if connection is None:
                    raise ValueError(
                        f"Transition {transition.id} references unknown connection "
                        f"{transition.connection_id!r}"
                    )
                if transition.kind != connection.kind:
                    raise ValueError(
                        f"Transition {transition.id} kind {transition.kind!r} does not match "
                        f"connection {connection.id!r} kind {connection.kind!r}"
                    )
        # Geometry is validated here, not in MultiFloorRoute, so an invalid route cannot load.
        movement = {}
        for floor in self.floors:
            walkable = {region.id: region for region in [*floor.rooms, *floor.corridors]}
            movement[floor.id] = [walkable[ref] for ref in floor.movement_regions]
        for route in self.transition_routes:
            for leg_index, leg in enumerate(route.legs):
                # A leg is traversed locally, so it is never closed back to its first waypoint.
                for start, end in zip(leg.waypoints, leg.waypoints[1:], strict=False):
                    if any(
                        intersects(start.position, end.position, wall.a, wall.b)
                        for wall in floors[leg.floor_id].walls
                    ):
                        raise ValueError(
                            f"Route {route.id} leg {leg_index} on floor {leg.floor_id} "
                            "crosses a solid wall"
                        )
                    if not segment_in_regions(start.position, end.position, movement[leg.floor_id]):
                        raise ValueError(
                            f"Route {route.id} leg {leg_index} on floor {leg.floor_id} "
                            "leaves valid movement regions"
                        )
            for i, transition in enumerate(route.transitions):
                following = (i + 1) % len(route.legs)
                source, target = route.legs[i], route.legs[following]
                if (
                    source.floor_id != transition.from_floor_id
                    or target.floor_id != transition.to_floor_id
                ):
                    raise ValueError(
                        f"Route {route.id} transition {transition.id} goes "
                        f"{transition.from_floor_id!r} -> {transition.to_floor_id!r} but joins "
                        f"leg {i} on floor {source.floor_id!r} to leg {following} on floor "
                        f"{target.floor_id!r}"
                    )
                connection = connections[transition.connection_id]
                stop_floors = [stop.floor_id for stop in connection.stops]
                missing = [
                    floor_id
                    for floor_id in (transition.from_floor_id, transition.to_floor_id)
                    if floor_id not in stop_floors
                ]
                if missing:
                    raise ValueError(
                        f"Route {route.id} transition {transition.id} uses connection "
                        f"{connection.id!r}, which has no stop on floor {missing[0]!r}; "
                        f"stops are {stop_floors}"
                    )
                separation = abs(
                    stop_floors.index(transition.from_floor_id)
                    - stop_floors.index(transition.to_floor_id)
                )
                if separation != 1:
                    raise ValueError(
                        f"Route {route.id} transition {transition.id} must connect floors that "
                        f"are adjacent in connection {connection.id!r}: "
                        f"{transition.from_floor_id!r} -> {transition.to_floor_id!r} are not "
                        f"consecutive in {stop_floors}"
                    )
                # A shaft is not a movement region, so a leg may only reach it at its door
                # midpoint; matching the entry keeps every floor leg inside walkable space.
                cores = {}
                for floor_id in (transition.from_floor_id, transition.to_floor_id):
                    stop = next(stop for stop in connection.stops if stop.floor_id == floor_id)
                    cores[floor_id] = next(
                        core for core in floors[floor_id].access_cores if core.id == stop.access_id
                    )
                source_core = cores[transition.from_floor_id]
                target_core = cores[transition.to_floor_id]
                # The traverse reports one horizontal point (the from-floor entry), so both
                # floors' entries must agree or the trajectory would step sideways mid-air.
                if dist(source_core.entry, target_core.entry) > 1e-6:
                    raise ValueError(
                        f"Route {route.id} transition {transition.id} uses connection "
                        f"{connection.id!r} between floors {transition.from_floor_id!r} and "
                        f"{transition.to_floor_id!r}, but their access-core entries differ: "
                        f"{source_core.entry} vs {target_core.entry}"
                    )
                for leg_index, leg, endpoint, side in (
                    (i, source, source.waypoints[-1], "end"),
                    (following, target, target.waypoints[0], "start"),
                ):
                    core = cores[leg.floor_id]
                    if dist(endpoint.position, core.entry) > 1e-6:
                        raise ValueError(
                            f"Route {route.id} leg {leg_index} on floor {leg.floor_id} must "
                            f"{side} at the {core.id!r} entry {core.entry} of transition "
                            f"{transition.id}; got {endpoint.position}"
                        )
        return self


def config_model(raw: dict) -> type[Environment] | type[Building]:
    """The model for a raw configuration document; the only loader discriminator."""
    return Building if is_building_config(raw) else Environment
