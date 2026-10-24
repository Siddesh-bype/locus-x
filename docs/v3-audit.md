# V3 foundation: repository audit

Audit date: 19 October 2026. Working branch: `feat/locus-x-v3-foundation`.

This audit records the pre-AI V3 foundation. The implemented LOS/NLOS reliability branch is documented separately in [ai-reliability.md](ai-reliability.md); the inference row below describes the audited baseline only.

## Existing implementation

The source tree is a small, working React 19 / TypeScript / R3F application backed by Python/FastAPI, NumPy and SciPy. No rewrite is needed. The initial working tree was clean on `main`.

| Area | Source of truth | Finding / reuse |
| --- | --- | --- |
| Environment | `configs/environments/hospital_floor_01.json`, `environment.py`, `schemas.py` | One validated 30 × 20 m floor, eight anchors, six rooms and two looping routes. Doors are gaps in solid wall segments. Preserve this fixture unchanged. |
| Radio simulation | `simulation.py` | Per-anchor seeded noise, wall intersections, positive bias, dropout and scheduled outages. Receives the same floor geometry that is sent to the browser. Reuse without changing fault strengths. |
| Inference | `estimators.py`, `tracking.py` | Algebraic initialization + nonlinear least squares; MAD/Huber/consistency weighting; four-state Kalman tracker, gating and three-second coasting timeout. No learned classifier. Preserve the algorithms. |
| Evaluation | `evaluation.py`, `benchmark.py` | Truth-based errors and coverage are calculated after inference. Benchmark includes seven scenarios × four anchor counts × two seeds × 160 frames = 8,960 evaluated frames. Named A1–A8 subsets assume the reference floor. |
| API | `main.py`, `session.py` | REST with revision-safe writes, per-session locks, in-memory state, one serialized benchmark worker. No database, socket streaming, radio ingestion or deployment platform. Frames lack environment/floor identity. |
| Client | `useSession.ts`, `types.ts`, `lib.ts` | API supplies geometry, anchors, observations, estimates, truth and uncertainty. Client serializes mutations and keeps a 600-sample window. Reconnect currently assumes the first environment is the session's environment. |
| Scene | `Scene.tsx` | Procedural geometry from the API; orthographic orbit/plan camera; canvas-texture labels; walls, anchors, paths, signals and ellipse. Furniture and corridor text assume one layout. Mapping is `(x,y) → (x,elevation,-y)` in metres. |
| Experiment interface | `Controls.tsx`, `Diagnostics.tsx`, `Charts.tsx`, `Benchmark.tsx` | Genuine controls and live data; very small typography and single-floor labels. Route choices are hardcoded. Live history can mix configurations, and only some live-window metrics are shown. |
| Prior experiments | `artifacts/benchmark`, `artifacts/nlos-audit`, `backend/research`, `configs/benchmarks/nlos-audit` | Reference, diagnosis and tuning artifacts exist. Research candidate estimators are separate from the production engine. Preserve all historical evidence. |

## Reference verified before editing

The saved benchmark contains the stated figures (rounded): moderate-noise RMSE 0.285 / 0.266 m, NLOS 1.540 / 1.968 m, combined faults 3.640 / 3.782 m (baseline / LOCUS-X). LOCUS-X nominal-95% empirical coverage is 49.375% under NLOS and 36.25% under combined faults. These are synthetic results, not real-radio validation.

Reference environment hash: `fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39`.

Executed before implementation: `python -m pytest -q` using `.venv` — **39 passed**, two upstream deprecation warnings. `npm.cmd test -- --configLoader native` — **2 passed**. No other pre-change check is claimed.

## Smallest compatible extension

1. Keep the legacy `Environment`, estimator input and fixture serialization intact. Extend that floor type for V3, then compose floors in a versioned building. Use solid wall segments, explicit door-gap metadata, semantic rooms, circulation, aligned access cores, movement-region references and independent candidate sites.
2. Resolve a building to one authoritative floor before invoking the existing simulator. Add environment/floor identity to frames and a revision-checked floor-selection endpoint. Switching floors clears filter/trust state and starts time at zero. This is localization on a known floor, not vertical estimation.
3. Load both environment types through the existing catalog. The same floor objects drive rendering, simulation and benchmarks. Keep the original default API environment and reference benchmark. Select V3 explicitly in the new UI.
4. Replace layout-specific scene construction with data-driven floor groups. Building/exploded modes add display elevations only; paths and estimates belong exclusively to the active simulation floor.
5. Extend the existing serial client workflow and real-data charts. Reset the comparison window on configuration/floor changes; expose metric denominators and unavailable coverage. Make geometry layers distinct from propagation controls.
6. Add schema/topology, API floor lifecycle, coordinate/view behavior and regression checks. Write new results to versioned output directories. No classifier or optimizer implementation in this phase.

## Risks to verify

- Legacy serialization must not change the reference hash or numerical results.
- Door descriptions must agree with physical gaps; complete route segments, including loop closure, must remain in walkable regions and avoid solid walls.
- Floor switches and reconnects must not display a stale floor or transplant tracking state.
- Missing positions must remain missing in charts/exports; uncertainty coverage must use only available ellipses.
- Installed sites on inactive floors and future candidate sites must not look like live observations.
- Three floors must remain usable on ordinary laptops: cached geometry, bounded DPR, no unnecessary effects, demand-based rendering.
- Existing benchmark losses under NLOS and poor calibration remain material limitations.

The V3 coordinate contract, handoff and final executed checks are recorded separately so this pre-implementation audit remains identifiable.
