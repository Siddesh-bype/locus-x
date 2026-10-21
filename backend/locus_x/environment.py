import json
from pathlib import Path

import numpy as np

from .building import Building, Floor
from .geometry import intersects as intersects
from .schemas import Environment, Point, Waypoint

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "environments"


def load_environments() -> dict[str, Environment | Building]:
    environments = {}
    for path in sorted(CONFIG_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        model = Building if raw.get("schema_version") == "3.0" else Environment
        data = model.model_validate(raw)
        if data.id in environments:
            raise ValueError(f"Duplicate environment ID: {data.id}")
        environments[data.id] = data
    return environments


def resolve_floor(environment: Environment | Building, floor_id: str | None = None) -> Environment:
    """Select geometry before simulation. This does not estimate the target's floor."""
    if isinstance(environment, Building):
        selected = floor_id or environment.default_floor_id
        for floor in environment.floors:
            if floor.id == selected:
                return floor
        raise ValueError(f"Unknown floor: {selected}")
    if floor_id is not None and floor_id != environment.id:
        raise ValueError(f"Unknown floor: {floor_id}")
    return environment


def floor_elevation(floor: Environment) -> float:
    return floor.elevation if isinstance(floor, Floor) else 0.0


def route_position(route: list[Waypoint], timestamp: float, speed: float) -> Point:
    segments = []
    for i, waypoint in enumerate(route):
        next_point = np.asarray(route[(i + 1) % len(route)].position)
        point = np.asarray(waypoint.position)
        duration = float(np.linalg.norm(next_point - point) / speed)
        segments.append((point, next_point, waypoint.dwell, duration))
    period = sum(dwell + duration for _, _, dwell, duration in segments)
    if period <= 0:
        return route[0].position
    remaining = timestamp % period
    for point, next_point, dwell, duration in segments:
        if remaining <= dwell:
            return tuple(point)
        remaining -= dwell
        if remaining <= duration and duration > 0:
            return tuple(point + (next_point - point) * remaining / duration)
        remaining -= duration
    return route[0].position
