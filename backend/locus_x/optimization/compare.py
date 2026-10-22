"""Fair comparison of manual, grid and optimized layouts at equal budget.

Every layout is evaluated on the *same* frozen split with the *same* fixed
estimator and the same per-anchor-index noise/fault streams. The estimator is
never changed together with the layout: each report row records which single
estimator produced it, and ``compare_layouts`` runs one estimator at a time.
"""

from __future__ import annotations

from ..building import Building, Floor
from ..schemas import Environment
from .evaluation import build_floor_environment, replay_split
from .layouts import grid_layout, manual_layout, validate_layout
from .protocol import ComparisonReport, ComparisonRow, Layout, PlacementPolicy, SplitConfig
from .sites import candidate_sites


def layout_floor(configuration: Environment | Building, floor_id: str | None) -> str:
    if isinstance(configuration, Building):
        return floor_id or configuration.default_floor_id
    return configuration.id


def regions_of(environment: Environment) -> list:
    if isinstance(environment, Floor):
        return [*environment.rooms, *environment.corridors]
    return list(environment.rooms)


def layout_rows(
    configuration: Environment | Building,
    floor_id: str | None,
    layout: Layout,
    split: SplitConfig,
    estimator: str,
    policy: PlacementPolicy | None = None,
) -> list[ComparisonRow]:
    """Evaluate one layout on one split with one fixed estimator."""
    environment = build_floor_environment(configuration, floor_id, layout.anchors)
    pooled, per_scenario, _ = replay_split(environment, split, estimator, regions_of(environment))
    rows = []
    for scenario, metrics in per_scenario.items():
        rows.append(_row(layout, estimator, scenario, split, metrics))
    rows.append(_row(layout, estimator, "pooled", split, pooled))
    return rows


def _row(layout, estimator, scenario, split, metrics) -> ComparisonRow:
    return ComparisonRow(
        budget=layout.budget,
        layout=layout.kind,
        layout_hash=layout.layout_hash,
        estimator=estimator,
        scenario=scenario,
        seeds=",".join(map(str, split.seeds)),
        rmse=metrics.rmse,
        median=metrics.median,
        p95=metrics.p95,
        valid_rate=metrics.valid_rate,
        update_rate=metrics.update_rate,
        nlos_rmse=metrics.nlos_rmse,
        outage_rmse=metrics.outage_rmse,
        frames=metrics.frames,
        per_region_rmse={name: values["rmse"] for name, values in metrics.per_region.items()},
    )


def compare_layouts(
    configuration: Environment | Building,
    floor_id: str | None,
    layouts: list[Layout],
    split: SplitConfig,
    estimator: str,
    policy: PlacementPolicy | None = None,
) -> ComparisonReport:
    """Compare layouts at identical budget/split/estimator. One estimator only."""
    if estimator not in ("baseline", "locus"):
        raise ValueError("estimator must be 'baseline' or 'locus'")
    budgets = {layout.budget for layout in layouts}
    if len(budgets) != 1:
        raise ValueError("All compared layouts must share one anchor budget")
    rows: list[ComparisonRow] = []
    for layout in layouts:
        rows.extend(layout_rows(configuration, floor_id, layout, split, estimator, policy))
    return ComparisonReport(
        environment_id=configuration.id,
        floor_id=layout_floor(configuration, floor_id),
        estimator_fixed_per_table=True,
        budgets=sorted(budgets),
        seeds=list(split.seeds),
        scenarios=list(split.scenarios),
        steps=split.steps,
        rows=rows,
        notes=(
            "Same split, same seeds, same per-anchor-index noise/fault streams across "
            "layouts; only anchor geometry differs. Estimator is fixed per report. "
            "Runtime excluded. Frames within a run are correlated."
        ),
    )


def reference_layouts(
    configuration: Environment | Building,
    floor_id: str | None,
    budget: int,
    optimized: Layout,
    policy: PlacementPolicy | None = None,
) -> list[Layout]:
    """Manual, grid and optimized layouts at the same budget.

    Manual anchors are the installed positions (a status-quo reference, so they
    are reported even when they violate the optimizer's policy); grid and
    optimized layouts must satisfy every constraint.
    """
    policy = policy or PlacementPolicy()
    resolved = layout_floor(configuration, floor_id)
    installed = _installed(configuration, floor_id)
    floor_env = build_floor_environment(configuration, floor_id, installed)
    legal, _, _ = candidate_sites(configuration, resolved, policy)
    manual = manual_layout(floor_env, resolved, budget)
    grid = grid_layout(floor_env, resolved, legal, budget, policy)
    validate_layout(grid, floor_env, legal, policy)
    validate_layout(optimized, floor_env, legal, policy)
    return [manual, grid, optimized]


def _installed(configuration: Environment | Building, floor_id: str | None):
    """The anchors currently installed on the target floor."""
    if isinstance(configuration, Building):
        selected = floor_id or configuration.default_floor_id
        floor = next((f for f in configuration.floors if f.id == selected), None)
        if floor is None:
            raise ValueError(f"Unknown floor: {selected}")
        return floor.anchors
    return configuration.anchors
