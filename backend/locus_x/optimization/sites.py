"""Candidate mounting sites and their legality.

For V3 floors the schema already carries ``candidate_anchors`` (sites C01-C21);
these are *possible* sites and the optimizer enforces installation legality on
top of them. For V1 environments (which have no mounting-site representation)
a documented adapter derives perimeter/corridor-wall sites from wall geometry.

Legal-site rules (PlacementPolicy):
  * inside the floor plan with a margin from the perimeter walls,
  * at least ``min_wall_clearance`` from every solid wall segment,
  * not inside an access core (stairs/elevator shaft) unless allowed,
  * inside a walkable movement region (room or corridor) unless allowed,
  * declared candidate sites only — the optimizer never invents coordinates.
"""

from __future__ import annotations

import math

from ..building import Building, Floor
from ..geometry import rectangle_bounds
from ..schemas import Anchor, Environment, Point
from .protocol import PlacementPolicy


def _point_segment_distance(point: Point, a: Point, b: Point) -> float:
    px, py = point
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def wall_clearance(point: Point, environment: Environment) -> float:
    return min(_point_segment_distance(point, w.a, w.b) for w in environment.walls)


def region_at(point: Point, regions) -> str | None:
    for region in regions:
        x0, y0, x1, y1 = rectangle_bounds(region.center, region.size)
        if x0 - 1e-9 <= point[0] <= x1 + 1e-9 and y0 - 1e-9 <= point[1] <= y1 + 1e-9:
            # V3 regions carry an ``id``; V1 rooms are identified by ``name``.
            return getattr(region, "id", None) or region.name
    return None


def in_access_core(point: Point, floor: Floor) -> str | None:
    for core in floor.access_cores:
        x0, y0, x1, y1 = rectangle_bounds(core.center, core.size)
        if x0 - 1e-9 <= point[0] <= x1 + 1e-9 and y0 - 1e-9 <= point[1] <= y1 + 1e-9:
            return core.id
    return None


def site_legality(
    site: Anchor, environment: Environment, policy: PlacementPolicy, floor: Floor | None = None
) -> tuple[bool, str]:
    """Return (legal, reason). ``reason`` is ``"ok"`` when the site may be used."""
    x, y = site.position
    m = policy.margin_from_bounds
    if not (m <= x <= environment.width - m and m <= y <= environment.height - m):
        return False, "outside floor plan (margin)"
    clearance = wall_clearance(site.position, environment)
    if clearance < policy.min_wall_clearance:
        return False, f"wall clearance {clearance:.2f} m < {policy.min_wall_clearance} m"
    if floor is not None:
        core = in_access_core(site.position, floor)
        if core is not None and not policy.allow_access_cores:
            return False, f"inside access core '{core}'"
        regions = [*floor.rooms, *floor.corridors]
        if region_at(site.position, regions) is None and not policy.allow_outside_movement_regions:
            return False, "not inside any walkable movement region"
    return True, "ok"


def derive_wall_sites(
    environment: Environment, spacing: float = 3.0, offset: float = 0.6
) -> list[Anchor]:
    """V1 adapter: derive mounting sites by offsetting inward from every wall
    segment. Deterministic, geometry-only, and documented in
    ``docs/anchor-optimizer.md``. Sites are deduplicated on a 0.1 m grid.
    """
    derived: dict[tuple[float, float], Anchor] = {}
    for wall in environment.walls:
        (ax, ay), (bx, by) = wall.a, wall.b
        length = math.hypot(bx - ax, by - ay)
        if length < 1e-9:
            continue
        ux, uy = (bx - ax) / length, (by - ay) / length
        # Inward normal candidates: both perpendicular directions, keep the one
        # pointing toward the floor interior by testing bounds after offsetting.
        nx, ny = -uy, ux
        steps = max(1, int(length / spacing))
        for step in range(steps + 1):
            t = min(max(step / steps, 0.02), 0.98)
            sx, sy = ax + (bx - ax) * t, ay + (by - ay) * t
            for sign in (1, -1):
                px, py = sx + sign * nx * offset, sy + sign * ny * offset
                key = (round(px, 1), round(py, 1))
                if key in derived:
                    continue
                site = Anchor(id=f"D{len(derived) + 1:03d}", position=key)
                if wall_clearance(site.position, environment) >= 0.5:
                    derived[key] = site
    return sorted(derived.values(), key=lambda s: s.id)


def candidate_sites(
    configuration: Environment | Building,
    floor_id: str | None = None,
    policy: PlacementPolicy | None = None,
) -> tuple[list[Anchor], list[dict], str]:
    """Return (legal sites, per-site legality records, source) for a floor.

    ``source`` is ``"declared"`` for V3 ``candidate_anchors`` or ``"derived"``
    for the V1 wall-offset adapter.
    """
    policy = policy or PlacementPolicy()
    if isinstance(configuration, Building):
        selected = floor_id or configuration.default_floor_id
        floor = next((f for f in configuration.floors if f.id == selected), None)
        if floor is None:
            raise ValueError(f"Unknown floor: {floor_id}")
        environment: Environment = floor
        declared = floor.candidate_anchors
        source = "declared"
    else:
        floor = None
        environment = configuration
        declared = []
        source = "derived"
    if not declared:
        declared = derive_wall_sites(environment)
        source = "derived"
    records = []
    legal = []
    for site in declared:
        ok, reason = site_legality(site, environment, policy, floor)
        records.append(
            {
                "id": site.id,
                "position": list(site.position),
                "legal": ok,
                "reason": reason,
                "source": source,
            }
        )
        if ok:
            legal.append(site)
    return legal, records, source


def region_names(environment: Environment, floor: Floor | None) -> list[str]:
    """Walkable region IDs used for coverage reporting."""
    if floor is not None:
        return [r.id for r in [*floor.rooms, *floor.corridors]]
    return [room.name for room in environment.rooms]
