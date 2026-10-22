"""Floor-local anchors projected into one building coordinate system.

Pure projection: no I/O, no simulation, no estimator imports. Geometry comes from
the validated configuration, never from a runtime frame, and nothing here
estimates a position.
"""

from dataclasses import dataclass

from ..building import DEFAULT_MOUNT_HEIGHT, Building, Floor
from ..schemas import Anchor


@dataclass(frozen=True, slots=True)
class Anchor3D:
    """An installed anchor in building coordinates (metres, shared local origin)."""

    anchor_id: str
    floor_id: str
    x: float
    y: float
    z: float


def anchor_key(floor_id: str, anchor_id: str) -> str:
    """Stable building-wide anchor key; anchor IDs are only unique within a floor."""
    return f"{floor_id}:{anchor_id}"


def anchor_height(floor: Floor, anchor_id: str) -> float:
    """Configured mount height of one anchor, defaulting to ``DEFAULT_MOUNT_HEIGHT``."""
    return floor.anchor_mount_heights.get(anchor_id, DEFAULT_MOUNT_HEIGHT)


def project_anchor(floor: Floor, anchor: Anchor) -> Anchor3D:
    """Project one floor-local anchor using that floor's datum and mount height."""
    return Anchor3D(
        anchor_id=anchor.id,
        floor_id=floor.id,
        x=float(anchor.position[0]),
        y=float(anchor.position[1]),
        z=floor.elevation + anchor_height(floor, anchor.id),
    )


def floor_anchors(building: Building) -> list[Anchor3D]:
    """Every installed anchor of every floor, ordered by floor then declaration order."""
    return [project_anchor(floor, anchor) for floor in building.floors for anchor in floor.anchors]


def anchor_index(building: Building) -> dict[str, Anchor3D]:
    """Every anchor keyed by ``"<floor_id>:<anchor_id>"``."""
    index: dict[str, Anchor3D] = {}
    for anchor in floor_anchors(building):
        key = anchor_key(anchor.floor_id, anchor.anchor_id)
        if key in index:
            raise ValueError(f"Duplicate anchor key {key!r} in building {building.id!r}")
        index[key] = anchor
    return index


def target_z(floor: Floor, building: Building, device_height: float | None = None) -> float:
    """Target device elevation on one floor, in building coordinates.

    ``device_height`` overrides ``building.target_device_height`` for this call.
    """
    height = building.target_device_height if device_height is None else device_height
    if not height > 0:
        raise ValueError(f"Device height must be positive, got {height!r}")
    return floor.elevation + height


def resolve_anchor(building: Building, floor_id: str, anchor_id: str) -> Anchor3D:
    """Look up one installed anchor, rejecting unknown floors and anchor IDs."""
    floor = next((candidate for candidate in building.floors if candidate.id == floor_id), None)
    if floor is None:
        known = ", ".join(candidate.id for candidate in building.floors)
        raise ValueError(
            f"Unknown floor {floor_id!r} in building {building.id!r}; known floors are {known}"
        )
    anchor = next((candidate for candidate in floor.anchors if candidate.id == anchor_id), None)
    if anchor is None:
        installed = ", ".join(candidate.id for candidate in floor.anchors)
        raise ValueError(
            f"Unknown anchor {anchor_id!r} on floor {floor_id!r} of building {building.id!r}; "
            f"installed anchors are {installed}"
        )
    return project_anchor(floor, anchor)
