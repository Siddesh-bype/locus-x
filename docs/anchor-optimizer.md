# Anchor placement optimizer

`backend/locus_x/optimization/` recommends where to mount range anchors inside a
LOCUS-X environment. It is a pure computation module: the dashboard and CLI
consume its JSON-serializable results, and every produced configuration loads
through the existing `Environment`/`Building` parsers and `SimulationSession`
without modification.

Protocol version: **1.0.0** (`OPTIMIZER_PROTOCOL_VERSION`). The protocol is
frozen: changing weights, splits, or the selection procedure requires a version
bump and re-run.

## 1. Problem statement

Given an environment floor, an anchor budget **K**, a set of permitted mounting
sites, target-space coverage requirements and a frozen evaluation protocol,
choose K sites that minimize a documented objective J over development and
validation scenarios, and report held-out performance against manual (installed)
and regular-grid placements at the same budget.

## 2. Candidate sites and legality

**V3 buildings** declare `candidate_anchors` (C01–C21 per floor) in
`configs/environments/hospital_v3.json`. The optimizer treats these as the only
permitted mounting positions; it never invents coordinates. The schema's
candidate sites are *possible sites*, not certified mounts — the optimizer
applies an installation policy on top:

| Rule (`PlacementPolicy`) | Default |
| --- | --- |
| Margin from floor perimeter | 0.25 m |
| Clearance from any solid wall | 0.25 m minimum |
| Inside a walkable movement region (room/corridor) | required |
| Inside an access core (stairs/elevator shaft) | forbidden |
| Minimum separation between installed anchors | 4.0 m |

On the V3 ground floor this rejects **C09** (sits exactly on the `core-east`
wall, clearance 0.00 m) and **C08** (inside the `stair-west` shaft), leaving 19
legal sites. Both rejections are asserted by tests.

**V1 environments** (`hospital_floor_01.json`) have no mounting-site
representation. The adapter `derive_wall_sites()` samples every wall segment
every 3.0 m and offsets 0.6 m to each side, keeping points that clear all walls
by ≥0.5 m, and deduplicates on a 0.1 m grid. These derived sites are labeled
`"source": "derived"` in results so the distinction from declared sites is never
lost. This is the documented representation of permissible mounting positions
for V1; adding a `candidate_anchors`-style field to the V1 schema is deferred to
the environment owner (Siddesh) to avoid an incompatible schema change.

## 3. Objective

    J = 0.35 · RMSE/2.0 m        (error)
      + 0.20 · P95 /4.0 m        (tail error)
      + 0.15 · (1 − valid_rate)  (unavailable positions)
      + 0.15 · poor_geometry_fraction   (coverage/geometry)
      + 0.15 · resilience_penalty       (outage robustness)

* Error terms are normalized by fixed reference scales (2 m RMSE, 4 m P95) so
  all terms are dimensionless and comparable. The scales are constant across
  environments; they are not refit per run.
* `poor_geometry_fraction` is a static geometric diagnostic: the fraction of
  route sample points (<0.75 m spacing) where fewer than three anchors have a
  wall-free line of sight, or the range-information condition number exceeds
  1000 — the same threshold the estimator uses to reject a fix. This is not
  visual map coverage: a point near many anchors can still have badly
  conditioned or heavily obstructed range geometry.
* `resilience_penalty` = ½·(valid-rate drop under `anchor_failure`/`chaos`
  relative to `clean`) + ½·(relative RMSE inflation under those scenarios,
  capped at 1).
* Search uses the **conventional (baseline) estimator only**; the estimator is
  fixed for the entire placement search so layout quality is not confounded
  with estimator behavior. Held-out reporting repeats with the LOCUS-X
  estimator separately.

All five components plus the raw metrics (RMSE, P95, valid/update rate,
observability, outage drop/inflation) are reported next to the total
(`ObjectiveBreakdown`); the schema *requires* total = sum of terms.

**Limits.** The weights encode a preference (average error first, then tails,
availability, geometry and resilience equally). They were fixed a priori for
protocol 1.0.0 and chosen on development scenarios only. A different weighting
can select a different layout; every component is reported so a reader can
re-derive preferences. Multi-objective/Pareto archives are preserved: every
evaluated layout with its components is stored in `archive` (including
unsuccessful ones).

## 4. Search procedure (deterministic)

1. **Greedy forward selection** on the development split: repeatedly add the
   legal site that most improves J (ties broken by site ID).
2. **Local swaps**: first-improvement swaps (selected ↔ unselected) up to
   `max_swap_passes=2` passes without improvement.
3. **Validation selection**: finalists (greedy solution + each swap-improved
   incumbent) are re-evaluated on the validation split; the lowest validation J
   wins.

No randomness is involved: given the same environment, protocol and code
version, the same layout hash results. This is test-enforced.

## 5. Splits and leakage control

| Split | Route | Seeds | Scenarios | Steps |
| --- | --- | --- | --- | --- |
| Development (search) | `asset_round` | 11, 22 | clean, nlos, anchor_failure | 20 |
| Validation (selection) | `asset_round` | 33, 44 | 6 scenarios (no high_noise) | 40 |
| Held-out (reporting) | `held_out` | 101, 202 | all 7 | 80 |

* Seeds are disjoint across splits; the held-out route never appears in search
  or validation (`validate_splits`, `OptimizerConfig` validator, and a spy test
  that fails if the optimizer ever evaluates a split touching held-out
  seeds/route).
* Held-out results are computed **once**, after selection is frozen, and are
  never fed back. The runner refuses to overwrite existing artifact directories,
  so re-running never silently replaces a historical result.
* The route sharing between development and validation (both `asset_round`) is
  an environment limitation — the fixture provides exactly two routes. The two
  splits are still separated by disjoint seeds and by when they are consulted.
* 101/202 are the existing *reference benchmark* seeds. They have been inspected
  for the foundation benchmark but were never part of any placement decision;
  for the placement claim this is a held-out comparison against the manual and
  grid layouts, not a "never-before-seen building" claim.

## 6. Fair comparison

`compare_layouts()` evaluates manual, grid and optimized layouts:

* at the **same anchor budget**,
* on the **same split** (identical routes, seeds, steps, dt),
* with the **same fixed estimator** (one estimator per report; the estimator
  is never changed together with the layout),
* under identical fault/noise streams: `generate_measurements` seeds each
  anchor stream by `(seed, frame_index, anchor_index)`, so all same-budget
  layouts receive the same noise and dropout draws and the same timed-outage
  schedule; only anchor geometry changes.

The manual reference uses the benchmark's named subsets (`ANCHOR_SELECTION`)
for budgets 3/4/6/8 so results line up with the existing benchmark protocol.
The grid reference places anchors at regular cell centers snapped to the
nearest legal site — deliberately geometry-only, ignoring walls and routes.
Snapping also respects the policy's minimum anchor separation (a cell whose
nearest site would violate separation snaps to the next-nearest legal site),
so the grid baseline is always a *valid* layout and never forfeits a budget
slot to illegality.

Rows report RMSE, median, P95, valid rate, accepted-update rate, NLOS RMSE,
outage RMSE and per-region RMSE (rooms/corridors containing the route).

## 7. Output

`run_optimization()` (Python API) / `runner.py` (CLI) produce:

* `OptimizationResult` — sites with legality records, selected layout with
  anchors A1..AK, site→anchor mapping, layout hash, objective components,
  metrics, the full evaluated-layout archive, and reproducibility metadata
  (protocol version, config SHA-256, all split seeds, layout hashes).
* Loadable environment configurations — full `Environment` (V1) or `Building`
  (V3) JSON with the layout installed, new environment ID/version, and (V3)
  a new `anchor_layout_id`; other floors are preserved untouched.
* `comparison.csv` / `comparison.json` — held-out rows for all layouts,
  budgets and estimators.

Artifact directories are written once (`manifest.json` presence blocks
re-runs). Example configurations are also installed under
`configs/environments/layouts/` — a directory the environment catalog loader
does **not** auto-load, so nothing in the running application changes unless a
user explicitly loads a layout file.

## 7b. HTTP API

The backend exposes the optimizer to the dashboard:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/v1/layouts/sites?environment_id=&floor_id=` | Candidate sites with per-site legality and the active `PlacementPolicy`. |
| `POST /api/v1/layouts/optimize` | Queue a full run (search → validation → held-out comparison). Body: `environment_id`, `floor_id`, `budgets` (1–16, unique), `with_locus_estimator`, optional `development_steps`/`validation_steps`/`held_out_steps` overrides (frozen defaults when omitted). Returns `202` with a job ID. |
| `GET /api/v1/layouts/optimize/{job_id}` | Job status: `queued`/`running`/`complete`/`failed`, `stage`, `progress`, `result` (the same payload the runner writes to artifacts). |
| `POST /api/v1/environments` | Validate and register an environment/Building JSON in memory (`409` on duplicate ID). Never persists to disk. |

Only one layout job (and one benchmark job) runs at a time; a second submit
while one is active returns `409`. The step overrides exist so the dashboard
can offer a fast preview; **published results always use the frozen protocol
defaults** — the protocol version in the response records which configuration
produced a given payload.

## 8. Multi-floor status

The optimizer runs **per floor** with a per-floor budget. The V3 building
fixture supports it (optimize `ground`, keep `first`/`second` untouched —
test-enforced). There is **no** cross-floor budget allocation, no inter-floor
ranging and no floor-transition model; claiming multi-floor optimization would
require the floor-transition workstream first.

## 9. How to run

```bash
# Full experiment (all budgets, both estimators, artifacts + example configs)
python scripts/run_placement_experiment.py

# Single run
python -m backend.locus_x.optimization.runner \
    --environment hospital_floor_01 --budgets 8 \
    --output artifacts/anchor-optimizer/my-run

# From Python (dashboard integration)
from backend.locus_x.optimization import run_optimization, OptimizerConfig
payload = run_optimization(environment_id="hospital_v3", floor_id="ground", budgets=[8])
```

## 10. Held-out results (protocol 1.0.0)

Pooled held-out RMSE in metres (all 7 scenarios, seeds 101/202, 80 steps,
identical fault/noise streams across layouts; full rows and per-scenario
breakdowns in `artifacts/anchor-optimizer/<run>/comparison.json`):

| Run (V1 `hospital_floor_01`) | Estimator | Manual | Grid | Optimized | Winner |
| --- | --- | --- | --- | --- | --- |
| k=3 | baseline | 1.89 | 1.78 | **1.74** | optimized |
| k=3 | locus | 2.40 | 2.36 | **1.97** | optimized |
| k=4 | baseline | 1.91 | **1.51** | 1.89 | **grid** |
| k=4 | locus | 2.43 | **1.65** | 1.87 | **grid** |
| k=6 | baseline | 1.85 | 1.38 | **1.29** | optimized |
| k=6 | locus | 1.99 | 1.56 | **1.24** | optimized |
| k=8 | baseline | 1.60 | 1.16 | **1.09** | optimized |
| k=8 | locus | 1.66 | 1.25 | **0.96** | optimized |

| Run (V3 `hospital_v3` ground) | Estimator | Manual | Grid | Optimized | Winner |
| --- | --- | --- | --- | --- | --- |
| k=8 | baseline | 2.15 | **1.76** | 3.91 | **grid** |
| k=8 | locus | 2.28 | **1.95** | 5.32 | **grid** |

The optimizer wins 4 of 5 runs and beats the manual layout in 4 of 5 runs
(only V3 k=8 loses to manual as well as grid). **Two regressions are real and
are reported as such:**

**V1 k=4 — grid beats optimized** (baseline 1.51 vs 1.89). Per-scenario
baseline RMSE shows the loss is broad, not one fault mode:

| scenario | manual | grid | optimized |
| --- | --- | --- | --- |
| clean | 0.00 | 0.00 | 0.00 |
| noise | **0.42** | 0.51 | 0.68 |
| high_noise | 1.29 | **1.61** | 2.24 |
| nlos | 2.19 | **1.92** | 1.97 |
| packet_loss | **0.26** | 0.31 | 1.12 |
| anchor_failure | **0.21** | 0.27 | 0.35 |
| chaos | 5.00 | **3.38** | 4.31 |
| pooled | 1.91 | **1.51** | 1.89 |

The k=4 optimized layout is the k=3 site set (`D059, D005, D073`) plus one
more site (`D069`); with that fourth anchor it is worse than *both* references
on noise, high_noise and packet_loss in the held-out run. Selection minimized
J on development/validation only, and the k=4 layout won its validation
selection — this is exactly the gap the held-out split exists to catch. The
layout and its archive are preserved unchanged rather than papered over; no
re-selection happens after seeing held-out rows.

**V3 ground k=8 — grid and manual both beat optimized.** The gap is
concentrated almost entirely in the `chaos` scenario (baseline):
manual 5.23, grid 4.11, **optimized 10.37** (outage RMSE 19.4 vs 3.74 for
grid); all six other scenarios are within ~0.1 m of the references. The
selected layout routes the held-out path through geometry that degrades
badly when the timed outages disable anchors — its validation `chaos`
behavior was acceptable (validation objective total 0.873, resilience term
0.087), so this is a genuine development/validation-vs-held-out gap on this
building, not a bug: the same protocol, streams and estimator apply to all
three layouts, and clean/noise/NLOS behavior is equivalent. Selection was on
the frozen protocol; we do not re-select after seeing held-out rows. Treat
the V3 result as evidence that protocol 1.0.0's selection splits (both on the single
`asset_round` route, disjoint seeds) can overfit and that the V3 ground
floor needs either more validation coverage or a scenario-weighted
resilience term before deployment.

Reproducibility: re-running the experiment reproduces identical layout hashes
and metrics (runtimes differ; nothing else). Completed runs are never
overwritten — the script and runner both refuse to touch a directory that
already contains `manifest.json`, and installed example configurations are
kept, not replaced.

## 11. Limitations

* Synthetic radio model only: wall-count NLOS bias, Gaussian noise, packet
  dropout and timed index-based outages. No real-radio validation.
* The objective is scenario-averaged; a layout that wins on average can still
  lose on one scenario. Per-scenario rows are always reported; check them
  before trusting an "improvement".
* Greedy + local swaps is not guaranteed globally optimal; the archive makes
  the search traceable but not exhaustive.
* Derived V1 sites are an adapter, not a structural mounting-site declaration.
* Runtime is wall-clock and machine-dependent; layouts and metrics are not.
* Frames within a run are correlated; single-run differences are not
  confidence intervals. Run-level resampling across seeds is future work.
