"""Layout construction and environment-configuration output.

Produces manual (installed), regular-grid, and optimized layouts at a fixed
anchor budget, and builds *loadable* environment configurations by replacing a
floor's installed anchors inside a copy of the existing configuration. The
catalog configuration is never mutated; output IDs are new so nothing is
silently replaced.
"""

from __future__ import annotations

import hashlib
import json
import math

from ..building import Building
from ..schemas import Anchor, Environment
from .protocol import Layout
from .sites import wall_clearance


def layout_hash(kind: str, floor_id: str, positions: list[tuple]) -> str:
    payload = json.dumps(
        {"kind": kind, "floor": floor_id, "positions": [list(map(float, p)) for p in positions]},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def installed_ids(budget: int) -> list[str]:
    """Stable installed-anchor IDs A1..AK for a K-anchor layout."""
    return [f"A{i}" for i in range(1, budget + 1)]


def make_layout(
    kind: str,
    floor_id: str,
    sites: list[Anchor],
    site_ids: list[str],
    provenance: dict | None = None,
) -> Layout:
    anchors = [
        Anchor(id=installed_id, position=site.position)
        for installed_id, site in zip(installed_ids(len(sites)), sites, strict=True)
    ]
    digest = layout_hash(kind, floor_id, [a.position for a in anchors])
    return Layout(
        layout_id=f"{kind}-{floor_id}-{digest[:8]}",
        kind=kind,
        floor_id=floor_id,
        budget=len(sites),
        anchors=anchors,
        site_ids=site_ids,
        selection={anchor.id: site for anchor, site in zip(anchors, site_ids, strict=True)},
        layout_hash=digest,
        provenance=provenance or {},
    )


def manual_layout(environment: Environment, floor_id: str, budget: int) -> Layout:
    """The currently installed layout at the requested budget.

    Uses the benchmark protocol's named subsets (``ANCHOR_SELECTION``) when the
    budget is one of the reference counts, so "manual" means the same anchor
    subsets the existing benchmark reports; otherwise the first ``budget``
    installed anchors in declared order. Never invents positions.
    """
    installed = environment.anchors
    if budget > len(installed):
        raise ValueError(
            f"Manual layout has {len(installed)} installed anchors; "
            f"cannot represent budget {budget}"
        )
    from ..benchmark import ANCHOR_SELECTION

    named = ANCHOR_SELECTION.get(budget)
    installed_by_id = {anchor.id: anchor for anchor in installed}
    if named and all(anchor_id in installed_by_id for anchor_id in named):
        chosen = [installed_by_id[anchor_id] for anchor_id in named]
    else:
        chosen = installed[:budget]
    sites = [Anchor(id=f"M{i + 1:02d}", position=a.position) for i, a in enumerate(chosen)]
    return make_layout(
        "manual",
        floor_id,
        sites,
        [s.id for s in sites],
        provenance={"source": "installed anchors, benchmark subset where defined"},
    )


def grid_layout(
    environment: Environment,
    floor_id: str,
    candidates: list[Anchor],
    budget: int,
    policy=None,
) -> Layout:
    """Regular-grid placement: cell centers over the floor, snapped to the
    nearest legal candidate site (ties broken by site ID). Deterministic and
    geometry-only; ignores walls and routes by design, which is exactly the
    baseline the optimizer is compared against.

    When ``policy`` is given, snapped sites must also respect its minimum
    anchor separation so the resulting layout stays valid under the frozen
    protocol (the baseline is still geometry-only for placement, but it is
    never allowed to emit an illegal layout)."""
    aspect = environment.width / environment.height
    cols = max(1, round(math.sqrt(budget * aspect)))
    rows = max(1, math.ceil(budget / cols))
    while cols * rows < budget:
        cols += 1
    points = []
    for row in range(rows):
        for col in range(cols):
            if len(points) >= budget:
                break
            x = environment.width * (col + 1) / (cols + 1)
            y = environment.height * (row + 1) / (rows + 1)
            points.append((x, y))
    min_sep = getattr(policy, "min_anchor_separation", 0.0)
    chosen: list[Anchor] = []
    used: set[str] = set()
    for point in points:
        remaining = [
            c
            for c in candidates
            if c.id not in used
            and all(math.dist(c.position, s.position) >= min_sep - 1e-9 for s in chosen)
        ]
        if not remaining:
            # No site fits without violating separation: skip this cell rather
            # than emit an illegal layout (budget may then come up short only
            # if the site set is degenerate, which validate_layout reports).
            continue
        nearest = min(
            remaining,
            key=lambda c: (math.dist(point, c.position), c.id),
        )
        chosen.append(nearest)
        used.add(nearest.id)
    return make_layout(
        "grid",
        floor_id,
        chosen,
        [c.id for c in chosen],
        provenance={"source": f"{cols}x{rows} cell centers snapped to nearest legal site"},
    )


def validate_layout(
    layout: Layout, environment: Environment, candidates: list[Anchor], policy
) -> None:
    """Raise if a layout violates budget, legality, or separation constraints."""
    if len(layout.anchors) != layout.budget:
        raise ValueError("Layout budget mismatch")
    legal_ids = {c.id for c in candidates}
    positions = [a.position for a in layout.anchors]
    for anchor, site_id in zip(layout.anchors, layout.site_ids, strict=True):
        if site_id not in legal_ids:
            raise ValueError(f"Anchor {anchor.id} references illegal site {site_id}")
        site = next(c for c in candidates if c.id == site_id)
        if site.position != anchor.position:
            raise ValueError(f"Anchor {anchor.id} position diverges from site {site_id}")
        if wall_clearance(anchor.position, environment) < policy.min_wall_clearance:
            raise ValueError(f"Anchor {anchor.id} violates wall clearance")
    for i, first in enumerate(positions):
        for second in positions[i + 1 :]:
            if math.dist(first, second) < policy.min_anchor_separation - 1e-9:
                raise ValueError(
                    f"Anchors {first} and {second} closer than {policy.min_anchor_separation} m"
                )


def build_environment_config(
    configuration: Environment | Building,
    floor_id: str,
    layout: Layout,
    environment_suffix: str | None = None,
) -> dict:
    """Build a full, loadable environment configuration with ``layout`` installed.

    For V3 buildings only the target floor's ``anchors``/``anchor_layout_id``
    change; all other floors and geometry are preserved byte-for-byte. For V1
    environments the anchor list is replaced in a copy. New environment ID and
    version strings ensure the result never overwrites the source configuration.
    """
    suffix = environment_suffix or f"{layout.kind}-k{layout.budget}-{layout.layout_hash[:8]}"
    raw = json.loads(configuration.model_dump_json())
    if isinstance(configuration, Building):
        floor = next((f for f in configuration.floors if f.id == floor_id), None)
        if floor is None:
            raise ValueError(f"Unknown floor: {floor_id}")
        for floor_raw in raw["floors"]:
            if floor_raw["id"] == floor_id:
                floor_raw["anchors"] = [a.model_dump() for a in layout.anchors]
                floor_raw["anchor_layout_id"] = layout.layout_id
        raw["id"] = f"{configuration.id}-{suffix}"
        raw["name"] = f"{configuration.name} · {layout.kind} layout k={layout.budget}"
        raw["version"] = f"{configuration.version}+{layout.layout_id}"
    else:
        raw["anchors"] = [a.model_dump() for a in layout.anchors]
        raw["id"] = f"{configuration.id}-{suffix}"
        raw["name"] = f"{configuration.name} · {layout.kind} layout k={layout.budget}"
    return raw


def validate_environment_config(raw: dict) -> Environment | Building:
    """Round-trip a generated configuration through the existing parsers."""
    model = Building if raw.get("schema_version") == "3.0" else Environment
    return model.model_validate(raw)
