"""Environment-aware anchor placement optimizer (LOCUS-X).

Public surface for the main dashboard and CLI:

* :func:`optimize_placement` — greedy + swap search returning a versioned
  :class:`OptimizationResult` with candidate sites, selected layout, objective
  components and reproducibility metadata.
* :func:`compare_layouts` / :func:`reference_layouts` — fair manual/grid/
  optimized comparison at equal budget with a fixed estimator.
* :func:`build_environment_config` — loadable environment configuration with a
  layout installed (round-trips through the existing parsers).
* :func:`run_optimization` — full deterministic runner (search → validation →
  held-out comparison → artifacts).
"""

from .compare import compare_layouts, layout_floor, reference_layouts
from .evaluation import build_floor_environment, evaluate_layout, replay_split
from .layouts import (
    build_environment_config,
    grid_layout,
    make_layout,
    manual_layout,
    validate_environment_config,
    validate_layout,
)
from .objective import compute_objective
from .optimizer import optimize_placement, validate_splits
from .protocol import (
    OPTIMIZER_PROTOCOL_VERSION,
    ComparisonReport,
    ComparisonRow,
    Layout,
    LayoutMetrics,
    ObjectiveBreakdown,
    ObjectiveNormalization,
    ObjectiveWeights,
    OptimizationResult,
    OptimizerConfig,
    PlacementPolicy,
    SearchSettings,
    SplitConfig,
)
from .runner import run_optimization, write_artifacts
from .sites import candidate_sites, derive_wall_sites, site_legality

__all__ = [
    "OPTIMIZER_PROTOCOL_VERSION",
    "ComparisonReport",
    "ComparisonRow",
    "Layout",
    "LayoutMetrics",
    "ObjectiveBreakdown",
    "ObjectiveNormalization",
    "ObjectiveWeights",
    "OptimizerConfig",
    "OptimizationResult",
    "PlacementPolicy",
    "SearchSettings",
    "SplitConfig",
    "build_environment_config",
    "build_floor_environment",
    "candidate_sites",
    "compare_layouts",
    "compute_objective",
    "derive_wall_sites",
    "evaluate_layout",
    "grid_layout",
    "layout_floor",
    "make_layout",
    "manual_layout",
    "optimize_placement",
    "reference_layouts",
    "replay_split",
    "run_optimization",
    "site_legality",
    "validate_environment_config",
    "validate_layout",
    "validate_splits",
    "write_artifacts",
]
