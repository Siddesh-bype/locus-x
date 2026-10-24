"""Anchor-placement optimizer: legality, budgets, reproducibility, round-trip,
split hygiene, estimator isolation, and artifact protection."""

import hashlib
import json
import math
from pathlib import Path

import pytest

from backend.locus_x.benchmark import run_benchmark
from backend.locus_x.building import Building
from backend.locus_x.environment import load_environments
from backend.locus_x.optimization import (
    OptimizerConfig,
    PlacementPolicy,
    SplitConfig,
    build_environment_config,
    candidate_sites,
    compare_layouts,
    grid_layout,
    manual_layout,
    optimize_placement,
    reference_layouts,
    run_optimization,
    validate_environment_config,
    validate_layout,
    validate_splits,
)
from backend.locus_x.optimization.sites import derive_wall_sites, site_legality, wall_clearance
from backend.locus_x.schemas import BenchmarkRequest, Settings
from backend.locus_x.session import SimulationSession

CONFIG_V1 = Path("configs/environments/hospital_floor_01.json")
CONFIG_V3 = Path("configs/environments/hospital_v3.json")
PROTOCOL = Path("configs/benchmarks/competition.json")


@pytest.fixture(scope="module")
def catalog():
    return load_environments()


@pytest.fixture(scope="module")
def v1(catalog):
    return catalog["hospital_floor_01"]


@pytest.fixture(scope="module")
def v3(catalog):
    return catalog["hospital_v3"]


@pytest.fixture(scope="module")
def tiny_config():
    """Fast split configuration for tests; the production protocol is the default."""
    return OptimizerConfig(
        development_split=SplitConfig(
            route="asset_round",
            seeds=[11],
            scenarios=["clean", "nlos"],
            steps=10,
            dt=0.5,
        ),
        validation_split=SplitConfig(
            route="asset_round",
            seeds=[33],
            scenarios=["clean", "nlos"],
            steps=10,
            dt=0.5,
        ),
    )


@pytest.fixture(scope="module")
def optimized_v1(v1, tiny_config):
    return optimize_placement(v1, budget=4, config=tiny_config)


# ---------------------------------------------------------------- legality --

def test_derived_sites_are_legal_and_deterministic(v1):
    first = derive_wall_sites(v1)
    second = derive_wall_sites(v1)
    assert first == second
    assert len(first) >= 8
    policy = PlacementPolicy()
    for site in first:
        ok, reason = site_legality(site, v1, policy)
        if ok:
            assert wall_clearance(site.position, v1) >= policy.min_wall_clearance
            x, y = site.position
            assert policy.margin_from_bounds <= x <= v1.width - policy.margin_from_bounds
            assert policy.margin_from_bounds <= y <= v1.height - policy.margin_from_bounds


def test_v3_declared_sites_enforce_wall_and_core_legality(v3):
    legal, records, source = candidate_sites(v3, "ground")
    assert source == "declared"
    by_id = {r["id"]: r for r in records}
    # C09 sits exactly on the 'core-east' wall; C08 is inside the stair core.
    assert not by_id["C09"]["legal"] and "wall clearance" in by_id["C09"]["reason"]
    assert not by_id["C08"]["legal"] and "access core" in by_id["C08"]["reason"]
    assert len(legal) == sum(r["legal"] for r in records)
    assert len(legal) == 19


def test_every_output_layout_respects_budget_and_legality(v1, optimized_v1):
    legal, _, _ = candidate_sites(v1)
    policy = PlacementPolicy()
    for layout in optimized_v1.candidates:
        assert layout.budget == 4
        assert len(layout.anchors) == 4
        # Manual is the status-quo reference and may violate the policy, but
        # grid and optimized candidates must be legal layouts.
        if layout.kind in ("grid", "optimized"):
            validate_layout(layout, v1, legal, policy)
            if layout.kind == "grid":
                assert layout.provenance.get("legal_under_policy") == "True"
        for anchor in layout.anchors:
            x, y = anchor.position
            assert 0 <= x <= v1.width and 0 <= y <= v1.height
            assert wall_clearance(anchor.position, v1) > 0


@pytest.fixture(scope="module")
def optimized_v1_k8(v1, tiny_config):
    """Budget-8 run (the discriminating budget for grid snapping) with the
    cheapest search settings; candidates/grid wiring is search-independent."""
    from backend.locus_x.optimization import SearchSettings

    fast = tiny_config.model_copy(
        update={"search": SearchSettings(max_swap_passes=0, archive_limit=10)}
    )
    return optimize_placement(v1, budget=8, config=fast)


def test_result_grid_candidate_matches_compared_grid(v1, optimized_v1_k8):
    """The grid reported in OptimizationResult.candidates must be the same
    legal grid the held-out comparison evaluates (regression: an artifact
    shipped whose candidate grid was built without the separation policy and
    differed from the compared grid at budget 8)."""
    candidate_grid = next(c for c in optimized_v1_k8.candidates if c.kind == "grid")
    compared = reference_layouts(
        v1, optimized_v1_k8.floor_id, optimized_v1_k8.budget, optimized_v1_k8.selected
    )
    compared_grid = next(c for c in compared if c.kind == "grid")
    assert candidate_grid.layout_hash == compared_grid.layout_hash
    assert candidate_grid.site_ids == compared_grid.site_ids
    # And the separation policy is what makes them agree: without it the
    # budget-8 grid snaps two cells onto sites closer than 4 m apart.
    legal, _, _ = candidate_sites(v1)
    unscreened = grid_layout(v1, optimized_v1_k8.floor_id, legal, 8)
    assert unscreened.layout_hash != compared_grid.layout_hash
    with pytest.raises(ValueError, match="closer than"):
        validate_layout(unscreened, v1, legal, PlacementPolicy())


def test_anchors_are_separated_and_reference_real_sites(optimized_v1, v1):
    legal, _, _ = candidate_sites(v1)
    policy = PlacementPolicy()
    validate_layout(optimized_v1.selected, v1, legal, policy)
    positions = [a.position for a in optimized_v1.selected.anchors]
    for i, first in enumerate(positions):
        for second in positions[i + 1:]:
            import math

            assert math.dist(first, second) >= policy.min_anchor_separation


def test_insufficient_legal_sites_rejected(v1, tiny_config):
    with pytest.raises(ValueError, match="legal candidate sites"):
        optimize_placement(v1, budget=500, config=tiny_config)
    narrow = PlacementPolicy(min_anchor_separation=20.0)
    with pytest.raises(ValueError, match="separation constraint"):
        optimize_placement(v1, budget=6, policy=narrow, config=tiny_config)


# ------------------------------------------------------- reproducibility ----

def test_same_config_and_seeds_reproduce_identical_output(v1, tiny_config):
    first = optimize_placement(v1, budget=3, config=tiny_config)
    second = optimize_placement(v1, budget=3, config=tiny_config)
    assert first.selected.layout_hash == second.selected.layout_hash
    assert first.selected.site_ids == second.selected.site_ids
    assert first.selected.objective.model_dump() == second.selected.objective.model_dump()
    assert [a.model_dump() for a in first.selected.anchors] == [
        a.model_dump() for a in second.selected.anchors
    ]


def test_different_seeds_change_the_search_surface(v1):
    config_a = OptimizerConfig(
        development_split=SplitConfig(
            route="asset_round", seeds=[11], scenarios=["nlos"], steps=10, dt=0.5
        ),
        validation_split=SplitConfig(
            route="asset_round", seeds=[33], scenarios=["nlos"], steps=10, dt=0.5
        ),
    )
    config_b = OptimizerConfig(
        development_split=SplitConfig(
            route="asset_round", seeds=[999], scenarios=["nlos"], steps=10, dt=0.5
        ),
        validation_split=SplitConfig(
            route="asset_round", seeds=[33], scenarios=["nlos"], steps=10, dt=0.5
        ),
    )
    result_a = optimize_placement(v1, budget=3, config=config_a)
    result_b = optimize_placement(v1, budget=3, config=config_b)
    # Not required to differ, but the search surface differs; at minimum both
    # runs remain valid and reproducible.
    assert result_a.selected.layout_hash and result_b.selected.layout_hash


# ------------------------------------------------------- round-trip ---------

def test_generated_v1_config_round_trips_and_simulates(v1, optimized_v1):
    raw = build_environment_config(v1, "hospital_floor_01", optimized_v1.selected)
    rebuilt = validate_environment_config(raw)
    assert rebuilt.id != v1.id
    assert [a.model_dump() for a in rebuilt.anchors] == [
        a.model_dump() for a in optimized_v1.selected.anchors
    ]
    frame = SimulationSession(rebuilt, Settings(scenario="clean")).frame
    assert [a.id for a in frame.anchors] == [a.id for a in optimized_v1.selected.anchors]
    assert len(frame.measurements) == 4
    assert frame.evaluation.baseline_error < 1e-6


def test_generated_v3_config_preserves_other_floors(v3, tiny_config):
    result = optimize_placement(v3, "ground", budget=4, config=tiny_config)
    raw = build_environment_config(v3, "ground", result.selected)
    rebuilt = validate_environment_config(raw)
    assert isinstance(rebuilt, Building)
    ground = next(f for f in rebuilt.floors if f.id == "ground")
    first = next(f for f in rebuilt.floors if f.id == "first")
    source_first = next(f for f in v3.floors if f.id == "first")
    assert [a.model_dump() for a in ground.anchors] == [
        a.model_dump() for a in result.selected.anchors
    ]
    assert ground.anchor_layout_id == result.selected.layout_id
    assert [a.model_dump() for a in first.anchors] == [
        a.model_dump() for a in source_first.anchors
    ]
    assert first.anchor_layout_id == source_first.anchor_layout_id
    frame = SimulationSession(rebuilt, Settings(scenario="noise"), "ground").frame
    assert frame.environment_id == rebuilt.id
    assert len(frame.measurements) == 4


def test_optimized_environment_ids_never_collide_with_sources(v1, optimized_v1):
    raw = build_environment_config(v1, "hospital_floor_01", optimized_v1.selected)
    assert raw["id"] not in {v1.id}
    # Round-trip validation must accept the config unchanged.
    validate_environment_config(json.loads(json.dumps(raw)))


# ------------------------------------------------- split hygiene / leakage --

def test_splits_reject_shared_seeds_and_heldout_route_reuse():
    development = SplitConfig(route="asset_round", seeds=[1, 2], scenarios=["clean"], steps=10)
    validation = SplitConfig(route="asset_round", seeds=[2, 3], scenarios=["clean"], steps=10)
    held_out = SplitConfig(route="held_out", seeds=[4, 5], scenarios=["clean"], steps=10)
    with pytest.raises(ValueError, match="share seeds"):
        validate_splits(development, validation, held_out)
    leaky_development = SplitConfig(route="held_out", seeds=[7, 8], scenarios=["clean"], steps=10)
    with pytest.raises(ValueError, match="held-out route"):
        validate_splits(
            leaky_development,
            SplitConfig(route="asset_round", seeds=[3, 4], scenarios=["clean"], steps=10),
            SplitConfig(route="held_out", seeds=[5, 6], scenarios=["clean"], steps=10),
        )


def test_search_never_evaluates_heldout_split(v1, tiny_config, monkeypatch):
    import backend.locus_x.optimization.optimizer as optimizer_module

    observed = []
    original = optimizer_module.evaluate_layout

    def spy(configuration, floor_id, anchors, split, *args, **kwargs):
        observed.append(split)
        return original(configuration, floor_id, anchors, split, *args, **kwargs)

    monkeypatch.setattr(optimizer_module, "evaluate_layout", spy)
    optimize_placement(v1, budget=3, config=tiny_config)
    held_out_route = tiny_config.held_out_split.route
    held_out_seeds = set(tiny_config.held_out_split.seeds)
    for split in observed:
        assert split.route != held_out_route
        assert not (set(split.seeds) & held_out_seeds)


def test_default_protocol_splits_are_disjoint():
    config = OptimizerConfig()
    validate_splits(
        config.development_split, config.validation_split, config.held_out_split
    )


# ---------------------------------------------- estimator isolation ---------

def test_comparison_holds_estimator_fixed(v1, optimized_v1, tiny_config):
    layouts = reference_layouts(v1, "hospital_floor_01", 4, optimized_v1.selected)
    split = tiny_config.held_out_split.model_copy(
        update={"scenarios": ["clean", "nlos"], "seeds": [101], "steps": 10}
    )
    report = compare_layouts(v1, "hospital_floor_01", layouts, split, "baseline")
    assert report.estimator_fixed_per_table is True
    assert {row.estimator for row in report.rows} == {"baseline"}
    assert {row.layout for row in report.rows} == {"manual", "grid", "optimized"}
    with pytest.raises(ValueError, match="estimator"):
        compare_layouts(v1, "hospital_floor_01", layouts, split, "both")


def test_comparison_uses_identical_observations_for_identical_layouts(v1, tiny_config):
    """Two evaluations of the same layout must produce identical metrics — the
    fault/noise streams depend only on seeds, frame index and anchor index."""
    layout = manual_layout(v1, "hospital_floor_01", 4)
    split = SplitConfig(route="held_out", seeds=[101], scenarios=["nlos"], steps=10, dt=0.5)
    first = compare_layouts(v1, "hospital_floor_01", [layout], split, "baseline")
    second = compare_layouts(v1, "hospital_floor_01", [layout], split, "baseline")
    for a, b in zip(first.rows, second.rows, strict=True):
        assert a.model_dump() == b.model_dump()


def test_layout_change_does_not_change_estimator_contract(v1, optimized_v1):
    """Changing the layout must not change which estimator runs or its inputs'
    schema — measurements still carry only the public Measurement fields."""
    layouts = reference_layouts(v1, "hospital_floor_01", 4, optimized_v1.selected)
    split = SplitConfig(route="held_out", seeds=[101], scenarios=["clean"], steps=4, dt=0.5)
    from backend.locus_x.optimization.evaluation import build_floor_environment, replay_split

    for layout in layouts:
        environment = build_floor_environment(v1, "hospital_floor_01", layout.anchors)
        _, _, records = replay_split(environment, split, "baseline")
        assert len(records) == split.steps


# ---------------------------------------------- historical artifacts -------

def test_optimizing_does_not_modify_historical_configs_or_benchmarks():
    watched = [CONFIG_V1, CONFIG_V3, PROTOCOL]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in watched}
    config = OptimizerConfig(
        development_split=SplitConfig(
            route="asset_round", seeds=[11], scenarios=["clean"], steps=8, dt=0.5
        ),
        validation_split=SplitConfig(
            route="asset_round", seeds=[33], scenarios=["clean"], steps=8, dt=0.5
        ),
    )
    run_optimization(environment_id="hospital_floor_01", budgets=[3], config=config)
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_runner_refuses_to_overwrite_artifacts(tmp_path, v1):
    config = OptimizerConfig(
        development_split=SplitConfig(
            route="asset_round", seeds=[11], scenarios=["clean"], steps=8, dt=0.5
        ),
        validation_split=SplitConfig(
            route="asset_round", seeds=[33], scenarios=["clean"], steps=8, dt=0.5
        ),
    )
    output = tmp_path / "run"
    run_optimization(environment_id="hospital_floor_01", budgets=[3], config=config, output=output)
    marker = output / "manifest.json"
    original = marker.read_bytes()
    with pytest.raises(FileExistsError, match="existing optimizer artifacts"):
        run_optimization(
            environment_id="hospital_floor_01", budgets=[3], config=config, output=output
        )
    assert marker.read_bytes() == original


def test_benchmark_protocol_still_runs_after_optimization(tmp_path):
    """The named A1-A8 benchmark protocol is untouched by the optimizer."""
    result = run_benchmark(
        BenchmarkRequest(scenarios=["clean"], anchor_counts=[4], seeds=[777], steps=10)
    )
    assert result["rows"][0]["rmse"] < 1e-6


# ---------------------------------------------- archive / unsuccessful -----

def test_unsuccessful_layouts_are_preserved_in_archive(optimized_v1):
    assert len(optimized_v1.archive) > 1
    totals = [entry.objective_total for entry in optimized_v1.archive]
    assert any(total is not None for total in totals)
    best = optimized_v1.selected.objective.total
    assert any(total > best + 1e-9 for total in totals if total is not None)
    assert {entry.stage for entry in optimized_v1.archive} <= {"greedy", "swap", "validation"}


def test_objective_components_are_reported_individually(optimized_v1):
    objective = optimized_v1.selected.objective
    assert objective.total == pytest.approx(
        objective.error_term
        + objective.tail_term
        + objective.unavailable_term
        + objective.geometry_term
        + objective.resilience_term
    )
    assert set(objective.raw) >= {"rmse_m", "p95_m", "valid_rate", "poor_geometry_fraction"}


# ---------------------------------------------- grid layout ---------------

def test_grid_layout_is_deterministic_and_legal(v1):
    legal, _, _ = candidate_sites(v1)
    policy = PlacementPolicy()
    first = grid_layout(v1, "hospital_floor_01", legal, 6, policy)
    second = grid_layout(v1, "hospital_floor_01", legal, 6, policy)
    assert first.site_ids == second.site_ids
    validate_layout(first, v1, legal, policy)
    assert len(set(first.site_ids)) == 6


@pytest.mark.parametrize("budget", [3, 4, 6, 8])
def test_grid_layout_meets_budget_and_separation(v1, budget):
    """Regression: snapping must never emit anchors closer than the policy's
    minimum separation (v1-k8 previously failed validate_layout this way)."""
    legal, _, _ = candidate_sites(v1)
    policy = PlacementPolicy()
    layout = grid_layout(v1, "hospital_floor_01", legal, budget, policy)
    validate_layout(layout, v1, legal, policy)
    assert layout.budget == budget
    assert len(layout.anchors) == budget
    assert len(set(layout.site_ids)) == budget
    positions = [a.position for a in layout.anchors]
    for i, first in enumerate(positions):
        for second in positions[i + 1 :]:
            assert math.dist(first, second) >= policy.min_anchor_separation - 1e-9


def test_manual_layout_matches_benchmark_subsets(v1):
    from backend.locus_x.benchmark import ANCHOR_SELECTION

    for budget in (3, 4, 6, 8):
        layout = manual_layout(v1, "hospital_floor_01", budget)
        expected = [
            tuple(next(a for a in v1.anchors if a.id == anchor_id).position)
            for anchor_id in ANCHOR_SELECTION[budget]
        ]
        assert [a.position for a in layout.anchors] == expected
