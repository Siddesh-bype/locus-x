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
from .positioning import (
    FIT_RATIO_LIMIT,
    MIN_ANCHORS_3D,
    Estimate3D,
    FloorDecision,
    FloorScores,
    decide_floor,
    floor_cell_half_gap,
    floor_scores,
    solve_3d,
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
from .tracking import (
    FloorAwareTracker,
    FloorState,
    LocalizerResult,
    MultiFloorLocalizer,
    RouteAssist,
    Tracking3D,
    TransitionState,
    vertical_speed_limit,
)

__all__ = [
    "DEFAULT_MOUNT_HEIGHT",
    "DEFAULT_ROUTE_SPEED",
    "FIT_RATIO_LIMIT",
    "MAX_SLABS",
    "MIN_ANCHORS_3D",
    "OUTAGE_INDICES",
    "Anchor3D",
    "Estimate3D",
    "FloorAwareTracker",
    "FloorDecision",
    "FloorScores",
    "FloorState",
    "LocalizerResult",
    "MultiFloorLocalizer",
    "Phase",
    "RouteAssist",
    "RouteState",
    "Tracking3D",
    "TransitionState",
    "TransitionWindow",
    "anchor_index",
    "anchor_key",
    "count_slab_hops",
    "decide_floor",
    "floor_anchors",
    "floor_cell_half_gap",
    "floor_scores",
    "generate_measurements_3d",
    "resolve_anchor",
    "route_state",
    "solve_3d",
    "target_z",
    "transition_windows",
    "vertical_speed_limit",
]
