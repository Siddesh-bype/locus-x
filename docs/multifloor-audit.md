# Multi-floor positioning: audit and migration record

Audit date: 22 October 2026. Branch: `feat/multifloor-positioning`, base `d430559`.
Working tree clean at the start of the audit.

This is the Phase 1 deliverable for the multi-floor extension. It records the single-floor assumptions of the
current engine, the coordinate convention already in force, the anchor and device elevation derivation that
later tasks use, the 3D observability measurement, the migration and rollback plan, and the compatibility
matrix. Later tasks cite this document as their migration record.

**The multi-floor engine is not implemented.** Every forward-looking item below is marked *planned*. The
current engine remains range-only localization of `(x, y)` on one manually selected, known floor. This audit
changed no schema, algorithm, configuration or frontend file; it adds this document and
`scripts/probe_vertical_observability.py`.

## Evidence verified before writing

| Check | Result |
| --- | --- |
| `sha256(Environment.model_dump_json())` for `hospital_floor_01` | `fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39`, unchanged |
| `configs/environments/hospital_floor_01.json` | Not modified by this task |
| `.\.venv\Scripts\python.exe -m pytest -q` | 103 passed, two upstream deprecation warnings (366 s) |
| `.\.venv\Scripts\python.exe scripts/probe_vertical_observability.py` | Runs clean; prints 0.62 / 0.94 / 0.83 m and the condition numbers in §4 |
| `.\.venv\Scripts\python.exe -m ruff check`, `ruff format --check` on this task's files | Passed |

## 1. Single-floor assumptions in the current engine

| # | Assumption | Location | Current behaviour | Consequence for more than one floor |
| --- | --- | --- | --- | --- |
| 1 | Ranges are horizontal distances | `simulation.py:20` | `true_range` is the norm of the difference between two 2-element positions; wall intersections at `simulation.py:21` are also 2D | An anchor one storey away at the same `(x, y)` would report the in-floor distance. Vertical separation and slab attenuation are absent |
| 2 | Anchors and measurements carry two coordinates | `schemas.py:7` (`Point = tuple[float, float]`), `schemas.py:15-17` (`Anchor.position`), `schemas.py:127-133` (`Measurement.anchor_position`) | The estimator input surface is `(anchor_id, anchor_position, timestamp, observed_range)` | No `z`, no floor identity and no mount height reach the estimator; an anchor ID alone cannot identify a storey |
| 3 | The position solve is two-dimensional | `estimators.py:27-107`; collinearity test `estimators.py:41-43`; two-column Jacobian and information matrix `estimators.py:82-90` | Algebraic initialization and nonlinear least squares over `(x, y)`; the failure text at `estimators.py:87` speaks of "both position axes" | A third coordinate is not estimated, and the 2 × 2 covariance cannot express vertical uncertainty |
| 4 | The tracker state is four-dimensional | `tracking.py:25-96`; four-state initialization `tracking.py:54`; 4 × 4 transition `tracking.py:37-40`; two-row observation `tracking.py:61`; reported 2 × 2 covariance `tracking.py:84-90` | State is `[x, y, vx, vy]` with a 2-D innovation gate (`tracking.py:65`) | No vertical velocity, elevation or floor state; a floor change is a hard restart of filter, trust and time (`session.py:142-148`) |
| 5 | Evaluation compares two 2-D positions | `evaluation.py:9-18`; 2-D error `evaluation.py:13`; 2-D 95% containment `evaluation.py:17-18`; `Evaluation` schema `schemas.py:176-182` | Error, coverage and truth are planar | Floor identity, vertical error and transition recovery cannot be scored or reported |
| 6 | Floor identity is a manual input, not an estimate | `session.py:125-140` (`select_floor`); floor resolution `environment.py:25-35`; endpoint `main.py:199`; reset `session.py:119-123` | `select_floor` resolves a configured floor, then restarts the run at time zero with a fresh tracker and trust map | The floor is chosen by the operator. There is no classifier, altimeter or vertical solve, and no evidence of a modelled transition |
| 7 | The advertised localization mode is fixed | `schemas.py:191-210`, literal at `schemas.py:198`; frame construction `session.py:85-97`; benchmark rows `benchmark.py:148` | Every frame and V3 benchmark result declares `localization_mode: "2d_on_known_floor"` | There is no API value or artifact field for a 3D solve, a route-assisted transition or an inferred floor |
| 8 | Benchmarks resolve one floor and the reference anchor ring | `benchmark.py:57-67`, anchor selection at `benchmark.py:82-84` | `resolve_floor` chooses one floor; named A1-A8 subsets are validated against that floor's installed anchors | A multi-floor protocol needs its own configuration model and result shape; the legacy benchmark must not be widened |
| 9 | The scene maps floor-local `(x, y)` to a display elevation | `src/lib.ts:4`; contract `docs/v3-contract.md:20` | `toScene([x, y], elevation) => [x, elevation, -y]` | Display already supports elevation, but no live state, covariance or overlay contains a vertical coordinate |

Anchor coordinates are identical on all three configured floors (`(1,1)`, `(21,1)`, `(41,1)`, `(41,14)`,
`(41,27)`, `(21,27)`, `(1,27)`, `(1,14)`). The horizontal projection of the building therefore repeats
exactly on every floor: horizontal geometry carries no storey cue, and a path that reasons only in `(x, y)`
cannot separate floors.

## 2. Coordinate convention in force

The convention below restates `docs/v3-contract.md:9-28`. No second convention is introduced here; the
planned 3D work adds an explicit `z` to this same floor-local frame.

| Field / space | Meaning |
| --- | --- |
| `position: [x, y]`, `a`, `b`, `center`, `entry` | Floor-local coordinates in metres; +X east, +Y north |
| `width` | East-west extent in metres |
| `height` on environment, building or floor | North-south length retained for V1 compatibility; **not vertical height** |
| `height` on wall or door | Physical vertical extent in metres |
| `size: [width, length]` | Axis-aligned room or region footprint |
| `floor.elevation` | Known floor datum above building ground, in metres |
| `Frame.floor_elevation` | Authoritative selected-floor datum; carries no estimation claim |
| Three.js mapping | `(x, y) -> (x, elevation, -y)`; one metre is one scene unit |
| Exploded placement | Adds `index × EXPLODE_GAP` to the configured elevation for display only |
| Covariance | 2 × 2 in the backend X/Y plane, units m² |
| Ellipse | Semimajor and semiminor in metres; angle in backend X/Y radians, rotated before mapping to X/-Z |
| Timestamps | Simulation seconds; HTTP and render time do not determine the target position |

All floors share one local origin. No floor-dependent X/Y rotation or translation is applied. Scene
geometry, observations, paths and ellipses are children of the same floor transform. Unavailable fixes do
not become zero coordinates; trajectories break across missing estimates and floor changes; estimates
outside the footprint are not clamped.

The estimator consumes two-dimensional ranges and anchor coordinates, so the target's `z` is configuration,
not an estimate. The Three.js elevation is a datum plus display offset, not a solved coordinate.

## 3. Anchor and device elevation used by later tasks

The current `Floor` type (`building.py:38-50`) has no per-anchor mount height. The elevation rule for
multi-floor work is therefore:

```
anchor z = floor.elevation + mount height          mount height = 2.6 m by default
device z = floor.elevation + 1.2 m                 target device height
```

`2.6 m` is the mount height adopted for the multi-floor work; later tasks use it rather than introducing a
second convention. The configuration does not record it yet, which is why 3.1 adds a per-anchor override. It
sits below the configured 3.2 m wall height, and the building validator already requires 0.2 m of slab and
wall clearance between floor elevations (`building.py:200-205`). `1.2 m` is the target device height. The
configurations are unchanged by this audit.

| Floor | `floor.elevation` (m) | Anchor `z` (m) | Device `z` (m) |
| --- | --- | --- | --- |
| `ground` | 0.0 | 2.6 | 1.2 |
| `first` | 4.2 | 6.8 | 5.4 |
| `second` | 8.4 | 11.0 | 9.6 |

Eight vertical columns exist because each floor installs the same eight `(x, y)` anchors at a different
datum. The planned 3.1 schema makes the rule configurable rather than hard-coded:

- `Floor.anchor_mount_heights: dict[str, float] = {}` - anchor ID to metres above the floor datum, default
  `2.6` for IDs not listed, validated against installed anchors and the slab clearance.
- `Building.target_device_height: float = 1.2` - device height above the selected floor datum.

Both fields are optional with defaults, so 3.0 documents keep validating unchanged. The probe script
already reads them through `getattr` and falls back to the values above; it therefore keeps working,
unchanged, after the fixture is bumped to 3.1.

## 4. 3D observability of the configured anchor set

### Method

`scripts/probe_vertical_observability.py` resolves the building under the identifier `hospital_v3` from
`backend.locus_x.environment.load_environments()` and uses the validated model it returns. It does not
hard-code the schema version or any anchor coordinate: anchor positions, floor datums and mount heights come
from the configuration, and the device height comes from the configuration with a `1.2 m` fallback.

For a target at `p` and anchor `a_i`, the unit range direction is

```
u_i = (a_i - p) / ||a_i - p||
H   = sum_i u_i u_i^T                          (3 × 3 information matrix, unit range noise)
Cov = H^-1                                     (unit range noise)
sigma_z = sqrt(Cov[2, 2])
```

Range noise is one metre (unit noise). A real run scales `sigma_z` by its own range sigma, because the
covariance is proportional to `sigma_r^2`. The probe evaluates a 2 m interior grid over each floor footprint
(260 points per floor, 42 × 28 m), with the target at `floor.elevation + 1.2 m`, and uses all 24 installed
anchors from the three floors. `cond(H)` is the 2-norm condition number of `H`.

### Results

| Floor | Datum (m) | Device `z` (m) | Grid points | Median `sigma_z` (m) | Median `cond(H)` | `sigma_z` at `sigma_r = 0.15` (m) |
| --- | --- | --- | --- | --- | --- | --- |
| `ground` | 0.0 | 1.2 | 260 | **0.62** | 5.33 | 0.09 |
| `first` | 4.2 | 5.4 | 260 | **0.94** | 12.70 | 0.14 |
| `second` | 8.4 | 9.6 | 260 | **0.83** | 9.88 | 0.12 |

The three medians reproduce the reference values recorded for this phase (0.62 / 0.94 / 0.83 m). Grid
resolution moves the second decimal only: a 0.5 m grid gives 0.62 / 0.92 / 0.82, a 1 m grid gives
0.62 / 0.93 / 0.83, and a 2 m grid gives the values above. The ordering is therefore a property of the
vertical geometry, not of the sampling.

The ordering follows from the vertical information available on each floor, which grows with
`sum_i ((z_i - z_target) / r_i)^2`. On the ground floor the target is 1.4 / 5.6 / 9.8 m from the three anchor
planes; on the first floor it is 2.8 m above the ground anchors, 1.4 m below the first-floor anchors and
5.6 m below the second-floor anchors, and the two nearest planes contribute little vertical leverage. The
first floor is consequently the worst-conditioned, exactly as the table shows.

### Condition number: recorded discrepancy

The multi-floor brief recorded `cond(H) = 4-5`. The probe measures medians of 5.33 (`ground`), 12.70
(`first`) and 9.88 (`second`). Only the ground floor lies near the stated band; its per-cell condition
numbers run roughly 4.3-6.1 between the tenth and ninetieth percentiles. The first floor is about 2.4 times
the ground median and the second about 1.9 times.

This is not a numerical or model difference: the same anchor geometry and the same noise convention
reproduce the three `sigma_z` medians to two decimals, and the condition number is scale-free. The 4-5
figure corresponds to the ground floor's better-conditioned cells, not to all three floors. Later tasks
should quote the measured medians above rather than a single 4-5 band. The expectations in the probe were
not adjusted to force agreement.

### Vertical observability requires more than one floor

| Case | Median `sigma_z` (m, unit noise) | Median `cond(H)` |
| --- | --- | --- |
| All 24 anchors, three floors | 0.62 / 0.94 / 0.83 | 5.33 / 12.70 / 9.88 |
| One floor's own eight anchors, coplanar | 4.02 on every floor | 78.89 |

With a single floor's eight anchors, ranges are mirror-symmetric about the anchor plane: a target 1.4 m
below the plane and its reflection 1.4 m above it produce identical ranges. The magnitude of the offset is
weakly observable (4.02 m at unit noise, about six times worse than the best 24-anchor floor) but the side of
the plane - hence the storey - is not. The current known-floor 2D path avoids this only because the floor is
an input. Any inferred-floor path needs anchors from at least two floors, which is what the 3D work plans.

These are geometric bounds under a synthetic, unit-noise range model. They are not real-radio accuracy,
they exclude NLOS bias, wall losses and slab attenuation, and they do not by themselves justify a
deployment claim.

## 5. Migration plan

### What changes, and how

| Change | Type | Legacy impact |
| --- | --- | --- |
| `Building.schema_version` accepts `"3.0"` and `"3.1"` | Additive widening of a literal | Existing 3.0 buildings validate unchanged |
| `Floor.anchor_mount_heights: dict[str, float] = {}` | Optional field with default | Absent in 3.0; default 2.6 m keeps today's geometry |
| `Building.target_device_height: float = 1.2` | Optional field with default | Absent in 3.0; no legacy reader |
| `Building.slab_bias_per_slab: float = 1.5` | Optional field with default | Synthetic model assumption, documented; unused by legacy |
| `Building.transition_routes: list[MultiFloorRoute] = []` | Optional field with default | Defaults to an empty list; 3.0 files omit it, route authoring is a later task |
| `backend/locus_x/multifloor/` (coordinates, routes, measurements, position, floor scoring, tracking, evaluation, protocol) | New package | Nothing on the legacy path imports it |
| Opt-in session mode and endpoints for 3D/floor-aware runs | New API surface | Default sessions keep `2d_on_known_floor`; legacy endpoints and result shape unchanged |
| Frontend mode selector, 3D overlays and transition display | New UI surface | Existing 2D workspace remains the default |

The new modules duplicate no legacy algorithm by editing it. `simulation.generate_measurements`,
`estimators.solve`, `tracking.MotionTracker`, `evaluation.evaluate` and `benchmark.run_benchmark` stay as
they are; the multi-floor path is a separate, explicitly selected pipeline. `Anchor`, `Environment` and
`Measurement` gain no fields because they serialise into the pinned legacy hash and the estimator input
contract.

### What stays behind the opt-in mode

- **Default session creation.** An empty `POST /api/v1/sessions` still creates `hospital_floor_01` with
  `localization_mode: "2d_on_known_floor"` (`docs/v3-contract.md:68`).
- **Legacy benchmark.** `configs/benchmarks/competition.json` accuracy rows and result shape remain the
  reference; the multi-floor protocol owns its own configuration model and output shape.
- **Known-floor 2D estimator and tracker.** They continue to serve legacy and V3 2D sessions; the 3D solve
  is only reached when a session requests the new mode.
- **Schema 3.0 snapshots.** The optimizer's layout files keep loading because every new field is optional.

### Planned order and gates

1. Additive schema 3.1 fields (no fixture bump yet) - 3.0 snapshots still load.
2. 3D coordinate projection (`Anchor3D`, anchor index, target `z`).
3. Multi-floor routes with stair/lift transitions; floor legs terminate at access-core entries.
4. 3D ranges with documented slab bias and slab-hop reporting.
5. 3D position solve, floor scoring and a vertical tracking state.
6. Multi-floor evaluation and its own benchmark protocol.
7. API/session integration and response fields, additive only.
8. Documentation and final verification.

Every step is gated on `pytest`, `ruff check`, `ruff format --check`, `npm run typecheck`, `npm run lint`,
`npm test` and `npm run build`, plus the pinned legacy hash and benchmark reproduction.

### Rollback

The change is additive at every layer, so rollback is deletion plus field reversion, not data repair:

1. Remove the opt-in mode wiring (session, API, frontend selector) and stop creating 3D sessions.
2. Delete `backend/locus_x/multifloor/` and its tests.
3. Revert the optional 3.1 fields in `building.py`; 3.0 buildings, the legacy `Environment` and every
   consumer continue to validate without them.
4. Restore `configs/environments/hospital_v3.json` to its 3.0 revision (or regenerate it with the authoring
   script at 3.0). The legacy `hospital_floor_01.json` is never touched.
5. Re-run the probe, `pytest`, ruff and the reference benchmark. The legacy path never depended on the new
   module or fields, so the pinned hash must return unchanged.

Versioned result directories are kept after a rollback; old results are never relabelled as new ones.

## 6. Compatibility matrix

| Configuration | Schema | Loader / consumer | Geometry | Localization path today | After the planned 3.1 work |
| --- | --- | --- | --- | --- | --- |
| `configs/environments/hospital_floor_01.json` | V1 `Environment`, no `schema_version` | `load_environments()`; default API environment; reference benchmark | One 30 × 20 m floor, eight 2D anchors | 2D on the known floor; `floor_elevation` 0.0 | Unchanged; not modified by this audit. Pinned model hash `fa1d5b05...c4b39` |
| `configs/environments/hospital_v3.json` | `Building`, `schema_version: "3.0"`, version `3.0.0` | `load_environments()` resolves `hospital_v3`; `resolve_floor` selects one floor | Three 42 × 28 m floors at 0 / 4.2 / 8.4 m; 8 installed anchors and 21 candidate sites per floor; aligned cores | 2D on a manually selected floor; V3 smoke benchmarks carry the 3.0 configuration hash | Re-authored at 3.1 with optional mount-height, device-height, slab-bias and transition-route fields. Its `environment_sha256` changes; existing V3 smoke artifacts stay identified by their recorded 3.0 hash |
| `configs/environments/layouts/*.json` (15 files) | 12 V1 `Environment`, 3 `Building` `3.0` | Optimizer and dashboard, via `optimization.layouts.validate_environment_config` (`layouts.py:208-211`); not read by `load_environments()`, which only globs the top-level configuration directory | Point layouts of the reference floor, plus three `hospital_v3 / ground` building snapshots | No session semantics of their own; they supply anchor geometry only | No edit required: new 3.1 fields are optional and defaulted. They remain 3.0 documents |

The multi-floor brief refers to "the optimizer's 18 layout snapshots (3.0)". The repository currently
contains **15** layout files: 12 legacy `Environment` snapshots (manual, grid and optimized at K = 3, 4, 6, 8)
and 3 `Building` 3.0 snapshots (`hospital_v3 / ground` manual, grid and optimized at K = 8). Only the latter
three are 3.0 buildings; the twelve legacy files have no schema version. This count difference is recorded
here rather than silently repeated.

Compatibility rules for all rows:

- `schema_version: "3.0"` keeps loading after `"3.1"` is added; new fields are optional and defaulted.
- No field is added to `Anchor`, `Environment` or `Measurement`.
- The legacy configuration hash and the legacy benchmark result shape are pinned.
- Multi-floor results are new, separately versioned outputs; legacy results are never relabelled.
- The synthetic slab and NLOS models remain documented assumptions, not validated radio physics.

## 7. Known gaps

- No 3D range model, 3D solve, floor estimator, transition tracker or multi-floor benchmark exists in this
  tree. Section 5 describes planned work.
- Vertical accuracy remains metre-scale under unit noise, and even at `sigma_r = 0.15 m` it is decimetres,
  before any NLOS or slab bias. The 3D solve's viability claim concerns conditioning only.
- Floor inference needs anchors from at least two floors; a single floor's coplanar ring cannot resolve the
  storey.
- The slab-bias value is a synthetic assumption that later tasks must document, not a measured attenuation.
- Existing NLOS and calibration limitations recorded in `docs/v3-audit.md:46` still apply.

## References

- `docs/v3-audit.md` - pre-implementation repository audit and preserved reference figures.
- `docs/v3-contract.md` - coordinate, building-schema and API contract restated in section 2.
- `docs/v3-status.md` - delivered V3 foundation and its executed checks.
- `scripts/probe_vertical_observability.py` - runnable reproduction of section 4.
