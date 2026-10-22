"""Timestamp -> trajectory for the building's multi-floor routes.

Pure evaluation: no I/O, no sampling and no estimation. Route validity (floor
adjacency, connector entries, wall-free segments inside movement regions) is a
load-time property enforced by :class:`~locus_x.building.Building`, so this
module only reads validated routes.

Floor legs are traversed locally instead of through ``environment.route_position``,
which wraps modulo the leg period: a one-way leg would silently loop instead of
stopping at its last waypoint. The dwell-then-move rule is the same, so per-floor
behaviour stays recognisable.

Every vertical traverse reports ``floor_id = None``; the two floors' access-core
entries are identical (Building enforces vertical alignment), so horizontal
position is unchanged by a traverse and only ``z`` moves.
"""

from dataclasses import dataclass
from math import dist
from typing import Literal

from ..building import Building, MultiFloorRoute, RouteLeg, RouteTransition
from ..schemas import Point

Phase = Literal["dwell", "move", "transition"]

DEFAULT_ROUTE_SPEED = 1.2
"""Metres per second. The authored route timings and their outage overlap assume it."""

JOIN_TOLERANCE = 1e-6
"""Horizontal join tolerance, the same bound ``Building`` uses for core entries."""


@dataclass(frozen=True, slots=True)
class RouteState:
    """One evaluated instant. ``floor_id`` is ``None`` only while crossing floors."""

    timestamp: float
    period: float
    phase: Phase
    floor_id: str | None
    x: float
    y: float
    z: float
    connection_id: str | None
    transition_id: str | None
    progress: float


@dataclass(frozen=True, slots=True)
class _Stage:
    """A constant-speed span of the timeline; ``start == end`` never occurs."""

    start: float
    end: float
    phase: Phase
    floor_id: str | None
    start_point: Point
    end_point: Point
    start_z: float
    end_z: float
    connection_id: str | None
    transition_id: str | None

    @property
    def duration(self) -> float:
        return self.end - self.start

    def at(self, offset: float, timestamp: float, period: float) -> RouteState:
        fraction = 0.0 if self.duration <= 0 else min(max(offset / self.duration, 0.0), 1.0)
        return RouteState(
            timestamp=timestamp,
            period=period,
            phase=self.phase,
            floor_id=self.floor_id,
            x=self.start_point[0] + (self.end_point[0] - self.start_point[0]) * fraction,
            y=self.start_point[1] + (self.end_point[1] - self.start_point[1]) * fraction,
            z=self.start_z + (self.end_z - self.start_z) * fraction,
            connection_id=self.connection_id,
            transition_id=self.transition_id,
            progress=fraction if self.phase == "transition" else 0.0,
        )


def _leg_stages(leg: RouteLeg, start: float, z: float, speed: float) -> tuple[float, list[_Stage]]:
    """Dwell then move through a leg; stop at the last waypoint instead of wrapping."""
    stages: list[_Stage] = []
    clock = start
    for index, waypoint in enumerate(leg.waypoints):
        if waypoint.dwell > 0:
            stages.append(
                _Stage(
                    start=clock,
                    end=clock + waypoint.dwell,
                    phase="dwell",
                    floor_id=leg.floor_id,
                    start_point=waypoint.position,
                    end_point=waypoint.position,
                    start_z=z,
                    end_z=z,
                    connection_id=None,
                    transition_id=None,
                )
            )
            clock += waypoint.dwell
        if index + 1 == len(leg.waypoints):
            break
        target = leg.waypoints[index + 1].position
        length = dist(waypoint.position, target)
        if length <= 0:
            continue
        duration = length / speed
        stages.append(
            _Stage(
                start=clock,
                end=clock + duration,
                phase="move",
                floor_id=leg.floor_id,
                start_point=waypoint.position,
                end_point=target,
                start_z=z,
                end_z=z,
                connection_id=None,
                transition_id=None,
            )
        )
        clock += duration
    return clock, stages


def _transition_stages(
    transition: RouteTransition,
    entry: Point,
    source_z: float,
    target_z: float,
    start: float,
) -> tuple[float, list[_Stage]]:
    """Hold, traverse, hold. Only the traverse itself reports ``floor_id = None``."""
    stages: list[_Stage] = []
    clock = start
    if transition.dwell_before > 0:
        stages.append(
            _Stage(
                start=clock,
                end=clock + transition.dwell_before,
                phase="dwell",
                floor_id=transition.from_floor_id,
                start_point=entry,
                end_point=entry,
                start_z=source_z,
                end_z=source_z,
                connection_id=None,
                transition_id=None,
            )
        )
        clock += transition.dwell_before
    stages.append(
        _Stage(
            start=clock,
            end=clock + transition.travel_seconds,
            phase="transition",
            floor_id=None,
            start_point=entry,
            end_point=entry,
            start_z=source_z,
            end_z=target_z,
            connection_id=transition.connection_id,
            transition_id=transition.id,
        )
    )
    clock += transition.travel_seconds
    if transition.dwell_after > 0:
        stages.append(
            _Stage(
                start=clock,
                end=clock + transition.dwell_after,
                phase="dwell",
                floor_id=transition.to_floor_id,
                start_point=entry,
                end_point=entry,
                start_z=target_z,
                end_z=target_z,
                connection_id=None,
                transition_id=None,
            )
        )
        clock += transition.dwell_after
    return clock, stages


def _assert_continuous(stages: list[_Stage], route: MultiFloorRoute) -> None:
    """The contract is continuity, so a broken timeline is a defect, not a runtime input.

    ``z`` is exact: both sides of every join are the same ``elevation + height`` value.
    Horizontal joins use the load-time bound on core-entry agreement.
    """
    for previous, following in zip(stages, stages[1:], strict=False):
        assert previous.end_z == following.start_z, (
            f"Route {route.id} has a vertical discontinuity at {previous.end:.6f} s"
        )
        assert dist(previous.end_point, following.start_point) <= JOIN_TOLERANCE, (
            f"Route {route.id} has a horizontal discontinuity at {previous.end:.6f} s"
        )
    if route.loop and stages:
        assert stages[-1].end_z == stages[0].start_z, (
            f"Loop route {route.id} changes elevation across its period boundary"
        )
        assert dist(stages[-1].end_point, stages[0].start_point) <= JOIN_TOLERANCE, (
            f"Loop route {route.id} does not return to its first waypoint"
        )


def _timeline(
    building: Building,
    route: MultiFloorRoute,
    speed: float,
    device_height: float,
) -> tuple[float, list[_Stage]]:
    """One period of the route as a contiguous list of stages, with its duration."""
    if speed <= 0:
        raise ValueError(f"Speed must be positive, got {speed!r}")
    if device_height <= 0:
        raise ValueError(f"Device height must be positive, got {device_height!r}")
    floors = {floor.id: floor for floor in building.floors}
    connections = {connection.id: connection for connection in building.connections}
    heights = {floor.id: floor.elevation + device_height for floor in building.floors}
    stages: list[_Stage] = []
    clock = 0.0
    for index, leg in enumerate(route.legs):
        clock, leg_stages = _leg_stages(leg, clock, heights[leg.floor_id], speed)
        stages.extend(leg_stages)
        if index >= len(route.transitions):
            continue
        transition = route.transitions[index]
        connection = connections[transition.connection_id]
        stop = next(stop for stop in connection.stops if stop.floor_id == transition.from_floor_id)
        core = next(
            core
            for core in floors[transition.from_floor_id].access_cores
            if core.id == stop.access_id
        )
        clock, transition_stages = _transition_stages(
            transition,
            core.entry,
            heights[transition.from_floor_id],
            heights[transition.to_floor_id],
            clock,
        )
        stages.extend(transition_stages)
    if not stages:
        raise ValueError(f"Route {route.id} has no duration at speed {speed!r}")
    _assert_continuous(stages, route)
    return stages[-1].end, stages


def route_state(
    building: Building,
    route: MultiFloorRoute,
    timestamp: float,
    speed: float = DEFAULT_ROUTE_SPEED,
    device_height: float | None = None,
) -> RouteState:
    """Evaluate a route at a simulated instant.

    ``z`` is the device elevation: the selected floor's datum plus ``device_height``.
    A traverse interpolates between the same two heights its legs report, so ``z`` is
    continuous at every phase boundary.

    One-way routes stop at their final waypoint once the period has elapsed; loop
    routes repeat with ``timestamp % period``.
    """
    if timestamp < 0:
        raise ValueError(f"Timestamp must be non-negative, got {timestamp!r}")
    height = building.target_device_height if device_height is None else device_height
    period, stages = _timeline(building, route, speed, height)
    if route.loop:
        elapsed = timestamp % period
    elif timestamp >= period:
        leg = route.legs[-1]
        point = leg.waypoints[-1].position
        floor = next(floor for floor in building.floors if floor.id == leg.floor_id)
        return RouteState(
            timestamp=timestamp,
            period=period,
            phase="dwell",
            floor_id=leg.floor_id,
            x=point[0],
            y=point[1],
            z=floor.elevation + height,
            connection_id=None,
            transition_id=None,
            progress=0.0,
        )
    else:
        elapsed = timestamp
    for stage in stages:
        if elapsed < stage.end:
            return stage.at(elapsed - stage.start, timestamp, period)
    # Unreachable for loop routes (elapsed < period) and for one-way routes below the
    # period; kept explicit so a floating-point edge clamps instead of raising.
    return stages[-1].at(stages[-1].duration, timestamp, period)


def transition_windows(
    building: Building,
    route: MultiFloorRoute,
    speed: float = DEFAULT_ROUTE_SPEED,
    device_height: float | None = None,
) -> list[tuple[float, float]]:
    """``[start, end)`` of every vertical traverse inside one period, in route order.

    Derived from the same timeline as :func:`route_state`, so it cannot drift from
    the reported states. Callers use it to reason about fixed time windows (for
    example the simulator's timed outage) without sampling.
    """
    height = building.target_device_height if device_height is None else device_height
    _, stages = _timeline(building, route, speed, height)
    return [(stage.start, stage.end) for stage in stages if stage.phase == "transition"]
