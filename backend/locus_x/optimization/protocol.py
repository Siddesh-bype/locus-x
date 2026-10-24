"""Data contract for the anchor-placement optimizer.

Everything the optimizer consumes or produces is a strict, JSON-serializable model:
request inputs (budget, policy, splits, weights), search outputs (layouts with real
anchor coordinates and site selections), objective breakdowns, and comparison
summaries. The main dashboard can consume these directly; nothing here imports
the estimator or simulator internals beyond ``schemas``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..schemas import Anchor, Scenario, StrictModel

#: Bumped when the objective, splits, or selection procedure change in a way that
#: can change which layout wins. Recorded in every manifest.
OPTIMIZER_PROTOCOL_VERSION = "1.0.0"


class PlacementPolicy(StrictModel):
    """Legality constraints for candidate mounting positions (metres)."""

    min_wall_clearance: float = Field(default=0.25, ge=0, le=2)
    min_anchor_separation: float = Field(default=4.0, ge=0, le=20)
    margin_from_bounds: float = Field(default=0.25, ge=0, le=5)
    allow_access_cores: bool = False
    allow_outside_movement_regions: bool = False


class SplitConfig(StrictModel):
    """A frozen evaluation split. Splits must be disjoint for validity (checked
    by :func:`backend.locus_x.optimization.optimizer.validate_splits`)."""

    route: str = Field(min_length=1)
    seeds: list[int] = Field(min_length=1, max_length=8)
    scenarios: list[Scenario] = Field(min_length=1, max_length=7)
    steps: int = Field(default=40, ge=4, le=400)
    dt: float = Field(default=0.5, ge=0.1, le=1)

    @model_validator(mode="after")
    def unique_seeds(self):
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("Split seeds must be unique")
        if len(set(self.scenarios)) != len(self.scenarios):
            raise ValueError("Split scenarios must be unique")
        return self


class SearchSettings(StrictModel):
    """Deterministic search controls for greedy selection + local swaps."""

    max_swap_passes: int = Field(default=2, ge=0, le=10)
    archive_limit: int = Field(default=250, ge=10, le=5000)


class ObjectiveWeights(StrictModel):
    """Documented weights for J. Chosen a priori and fixed for protocol 1.0.0.

    Each term is normalized to a comparable scale before weighting; see
    ``objective.py`` and ``docs/anchor-optimizer.md`` for the exact definitions.
    """

    error: float = Field(default=0.35, ge=0)
    tail: float = Field(default=0.20, ge=0)
    unavailable: float = Field(default=0.15, ge=0)
    geometry: float = Field(default=0.15, ge=0)
    resilience: float = Field(default=0.15, ge=0)

    @model_validator(mode="after")
    def nonempty(self):
        if self.error + self.tail + self.unavailable + self.geometry + self.resilience <= 0:
            raise ValueError("At least one objective weight must be positive")
        return self


class ObjectiveNormalization(StrictModel):
    """Fixed reference scales that make the error terms dimensionless."""

    rmse_reference_m: float = Field(default=2.0, gt=0)
    p95_reference_m: float = Field(default=4.0, gt=0)


def validate_split_seeds(splits: dict[str, SplitConfig]) -> None:
    """Leakage guard: seeds must be disjoint across splits, and the held-out
    route must never appear in development or validation.

    The two hospital routes are ``asset_round`` (development/validation) and
    ``held_out`` (final reporting only). Development and validation share a
    route because the environment provides exactly two; they are separated by
    disjoint seeds and by when they are consulted.
    """
    names = list(splits)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            shared = set(splits[first].seeds) & set(splits[second].seeds)
            if shared:
                raise ValueError(f"{first} and {second} splits share seeds: {sorted(shared)}")
    held_out_route = splits["held_out"].route
    for name in ("development", "validation"):
        if splits[name].route == held_out_route:
            raise ValueError(f"{name} split must not use the held-out route '{held_out_route}'")


class OptimizerConfig(StrictModel):
    """Complete optimizer run configuration. Splits are frozen per protocol.

    Development and validation share the ``asset_round`` route by design (the
    hospital has exactly two routes; ``held_out`` is reserved exclusively for
    final reporting). They must use disjoint seeds.
    """

    protocol_version: str = OPTIMIZER_PROTOCOL_VERSION
    # Development split: 20 steps keeps a greedy search over ~60 sites in the
    # tens of minutes; validation and held-out use longer runs for selection
    # and reporting.
    development_split: SplitConfig = Field(
        default_factory=lambda: SplitConfig(
            route="asset_round",
            seeds=[11, 22],
            scenarios=["clean", "nlos", "anchor_failure"],
            steps=20,
            dt=0.5,
        )
    )
    validation_split: SplitConfig = Field(
        default_factory=lambda: SplitConfig(
            route="asset_round",
            seeds=[33, 44],
            scenarios=["clean", "noise", "nlos", "packet_loss", "anchor_failure", "chaos"],
            steps=40,
            dt=0.5,
        )
    )
    held_out_split: SplitConfig = Field(
        default_factory=lambda: SplitConfig(
            route="held_out",
            seeds=[101, 202],
            scenarios=[
                "clean",
                "noise",
                "high_noise",
                "nlos",
                "packet_loss",
                "anchor_failure",
                "chaos",
            ],
            steps=80,
            dt=0.5,
        )
    )
    search: SearchSettings = Field(default_factory=SearchSettings)
    weights: ObjectiveWeights = Field(default_factory=ObjectiveWeights)
    normalization: ObjectiveNormalization = Field(default_factory=ObjectiveNormalization)
    policy: PlacementPolicy = Field(default_factory=PlacementPolicy)

    def progress_total(self, budget: int) -> int:
        """Conservative upper bound on evaluated layouts for progress reporting."""
        return budget * (budget + 4)

    @model_validator(mode="after")
    def splits_are_seeded_disjointly(self):
        validate_split_seeds(
            {
                "development": self.development_split,
                "validation": self.validation_split,
                "held_out": self.held_out_split,
            }
        )
        return self


class ObjectiveBreakdown(StrictModel):
    """Every component of J is reported next to the total. No hidden trade-offs."""

    total: float
    error_term: float
    tail_term: float
    unavailable_term: float
    geometry_term: float
    resilience_term: float
    raw: dict[str, float]

    @model_validator(mode="after")
    def consistent_total(self):
        expected = (
            self.error_term
            + self.tail_term
            + self.unavailable_term
            + self.geometry_term
            + self.resilience_term
        )
        if abs(expected - self.total) > 1e-9:
            raise ValueError("Total objective must equal the sum of its weighted terms")
        return self


class LayoutMetrics(StrictModel):
    """Aggregate localization metrics for one layout under one split."""

    rmse: float | None
    median: float | None
    p95: float | None
    valid_rate: float
    update_rate: float
    nlos_rmse: float | None
    outage_rmse: float | None
    frames: int
    valid_frames: int
    per_region: dict[str, dict[str, float | int | None]]
    observability: float
    poor_geometry_fraction: float
    mean_condition: float | None


class Layout(StrictModel):
    """A complete, loadable anchor layout for one floor."""

    layout_id: str = Field(min_length=1)
    kind: Literal["manual", "grid", "optimized"]
    floor_id: str = Field(min_length=1)
    budget: int = Field(ge=1, le=64)
    anchors: list[Anchor] = Field(min_length=1)
    site_ids: list[str] = Field(min_length=1)
    selection: dict[str, str]
    layout_hash: str = Field(min_length=8)
    objective: ObjectiveBreakdown | None = None
    metrics: LayoutMetrics | None = None
    provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self):
        if len(self.anchors) != self.budget:
            raise ValueError("Layout must contain exactly `budget` anchors")
        if len(self.site_ids) != self.budget:
            raise ValueError("Layout must name one site per anchor")
        if len({a.id for a in self.anchors}) != self.budget:
            raise ValueError("Installed anchor IDs must be unique within a layout")
        if set(self.selection) != {a.id for a in self.anchors}:
            raise ValueError("Selection must map every installed anchor ID to a site ID")
        return self


class LayoutArchiveEntry(StrictModel):
    """Every evaluated layout is preserved, including rejected ones."""

    layout_hash: str
    site_ids: list[str]
    objective_total: float | None
    components: dict[str, float]
    accepted: bool
    stage: Literal["greedy", "swap", "validation"]


class OptimizationResult(StrictModel):
    optimizer_protocol: str
    environment_id: str
    floor_id: str
    budget: int
    sites: list[dict]
    policy: PlacementPolicy
    weights: ObjectiveWeights
    normalization: ObjectiveNormalization
    development_split: SplitConfig
    validation_split: SplitConfig
    selected: Layout
    candidates: list[Layout]
    archive: list[LayoutArchiveEntry]
    runtime_seconds: float
    reproducibility: dict[str, str]


class ComparisonRow(StrictModel):
    budget: int
    layout: Literal["manual", "grid", "optimized"]
    layout_hash: str
    estimator: Literal["baseline", "locus"]
    scenario: str
    seeds: str
    rmse: float | None
    median: float | None
    p95: float | None
    valid_rate: float
    update_rate: float
    nlos_rmse: float | None
    outage_rmse: float | None
    frames: int
    per_region_rmse: dict[str, float | None]


class ComparisonReport(StrictModel):
    environment_id: str
    floor_id: str
    estimator_fixed_per_table: bool
    budgets: list[int]
    seeds: list[int]
    scenarios: list[Scenario]
    steps: int
    rows: list[ComparisonRow]
    notes: str
