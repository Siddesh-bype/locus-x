# Architecture

V3 adds a compositional building model around the existing engine. Read the [V3 contract](v3-contract.md) for exact fields, coordinate rules, validation and compatibility, and the [team handoff](v3-handoff.md) for the remaining workstreams. The historical algorithm and benchmark definitions below remain applicable to the selected floor.

## Modules

| Module | Responsibility |
|---|---|
| `configs/environments/*.json` | Legacy floor or versioned building; shared by simulation, API, renderer and benchmark |
| `building.py`, `geometry.py` | V3 floor composition, exact route/region validation, aligned connections and reusable segment geometry |
| `environment.py` | Typed loading, geometry validation, segment intersection, route interpolation |
| `simulation.py` | Seeded range generation; observations and simulator-only labels returned separately |
| `schemas.py` | Strict Pydantic API contracts; extra fields and nonfinite inputs rejected |
| `estimators.py` | Validity/geometry checks, baseline, Huber robust solve, optional multiplicative AI weights and reliability diagnostics |
| `reliability.py` | Inference-safe 24-feature LOS/NLOS adapter, calibrated probability, ambiguity fallback and weight policy |
| `tracking.py` | Constant-velocity Kalman state, covariance, innovation gate, outage handling, ellipse |
| `evaluation.py` | Ground-truth error and covariance coverage; no feedback into estimation |
| `session.py` | Authoritative settings, time, frame, trust, AI and filter state; reproducible reset |
| `benchmark.py` | Independent experiment loop and metrics/CSV/JSON export |
| `main.py` | FastAPI, per-session locks, revisions, bounded session/job stores |
| `src/useSession.ts` | Serial HTTP requests, same-session reconnect and actual-frame history |
| `src/Scene.tsx`, `src/scene/*` | Data-driven floor groups, slab/core openings, instanced architecture, known-floor overlays and demand-rendered camera |
| `src/EnvironmentPanel.tsx`, `src/workspace.ts` | Floor/layer controls, legacy adaptation, view state, coordinate/trajectory and window-metric helpers |
| `src/Controls.tsx` | Scenario, fault, anchor and target settings |
| `src/Diagnostics.tsx` | Frame diagnostics and independently calculated session-window statistics |
| `src/Benchmark.tsx`, `src/Charts.tsx` | Separate completed experiments and charts from real records |

## Truth boundary

The estimator's only observation type is `Measurement(anchor_id, anchor_position, timestamp, observed_range)`. Simulator-only `FaultLabel` contains true range, applied bias and intersected-wall IDs. These are separate lists; all three methods receive the **same measurement list** and the AI adapter receives no truth, label, bias or fault flag. The tracker receives only a robust result and timestamp. The estimator, reliability and tracking modules do not import simulation or evaluation. Evaluation runs afterward and cannot affect the current or next estimate.

The browser can inspect truth and fault labels because this is a synthetic experiment. Its signal rays visualize simulator geometry, not inferred radio paths. There is no claim that a real radio exposes these labels.

## Coordinates and geometry

Backend coordinates are `(x, y)` in metres, within `[0,width] × [0,height]`. Three.js coordinates are `(x, elevation, -y)` in metres: +X is east, +backend-Y is north, and Three.js +Y is vertical. `toScene`/`fromScene` implement the mapping; frontend tests round-trip every configured anchor and waypoint.

Walls are 2-D line segments with IDs and physical height. Doors are actual missing wall segments; V3 adds validated opening metadata and circulation references. The propagation generator intersects the horizontal ray with the same solid segments. The display can show physical heights or a cutaway (0.62 m external / 1.05 m internal); propagation geometry is unchanged. Room colors, beds, scanners and shelves are decorative and do not generate signal obstruction. Every complete V3 route segment, including loop closure, is validated against walls and walkable regions.

JSON is editable and validated at backend startup. Restart after editing it. `load_environments` accepts both V1 and V3; `resolve_floor` supplies one floor to the unchanged simulator. Physical elevations are 0/4.2/8.4 m in the supplied building. Exploded offsets affect rendering only. Session frames identify the selected floor and explicitly say `2d_on_known_floor`. The benchmark can run a selected V3 floor but retains named A1–A8 subsets; arbitrary optimized layouts require a new explicit-layout protocol.

## Session and request behavior

- `GET /api/v1/health`: engine readiness.
- `GET /api/v1/environments`: legacy floor/building definitions, presets, coordinate convention.
- `POST /api/v1/sessions`: environment, optional floor and settings → initial computed frame. Empty input still selects V1.
- `GET /api/v1/sessions/{id}`: current authoritative frame.
- `POST /api/v1/sessions/{id}/advance`: `{expected_revision, dt}` → next frame.
- `PATCH /api/v1/sessions/{id}/controls`: `{expected_revision, ...changes}` → recomputed frame.
- `POST /api/v1/sessions/{id}/floor`: `{expected_revision, floor_id}` → fresh known-floor 2D run, with no tracking state transferred.
- `POST /api/v1/sessions/{id}/reset`: `{expected_revision}` → time zero with the session's creation settings restored.
- `DELETE /api/v1/sessions/{id}`: release local session.
- `POST /api/v1/benchmarks`: configuration plus optional environment/floor → asynchronous job ID.
- `GET /api/v1/benchmarks/{id}`: status, progress and completed result.

Session mutations use a lock and monotonically increasing revision. A stale request returns 409. Requests with invalid parameters return 422; unknown sessions/environments return 404. The browser serializes requests, stops on error, and offers reconnect. It never runs concurrent advancement requests.

Control changes at the same timestamp restore the pre-frame filter and trust snapshot, then regenerate and solve once. This prevents an unchanged measurement being counted repeatedly. Route, speed and seed edits restart time with the edited settings to avoid target discontinuities. Floor switches also reset time/filter/trust. Reset restores the floor and settings saved at session creation, preserves revision monotonicity, and resets RNG frame index, tracking, trust, event history and simulation time. A custom API session returns to its custom creation settings, not the browser defaults.

At most 64 sessions are retained, with one-hour idle expiry checked when creating sessions. Up to five recent benchmark jobs remain in memory. One benchmark worker runs at a time, and a request is bounded to 25,000 frames. Sessions and jobs do not survive a backend restart. Use one Uvicorn worker; there is no cross-process state synchronization.

## Frontend and operation

React/TypeScript + Vite, React Three Fiber/Three.js, Lucide icons, small SVG charts, locally bundled IBM Plex Sans/JetBrains Mono fonts. The 3-D module loads separately. No image services, CDN fonts, external APIs, database, websocket service, or credentials are used at runtime. Development uses the Vite same-origin proxy; a built demo can be served entirely by FastAPI.

V3 uses instanced architectural solids, a 1.5 DPR cap and demand rendering; the camera invalidates frames only while moving/damping or when state changes. Browser history contains actual server frames, resets on configuration changes and breaks trajectories across missing estimates/floors. Metrics and CSV exports preserve unavailable values. Candidate sites are a separate visualization layer and never enter measurement generation.

## Reference preservation

The V1 fixture, competition configuration, estimator/tracker/simulator/evaluator files and historical experiment artifacts were preserved. The full 8,960-frame reference was rerun into `artifacts/v3-foundation/reference-reproduction`: all 56 accuracy rows, traces, distributions, configuration and environment hash matched exactly (excluding runtime). New V3 outputs contain building/floor/version metadata. The CLI refuses to overwrite existing result files and chooses a unique output directory by default.
