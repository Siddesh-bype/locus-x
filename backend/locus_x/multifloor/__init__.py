"""Building-coordinate projection and route evaluation for the multi-floor work.

The legacy 2D pipeline never imports this package; it is an additive layer over the
validated configuration.
"""

from .coordinates import (
    DEFAULT_MOUNT_HEIGHT,
    Anchor3D,
    anchor_index,
    floor_anchors,
    resolve_anchor,
    target_z,
)
from .route import (
    DEFAULT_ROUTE_SPEED,
    Phase,
    RouteState,
    route_state,
    transition_windows,
)

__all__ = [
    "DEFAULT_MOUNT_HEIGHT",
    "DEFAULT_ROUTE_SPEED",
    "Anchor3D",
    "Phase",
    "RouteState",
    "anchor_index",
    "floor_anchors",
    "resolve_anchor",
    "route_state",
    "target_z",
    "transition_windows",
]
