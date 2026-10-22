"""Vertical observability of a range-only position solve in the multi-floor hospital.

The probe loads the versioned building through the runtime catalog, projects every
installed anchor into building coordinates and evaluates the 3 × 3 range-only
information matrix over a grid of target positions on each floor. Range noise is
one metre (unit noise); a real estimate scales the reported sigma_z by the range
sigma of that run.

Nothing in the production estimator consumes these numbers: this is an audit
measurement of the geometry, recorded in docs/multifloor-audit.md.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe scripts/probe_vertical_observability.py
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.locus_x.building import Building, Floor  # noqa: E402
from backend.locus_x.environment import load_environments  # noqa: E402

BUILDING_ID = "hospital_v3"
DEFAULT_MOUNT_HEIGHT = 2.6
DEFAULT_DEVICE_HEIGHT = 1.2
GRID_SPACING = 2.0


def floor_anchors(building: Building) -> list[np.ndarray]:
    """Installed anchors per floor, as (x, y, z) rows in building coordinates."""
    per_floor = []
    for floor in building.floors:
        # The 3.1 schema may override individual mount heights; 3.0 has none.
        heights = getattr(floor, "anchor_mount_heights", {}) or {}
        points = []
        for anchor in floor.anchors:
            mount = heights.get(anchor.id, DEFAULT_MOUNT_HEIGHT)
            points.append((anchor.position[0], anchor.position[1], floor.elevation + mount))
        per_floor.append(np.asarray(points, dtype=float))
    return per_floor


def target_grid(floor: Floor, spacing: float = GRID_SPACING) -> list[tuple[float, float]]:
    """Interior grid over one floor footprint, at a fixed spacing in metres."""
    xs = np.arange(spacing, floor.width - 1e-9, spacing)
    ys = np.arange(spacing, floor.height - 1e-9, spacing)
    return [(float(x), float(y)) for x in xs for y in ys]


def information(anchors: np.ndarray, target: np.ndarray) -> np.ndarray:
    """J^T J for unit-weight range measurements; rows are unit anchor directions."""
    deltas = anchors - target
    distances = np.linalg.norm(deltas, axis=1)
    if np.any(distances < 1e-9):
        raise ValueError("A target position coincides with an anchor")
    directions = deltas / distances[:, None]
    return directions.T @ directions


def floor_statistics(
    anchors: np.ndarray, floor: Floor, device_height: float, spacing: float = GRID_SPACING
) -> tuple[float, float]:
    """Median sigma_z and median cond(H) for a target grid on one floor."""
    sigma, condition = [], []
    for x, y in target_grid(floor, spacing):
        matrix = information(anchors, np.array([x, y, floor.elevation + device_height]))
        covariance = np.linalg.inv(matrix)
        sigma.append(float(np.sqrt(covariance[2, 2])))
        condition.append(float(np.linalg.cond(matrix)))
    return float(np.median(sigma)), float(np.median(condition))


def main() -> int:
    catalog = load_environments()
    building = catalog.get(BUILDING_ID)
    if not isinstance(building, Building):
        print(f"Configuration {BUILDING_ID!r} is not a versioned building", file=sys.stderr)
        return 1

    device_height = getattr(building, "target_device_height", DEFAULT_DEVICE_HEIGHT)
    per_floor = floor_anchors(building)
    all_anchors = np.vstack(per_floor)
    print(
        f"{building.name} ({building.id}): {len(building.floors)} floors, "
        f"{len(all_anchors)} installed anchors, unit range noise"
    )
    print(f"Target grid: {GRID_SPACING:g} m spacing, device height {device_height:g} m above datum")
    print()
    print(
        f"{'Floor':8s} {'Datum':>7s} {'Device z':>9s} {'Points':>7s} "
        f"{'sigma_z':>8s} {'cond(H)':>9s}"
    )
    for floor in building.floors:
        sigma, condition = floor_statistics(all_anchors, floor, device_height)
        print(
            f"{floor.id:8s} {floor.elevation:7.1f} {floor.elevation + device_height:9.1f} "
            f"{len(target_grid(floor)):7d} {sigma:8.2f} {condition:9.2f}"
        )

    print()
    print("Reference, selected floor's own eight coplanar anchors only (storey not resolvable):")
    print(f"{'Floor':8s} {'Anchors':>7s} {'sigma_z':>8s} {'cond(H)':>9s}")
    for floor, own in zip(building.floors, per_floor, strict=True):
        sigma, condition = floor_statistics(own, floor, device_height)
        print(f"{floor.id:8s} {len(own):7d} {sigma:8.2f} {condition:9.2f}")

    print()
    print("Multiply sigma_z by the range sigma of a real run; sigma_z is not an estimate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
