"""Building-coordinate projection, route evaluation and 3D observations.

The legacy 2D pipeline never imports this package; it is an additive layer over the
validated configuration.
"""

from .coordinates import (
    DEFAULT_MOUNT_HEIGHT,
    Anchor3D,
    anchor_index,
    anchor_key,
    floor_anchors,
    resolve_anchor,
    target_z,
)
from .route import (
    DEFAULT_ROUTE_SPEED,
    Phase,
    RouteState,
    TransitionWindow,
    route_state,
    transition_windows,
)
from .simulation import (
    MAX_SLABS,
    OUTAGE_INDICES,
    count_slab_hops,
    generate_measurements_3d,
)

__all__ = [
    "DEFAULT_MOUNT_HEIGHT",
    "DEFAULT_ROUTE_SPEED",
    "MAX_SLABS",
    "OUTAGE_INDICES",
    "Anchor3D",
    "Phase",
    "RouteState",
    "TransitionWindow",
    "anchor_index",
    "anchor_key",
    "count_slab_hops",
    "floor_anchors",
    "generate_measurements_3d",
    "resolve_anchor",
    "route_state",
    "target_z",
    "transition_windows",
]
