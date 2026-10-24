"""Deterministic geometric observability diagnostics for a layout.

These are *static* checks computed from anchor coordinates, wall geometry and
target-space sample points. They do not simulate radio faults; they capture
whether a layout can constrain a position at all (range geometry conditioning
and LOS availability). Visual map coverage is explicitly not this metric: a
point can be close to anchors yet yield ill-conditioned or heavily obstructed
range geometry.
"""

from __future__ import annotations

import numpy as np

from ..geometry import intersects
from ..schemas import Environment, Point
from .sites import region_at

#: Same conditioning threshold the estimator uses to reject a fix.
CONDITION_LIMIT = 1000.0


def sample_route(route, spacing: float = 0.75) -> list[Point]:
    """Sample a closed waypoint route into dense target-space points."""
    points: list[Point] = []
    for i, waypoint in enumerate(route):
        end = route[(i + 1) % len(route)].position
        start = np.asarray(waypoint.position)
        stop = np.asarray(end)
        length = float(np.linalg.norm(stop - start))
        count = max(1, int(length / spacing))
        for step in range(count + 1):
            point = start + (stop - start) * step / count
            points.append((float(point[0]), float(point[1])))
    return points


def los_anchors(
    point: Point, anchors: list[tuple[str, Point]], environment: Environment
) -> list[str]:
    """Anchor IDs with an unobstructed straight path to ``point``."""
    return [
        anchor_id
        for anchor_id, position in anchors
        if not any(intersects(point, position, wall.a, wall.b) for wall in environment.walls)
    ]


def geometry_condition(point: Point, anchors: list[Point]) -> float:
    """Condition number of the range-information matrix at ``point``."""
    if len(anchors) < 3:
        return float("inf")
    points = np.asarray(anchors, dtype=float)
    delta = np.asarray(point, dtype=float) - points
    norms = np.maximum(np.linalg.norm(delta, axis=1, keepdims=True), 1e-9)
    jacobian = delta / norms
    information = jacobian.T @ jacobian
    try:
        condition = float(np.linalg.cond(information))
    except np.linalg.LinAlgError:
        return float("inf")
    return condition if np.isfinite(condition) else float("inf")


def layout_observability(
    environment: Environment,
    anchors: list[tuple[str, Point]],
    samples: list[Point],
    regions=None,
) -> dict:
    """Fraction of sample points with >=3 LOS anchors and acceptable conditioning.

    ``regions`` are rectangle objects (rooms/corridors) used only for per-region
    reporting; points outside every region are grouped as ``other``.
    """
    shells = list(regions or [])
    by_id = dict(anchors)
    total = observable = poor = 0
    conditions: list[float] = []
    per_region: dict[str, dict[str, int]] = {}
    for point in samples:
        total += 1
        visible = [by_id[anchor_id] for anchor_id in los_anchors(point, anchors, environment)]
        condition = geometry_condition(point, visible)
        ok = len(visible) >= 3 and condition <= CONDITION_LIMIT
        observable += int(ok)
        poor += not ok
        if np.isfinite(condition):
            conditions.append(condition)
        name = region_at(point, shells) or "other"
        slot = per_region.setdefault(name, {"samples": 0, "observable": 0})
        slot["samples"] += 1
        slot["observable"] += int(ok)
    return {
        "observability": observable / total if total else 0.0,
        "poor_geometry_fraction": poor / total if total else 0.0,
        "mean_condition": float(np.mean(conditions)) if conditions else None,
        "samples": total,
        "per_region": {
            name: {
                "samples": slot["samples"],
                "observable": slot["observable"],
                "observability": slot["observable"] / slot["samples"] if slot["samples"] else 0.0,
            }
            for name, slot in per_region.items()
        },
    }
