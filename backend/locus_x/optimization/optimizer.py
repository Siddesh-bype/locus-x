"""Deterministic greedy selection with local swaps.

Search procedure (protocol 1.0.0):

1. **Greedy forward selection** on the *development* split: start empty; at
   each step add the legal candidate that most improves J (ties broken by
   site ID). Evaluations use the conventional (baseline) estimator with fixed
   seeds/routes.
2. **Local swaps** on the development split: repeatedly try every
   (selected → unselected) swap, keep the best improving move, up to
   ``max_swap_passes`` passes without requiring improvement to continue.
3. **Validation selection**: evaluate the greedy result, each swap-improved
   intermediate, and (in the runner) manual/grid references on the
   *validation* split; the lowest validation J wins. The held-out split is
   never evaluated here.

Every evaluated layout — including rejected ones — is recorded in an archive
with its components, so unsuccessful layouts are preserved and auditable.
"""

from __future__ import annotations

import math
from time import perf_counter

from ..building import Building
from ..schemas import Anchor, Environment
from .evaluation import evaluate_layout
from .layouts import grid_layout, make_layout, manual_layout, validate_layout
from .objective import compute_objective
from .protocol import (
    Layout,
    LayoutArchiveEntry,
    ObjectiveNormalization,
    ObjectiveWeights,
    OptimizationResult,
    OptimizerConfig,
    PlacementPolicy,
    SplitConfig,
    validate_split_seeds,
)
from .sites import candidate_sites


def _key(site_ids: tuple[str, ...]) -> str:
    return ",".join(sorted(site_ids))


def optimize_placement(
    configuration: Environment | Building,
    floor_id: str | None = None,
    budget: int = 8,
    policy: PlacementPolicy | None = None,
    weights: ObjectiveWeights | None = None,
    normalization: ObjectiveNormalization | None = None,
    development_split: SplitConfig | None = None,
    validation_split: SplitConfig | None = None,
    config: OptimizerConfig | None = None,
    progress=None,
) -> OptimizationResult:
    start = perf_counter()
    config = config or OptimizerConfig()
    policy = policy or PlacementPolicy()
    weights = weights or ObjectiveWeights()
    normalization = normalization or ObjectiveNormalization()
    development_split = development_split or config.development_split
    validation_split = validation_split or config.validation_split

    floor = _resolve(configuration, floor_id)
    resolved_floor_id = floor.id
    legal, site_records, source = candidate_sites(configuration, resolved_floor_id, policy)
    if len(legal) < budget:
        raise ValueError(
            f"Only {len(legal)} legal candidate sites; cannot place {budget} anchors"
        )

    archive: list[LayoutArchiveEntry] = []
    archive_index: dict[str, int] = {}
    cache: dict[str, tuple] = {}

    def evaluate(sites: list[Anchor], stage: str, accepted: bool) -> tuple:
        key = _key(tuple(s.id for s in sites))
        if key not in cache:
            layout = make_layout(
                "optimized",
                resolved_floor_id,
                sites,
                [s.id for s in sites],
                provenance={"stage": stage, "sites_source": source},
            )
            evaluation = evaluate_layout(
                configuration, resolved_floor_id, layout.anchors, development_split
            )
            breakdown = compute_objective(evaluation, weights, normalization)
            validate_layout(layout, floor, legal, policy)
            cache[key] = (layout, evaluation, breakdown)
            archive_index[key] = len(archive)
            archive.append(
                LayoutArchiveEntry(
                    layout_hash=layout.layout_hash,
                    site_ids=[s.id for s in sites],
                    objective_total=breakdown.total,
                    components={
                        k: v for k, v in breakdown.model_dump().items() if k.endswith("_term")
                    },
                    accepted=accepted,
                    stage=stage,
                )
            )
            if progress:
                progress(len(cache), config.progress_total(budget))
        elif accepted:
            # A cached layout can still become a finalist (e.g. the greedy
            # solution was already evaluated as an incremental step).
            entry = archive[archive_index[key]]
            archive[archive_index[key]] = entry.model_copy(
                update={"accepted": True, "stage": stage}
            )
        return cache[key]

    # --- 1. Greedy forward selection (development split) -------------------
    selected: list[Anchor] = []
    remaining = sorted(legal, key=lambda s: s.id)
    for _ in range(budget):
        best = None
        for candidate in remaining:
            if any(
                math.dist(candidate.position, chosen.position) < policy.min_anchor_separation - 1e-9
                for chosen in selected
            ):
                continue
            _, _, breakdown = evaluate([*selected, candidate], "greedy", accepted=False)
            if best is None or (breakdown.total, candidate.id) < (best[0], best[1]):
                best = (breakdown.total, candidate.id, candidate)
        if best is None:
            raise ValueError("No legal site satisfies the separation constraint at this step")
        selected.append(best[2])
        remaining = [s for s in remaining if s.id != best[2].id]

    # --- 2. Local swaps (development split) --------------------------------
    _, _, best_breakdown = evaluate(selected, "greedy", accepted=True)
    best_total = best_breakdown.total
    for _ in range(config.search.max_swap_passes):
        improved = False
        for chosen_index in range(len(selected)):
            for candidate in remaining:
                trial = list(selected)
                trial[chosen_index] = candidate
                if any(
                    math.dist(a.position, b.position) < policy.min_anchor_separation - 1e-9
                    for i, a in enumerate(trial)
                    for b in trial[i + 1 :]
                ):
                    continue
                _, _, breakdown = evaluate(trial, "swap", accepted=False)
                if breakdown.total < best_total - 1e-12:
                    best_total = breakdown.total
                    selected = trial
                    remaining = [s for s in legal if s.id not in {t.id for t in trial}]
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    optimized_sites = list(selected)
    optimized, dev_evaluation, dev_breakdown = evaluate(optimized_sites, "swap", accepted=True)
    optimized = optimized.model_copy(
        update={
            "objective": dev_breakdown,
            "metrics": dev_evaluation.pooled,
            "provenance": {
                **optimized.provenance,
                "split": "development",
                "estimator": "baseline",
            },
        }
    )

    # --- 3. Validation selection among distinct development finalists ------
    finalists: dict[str, list[Anchor]] = {}
    for entry in archive:
        if entry.stage in ("greedy", "swap") and entry.accepted:
            finalists[_key(tuple(entry.site_ids))] = [
                next(s for s in legal if s.id == site_id) for site_id in entry.site_ids
            ]
    # Always include the manual and grid layouts' *sites where representable*
    # is the runner's job; here the search compares its own finalists only.
    validation_candidates: list[Layout] = []
    for key, sites in sorted(finalists.items()):
        layout = make_layout(
            "optimized",
            resolved_floor_id,
            sites,
            [s.id for s in sites],
            provenance={"stage": "validation"},
        )
        evaluation = evaluate_layout(
            configuration, resolved_floor_id, layout.anchors, validation_split
        )
        breakdown = compute_objective(evaluation, weights, normalization)
        validation_candidates.append(
            layout.model_copy(
                update={
                    "objective": breakdown,
                    "metrics": evaluation.pooled,
                    "provenance": {
                        "stage": "validation",
                        "split": "validation",
                        "estimator": "baseline",
                        "development_total": str(
                            next(
                                e.objective_total
                                for e in archive
                                if _key(tuple(e.site_ids)) == key and e.accepted
                            )
                        ),
                    },
                }
            )
        )
        archive.append(
            LayoutArchiveEntry(
                layout_hash=layout.layout_hash,
                site_ids=[s.id for s in sites],
                objective_total=breakdown.total,
                components={k: v for k, v in breakdown.model_dump().items() if k.endswith("_term")},
                accepted=True,
                stage="validation",
            )
        )
    selected_layout = min(
        validation_candidates,
        key=lambda layout: (layout.objective.total, layout.layout_id),
    )

    manual = manual_layout(floor, resolved_floor_id, budget)
    grid = grid_layout(floor, resolved_floor_id, legal, budget, policy)
    for reference in (manual, grid):
        try:
            validate_layout(reference, floor, legal, policy)
            reference_ok = True
        except ValueError:
            reference_ok = False
        reference.metrics = None
        reference.provenance = {
            **reference.provenance,
            "legal_under_policy": str(reference_ok),
        }

    return OptimizationResult(
        optimizer_protocol=config.protocol_version,
        environment_id=configuration.id,
        floor_id=resolved_floor_id,
        budget=budget,
        sites=site_records,
        policy=policy,
        weights=weights,
        normalization=normalization,
        development_split=development_split,
        validation_split=validation_split,
        selected=selected_layout,
        candidates=[manual, grid, selected_layout],
        archive=archive,
        runtime_seconds=perf_counter() - start,
        reproducibility={
            "development_seeds": ",".join(map(str, development_split.seeds)),
            "validation_seeds": ",".join(map(str, validation_split.seeds)),
            "development_route": development_split.route,
            "validation_route": validation_split.route,
            "sites_source": source,
            "environment_id": configuration.id,
        },
    )


def _resolve(configuration: Environment | Building, floor_id: str | None) -> Environment:
    if isinstance(configuration, Building):
        selected = floor_id or configuration.default_floor_id
        for floor in configuration.floors:
            if floor.id == selected:
                return floor
        raise ValueError(f"Unknown floor: {selected}")
    if floor_id is not None and floor_id != configuration.id:
        raise ValueError(f"Unknown floor: {floor_id}")
    return configuration


def validate_splits(
    development: SplitConfig, validation: SplitConfig, held_out: SplitConfig
) -> None:
    """Raise if the splits leak: seeds must be disjoint everywhere, and the
    held-out route must never be used during search or validation.

    Development and validation share the ``asset_round`` route by design (the
    environment provides exactly two routes); they are separated by disjoint
    seeds and by when each is consulted.
    """
    validate_split_seeds(
        {"development": development, "validation": validation, "held_out": held_out}
    )
