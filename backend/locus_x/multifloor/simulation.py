"""Three-dimensional radio ranges for the multi-floor pipeline.

This is the 3D counterpart of :mod:`locus_x.simulation`; the legacy 2D generator is
untouched and stays the only producer for the V1 ``Environment`` contract. Everything
here is additive and consumes validated configuration plus one
:class:`~locus_x.multifloor.route.RouteState`.

Range model
-----------
``true_range`` is the Euclidean distance between the target device at
``(state.x, state.y, state.z)`` and the anchor's configured 3D position. The anchor
position is always looked up from the building configuration
(:func:`~locus_x.multifloor.coordinates.resolve_anchor`) and never read from an
observation, so ``Measurement.anchor_position`` keeps carrying the floor-local 2D
projection the legacy estimator expects. Elevations differ between floors and even on
one floor (device 1.2 m vs anchor 2.6 m), so the vertical term is never dropped: a
target standing directly under an anchor is 1.4 m away, not 0 m.

Obstruction
-----------
*Same floor* (``state.floor_id == anchor.floor_id``): walls live on one floor, so the
legacy rule is reused verbatim — ``bias = nlos_bias * min(walls, 3) / 2``, gated by
``Settings.walls_enabled``, with the crossed wall IDs reported in
``intersected_walls``.

*Any floor*: slabs attenuate. ``slab_hops`` is the number of floor datums **strictly**
between the target's ``z`` and the anchor's ``z`` (a target resting exactly on a datum
adds nothing) and the added bias is
``building.slab_bias_per_slab * min(slab_hops, MAX_SLABS)``. This is deliberately
integer: during a vertical traverse the continuous ``z`` crosses each datum once, so
the attenuation steps rather than ramps, and the reported range is non-monotonic —
it is minimal when the target passes the anchor's own elevation. Slab attenuation is
geometry, not a wall, so it applies even with ``walls_enabled = False``.

A same-floor pair therefore has ``slab_hops == 0`` and only the wall term; a
cross-floor pair has no wall term (a wall cannot block a path through a slab) and only
the slab term.

Fault labels
------------
:class:`~locus_x.schemas.FaultLabel` is unchanged, so legacy frames serialise
identically. ``kind`` becomes ``"nlos"`` whenever either bias term is positive,
including pure slab attenuation; ``injected_bias`` is the sum of both terms while
``intersected_walls`` only ever holds same-floor hits. The slab hop count is returned
separately as ``slab_hops`` (raw, uncapped) so callers can see the true geometry even
where the attenuation saturated.

Draw order and determinism
--------------------------
The per-anchor stream is ``SeedSequence([settings.seed, frame_index, i])`` and the draw
order mirrors the legacy generator exactly: classify ``kind`` (which consumes
``rng.random()`` for the dropout test, and only when neither disabling nor the timed
outage already decided the outcome), then draw ``rng.normal()`` for the noise
**unconditionally**, including for unavailable anchors. Reordering the draws would
silently change every observation, so it is load-bearing.

``i`` is the anchor's position in the flat list used for this call: by default
``floor_anchors(building)`` — floors in declared order, each floor's anchors in
declaration order — and a caller-supplied ``anchors`` sequence in its own order.
Disabling never removes an anchor from that list, so one anchor's stream never shifts
because another was disabled.

Timed outage
------------
The legacy scenario-gated outage (``anchor_failure``/``chaos``, ``12 <= timestamp % 40
< 22``) fires for flat indices ``1, 3, 5, 7``. On a 24-anchor building those are all
ground-floor anchors, so the physical intent — the four perimeter anchors fail — is
applied here per floor: indices ``1, 3, 5, 7`` *within each floor's declared anchor
list*. The legacy generator keeps its flat-index rule.

Disabling
---------
``Settings.disabled_anchors`` keeps the legacy meaning "this anchor ID on every floor".
``Settings.disabled_anchor_keys`` holds building-wide ``"<floor_id>:<anchor_id>"`` keys
for one specific anchor. Unknown or malformed keys are rejected here, at the point of
use, rather than silently ignored.
"""

from collections.abc import Sequence
from math import dist

import numpy as np

from ..building import Building
from ..geometry import intersects
from ..schemas import FaultLabel, Measurement, Settings
from .coordinates import Anchor3D, anchor_key, floor_anchors, resolve_anchor
from .route import RouteState

MAX_SLABS = 3
"""Slab attenuation stops growing after this many intervening datums."""

OUTAGE_INDICES: tuple[int, ...] = (1, 3, 5, 7)
"""Anchor positions within one floor's list that fail during the timed outage."""

OUTAGE_SCENARIOS: tuple[str, ...] = ("anchor_failure", "chaos")
OUTAGE_WINDOW = (12.0, 22.0)
OUTAGE_PERIOD = 40.0


def count_slab_hops(target_z: float, anchor_z: float, datums: Sequence[float]) -> int:
    """Floor datums strictly between two elevations, order-insensitive.

    Strict on both sides: a target resting exactly on a datum crosses no slab, which is
    what makes the traverse's slab count step instead of jumping at a boundary.
    """
    low, high = sorted((target_z, anchor_z))
    return sum(1 for datum in datums if low < datum < high)


def _disabled_keys(building: Building, keys: Sequence[str]) -> set[str]:
    """Validate floor-scoped anchor keys and return them as a set."""
    for key in keys:
        floor_id, separator, anchor_id = key.partition(":")
        if not separator or not floor_id or not anchor_id:
            raise ValueError(
                f"Malformed disabled anchor key {key!r}; expected '<floor_id>:<anchor_id>'"
            )
        resolve_anchor(building, floor_id, anchor_id)
    return set(keys)


def generate_measurements_3d(
    building: Building,
    settings: Settings,
    state: RouteState,
    frame_index: int,
    *,
    anchors: Sequence[Anchor3D] | None = None,
) -> tuple[list[Measurement], list[FaultLabel], dict[str, int]]:
    """Observe every anchor from one 3D target state.

    Returns the available ``measurements`` in anchor order, one ``FaultLabel`` per
    anchor (available or not), and ``slab_hops`` keyed ``"<floor_id>:<anchor_id>"`` with
    the raw count of intervening floor datums per anchor.

    ``anchors`` overrides the default ``floor_anchors(building)`` sequence; the seeding
    index is then the position in that sequence, so a caller must pass the same sequence
    on every frame of a run to keep the draws stable.
    """
    installed = list(floor_anchors(building)) if anchors is None else list(anchors)
    floors = {floor.id: floor for floor in building.floors}
    datums = [floor.elevation for floor in building.floors]
    anchor_positions = {
        anchor_key(floor.id, anchor.id): position
        for floor in building.floors
        for position, anchor in enumerate(floor.anchors)
    }
    disabled_ids = set(settings.disabled_anchors)
    disabled_keys = _disabled_keys(building, settings.disabled_anchor_keys)
    outage = (
        settings.scenario in OUTAGE_SCENARIOS
        and OUTAGE_WINDOW[0] <= state.timestamp % OUTAGE_PERIOD < OUTAGE_WINDOW[1]
    )

    observations: list[Measurement] = []
    labels: list[FaultLabel] = []
    slab_hops: dict[str, int] = {}
    seen: set[str] = set()
    for i, anchor in enumerate(installed):
        key = anchor_key(anchor.floor_id, anchor.anchor_id)
        if key in seen:
            raise ValueError(f"Duplicate anchor key {key!r} in the 3D measurement list")
        seen.add(key)
        floor = floors.get(anchor.floor_id)
        if floor is None:
            known = ", ".join(candidate.id for candidate in building.floors)
            raise ValueError(
                f"Unknown floor {anchor.floor_id!r} for anchor {anchor.anchor_id!r}; "
                f"known floors are {known}"
            )
        # Per-anchor streams keep surviving observations identical when an anchor is disabled.
        rng = np.random.default_rng(np.random.SeedSequence([settings.seed, frame_index, i]))
        true_range = dist((state.x, state.y, state.z), (anchor.x, anchor.y, anchor.z))
        same_floor = state.floor_id == anchor.floor_id
        walls = (
            [
                wall.id
                for wall in floor.walls
                if intersects((state.x, state.y), (anchor.x, anchor.y), wall.a, wall.b)
            ]
            if same_floor
            else []
        )
        bias = settings.nlos_bias * min(len(walls), 3) / 2 if settings.walls_enabled else 0.0
        hops = count_slab_hops(state.z, anchor.z, datums)
        bias += building.slab_bias_per_slab * min(hops, MAX_SLABS)
        kind = "nlos" if bias > 0 else "los"
        if anchor.anchor_id in disabled_ids or key in disabled_keys:
            kind = "disabled"
        elif outage and anchor_positions.get(key) in OUTAGE_INDICES:
            kind = "outage"
        elif rng.random() < settings.dropout:
            kind = "packet_loss"
        available = kind in ("los", "nlos")
        noise = float(rng.normal(0, settings.noise_std))
        if available:
            observations.append(
                Measurement(
                    anchor_id=anchor.anchor_id,
                    anchor_position=(anchor.x, anchor.y),
                    timestamp=state.timestamp,
                    observed_range=max(0.01, true_range + bias + noise),
                )
            )
        labels.append(
            FaultLabel(
                anchor_id=anchor.anchor_id,
                available=available,
                kind=kind,
                intersected_walls=walls if settings.walls_enabled else [],
                true_range=true_range,
                injected_bias=bias,
            )
        )
        slab_hops[key] = hops
    return observations, labels, slab_hops
