"""Building-coordinate projection for the multi-floor positioning work.

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

__all__ = [
    "DEFAULT_MOUNT_HEIGHT",
    "Anchor3D",
    "anchor_index",
    "floor_anchors",
    "resolve_anchor",
    "target_z",
]
