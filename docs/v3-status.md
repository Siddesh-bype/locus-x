# LOCUS-X V3 foundation: delivery status

Date: 19 October 2026. Branch: `feat/locus-x-v3-foundation`.

The first-phase implementation is available in the working tree. It extends the existing application with a shared three-floor hospital, architectural inspection modes and a real-data experiment workspace. Backend tests, frontend tests, production lint and the final production build passed. The original benchmark reproduced exactly for accuracy and traces. **Interactive browser/WebGL acceptance remains unverified** because the available computer-use service exposed no browser surface.

This remains **2D localization on a manually selected, known floor**. Elevation is configuration, not an estimated vertical coordinate. The production estimator, tracker, measurement simulator and evaluation formulas were preserved. No trained AI model, anchor optimizer, real-radio validation or deployment readiness is claimed. No dependencies were added; no commit or push was made.

## Delivered foundation

| Deliverable | Implementation / evidence |
| --- | --- |
| Repository audit and compatibility plan | [V3 audit](v3-audit.md), completed before implementation; initial working tree was clean on `main` |
| Shared hospital configuration | [hospital_v3.json](../configs/environments/hospital_v3.json): fictional Meridian Hospital, 42 × 28 m; Ground/First/Second at 0/4.2/8.4 m; 10/10/8 semantic rooms; corridors, actual door gaps, aligned stairs/lifts and validated movement routes |
| Backward-compatible environment model | `Floor` extends the original `Environment`; `Building` composes floors, vertical connections, validation metadata and independent candidate sites. The V1 fixture and default API environment are retained |
| Functional architectural scene | Floor/building/exploded views, plan/orbit camera, fit/zoom/expand and room/corridor/wall/label/access/anchor/route/trajectory/uncertainty/signal layers. Configured candidate sites are an independent inspection layer |
| Actual experiment state | Revision-safe floor selection, selected-floor simulation, synchronized session/settings state, truthful anchor status, baseline/LOCUS-X/truth paths and errors. Inactive floors show static sites without live-status claims |
| Comparison workspace | Actual window RMSE, median, P95, valid/fresh-update rates, measured runtime and empirical coverage; bounded sample history, missing-fix gaps, scenario JSON and sample CSV exports; independent benchmark jobs and exports |
| Coordinate contract | [V3 contract](v3-contract.md): local `(x, y)` metres map to `(x, elevation, -y)` in Three.js. Exploded offsets affect display only. Floor changes reset independent 2D runs |
| Team integration handoff | [V3 handoff](v3-handoff.md): inference-feature and split boundaries, model bundle requirements, candidate/layout output contract, versioned evaluation, floor transitions and acceptance criteria |
| Setup and architecture | Updated [README](../README.md), [contribution guide](../CONTRIBUTING.md), [architecture](architecture.md), [limitations](assumptions-limitations.md) and historical [build-status pointer](build-status.md) |

The renderer uses instanced solids, demand rendering and a DPR cap of 1.5. These are implementation measures; laptop FPS and GPU performance have not been measured. The active floor is the default view so live overlays remain prominent. Geometry visibility is independent of wall-induced range bias.

## Executed checks

Commands below were executed from the repository root, using the existing virtual environment and installed npm dependencies. No test is reported as passing solely from inspection.

| Command / check | Actual result |
| --- | --- |
| Pre-change `.\.venv\Scripts\python.exe -m pytest -q` | 39 passed; two upstream deprecation warnings |
| Pre-change `npm.cmd test -- --configLoader native` | 2 passed |
| `.\.venv\Scripts\python.exe -m pytest --cov=backend.locus_x --cov-report=term-missing -q` | **70 passed, 93% backend coverage** on the approved rerun. Initial restricted run: 69 passed, one setup error due to the Windows pytest temporary-directory permission. No failing assertion was hidden |
| `npm.cmd test -- --configLoader native` | **24 passed**, including the final camera/view reducer changes |
| `npm.cmd run typecheck` | Passed; final build also reran TypeScript after the last camera/CSS changes |
| `npm.cmd run lint` | Passed after the final camera/CSS changes |
| `.\.venv\Scripts\python.exe -m ruff check backend/locus_x backend/tests scripts` | Passed |
| `.\.venv\Scripts\python.exe -m ruff format --check backend/locus_x backend/tests scripts` | Passed |
| `npm.cmd run build` | **Passed after the final camera/CSS changes**: TypeScript and Vite production compilation. Lazy scene chunk: 905.71 kB minified / 244.49 kB gzip; Vite emitted its >500 kB advisory |
| `.\.venv\Scripts\python.exe scripts/build_hospital_v3.py` | Generated the V3 fixture; its deterministic authoring output was checked against the validated runtime JSON |
| `git diff --check` | Passed; Git emitted CRLF conversion advisories |
| `git branch --show-current` | Confirmed `feat/locus-x-v3-foundation` |
| Local documentation-link and HTTP asset inspection | All 44 local links resolved. Both local app roots returned HTTP 200; backend-served HTML matched the final `dist/index.html`, and its entry JS/CSS returned HTTP 200. This checks delivery, not browser rendering |

Python coverage includes schema/topology validation, legacy loading, floor resolution, invalid/stale requests, floor/reset lifecycle, V3 benchmark metadata and artifact overwrite protection. Frontend tests exercise coordinate/ellipse mapping, floor/view/layer state, missing trajectory segments, comparison-window metrics, CSV escaping and server-rendered control contracts. These tests do **not** establish real browser interaction or visual correctness.

The preserved `backend/research` has 15 pre-existing Ruff findings and three files that fail Ruff's formatting check in the broader audit. Those research files are untouched; production-path lint/format checks above pass. The Python run also reports two upstream deprecations and a pytest cache-write permission warning.

## Reproducible experiment evidence

The following evaluation commands completed. Their existing output directories must not be reused: the CLI now refuses to overwrite `results.json` or `metrics.csv`. Omit `--output` for a fresh timestamped directory.

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/competition.json --output artifacts/v3-foundation/reference-reproduction
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/v3/ground-smoke.json --output artifacts/v3-foundation/ground-smoke
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/v3/first-smoke.json --output artifacts/v3-foundation/first-smoke
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/v3/second-smoke.json --output artifacts/v3-foundation/second-smoke
.\.venv\Scripts\python.exe scripts/verify_v3_http.py --output artifacts/v3-foundation/live-api.json
.\.venv\Scripts\python.exe scripts/verify_v3_http.py --url http://127.0.0.1:5173 --output artifacts/v3-foundation/live-proxy.json
```

| Evidence | Result |
| --- | --- |
| [Protected-file manifest](../artifacts/v3-foundation/baseline-manifest.json) and [preservation check](../artifacts/v3-foundation/preservation-check.json) | **24 protected source/configuration/artifact files unchanged** |
| [Reference reproduction](../artifacts/v3-foundation/reference-reproduction/results.json) and [comparison](../artifacts/v3-foundation/reference-comparison.json) | **8,960 evaluated frames; all 56 accuracy rows identical**, as are the configuration, environment identity/hash, traces and distributions. Wall-clock runtime is deliberately excluded from equality; the reproduced run took approximately 59.4 s |
| [Ground smoke](../artifacts/v3-foundation/ground-smoke/results.json) | 360 frames completed |
| [First smoke](../artifacts/v3-foundation/first-smoke/results.json) | 360 frames completed |
| [Second smoke](../artifacts/v3-foundation/second-smoke/results.json) | 360 frames completed |
| [Direct API check](../artifacts/v3-foundation/live-api.json) | All three floors and the legacy default passed |
| [Vite proxy check](../artifacts/v3-foundation/live-proxy.json) | All three floors and the legacy default passed through the frontend proxy |

The V3 smoke protocol uses clean, NLOS and combined-failure scenarios; eight anchors; seeds 17 and 29; 60 evaluated steps per run at 0.5 s. Its **1,080 total frames** validate operation on all floors and do not establish algorithmic improvement. HTTP verification exercises actual create/read/step/control/floor/reset endpoints, including fault settings, stale/invalid floor rejection and state preservation. Temporary verification sessions are cleaned up.

Reference environment SHA-256: `fa1d5b056ee2b775d1f65fba958431332e4557f224ca2dbccca119fa760c4b39`.

Historical moderate-noise/NLOS/combined-failure figures remain in the README and original artifacts. Existing NLOS underperformance and poor nominal-95% coverage remain unresolved algorithmic limitations. New outputs are separate from the historical reference. Generated artifacts and `dist/` remain ignored by Git under the existing repository policy; the smoke configurations and generation/verification scripts are included in the working changes. Artifact links above refer to this workspace's actual generated files.

## Changed and new files

Paths are relative to the repository root. `Modified` means an existing tracked file; `New` means a new source/configuration/documentation file. Generated evidence/build output is listed separately above.

| Status | Files | Responsibility |
| --- | --- | --- |
| New | `backend/locus_x/building.py`, `backend/locus_x/geometry.py` | Building/floor schema, architectural validation and exact movement-segment coverage |
| Modified | `backend/locus_x/environment.py`, `backend/locus_x/schemas.py` | Legacy/V3 loading, floor resolution, compatible API identity and request fields |
| Modified | `backend/locus_x/main.py`, `backend/locus_x/session.py` | Floor lifecycle, revision safety, frame identity and selected-floor runtime |
| Modified | `backend/locus_x/benchmark.py` | V3 floor evaluation/metadata; legacy protocol preservation; safe output directories |
| New | `backend/tests/test_building.py` | New schema, API, benchmark and compatibility coverage |
| New | `configs/environments/hospital_v3.json` | Shared three-floor architectural and simulation configuration |
| New | `configs/benchmarks/v3/ground-smoke.json`, `configs/benchmarks/v3/first-smoke.json`, `configs/benchmarks/v3/second-smoke.json` | Versioned per-floor smoke protocols |
| New | `scripts/build_hospital_v3.py`, `scripts/verify_v3_http.py` | Deterministic fixture authoring and live API/proxy verification |
| Modified | `src/App.tsx`, `src/styles.css`, `index.html` | Experiment shell, responsive layout, loading/error states and application metadata |
| Modified | `src/Controls.tsx`, `src/Diagnostics.tsx`, `src/Charts.tsx`, `src/Benchmark.tsx` | Real experiment controls, measured comparison/diagnostics, exports and benchmark workflow |
| New | `src/EnvironmentPanel.tsx`, `src/HelpDialog.tsx` | Environment/floor/layer controls and scoped operating help |
| Modified | `src/Scene.tsx` | Data-driven scene composition and viewport state |
| New | `src/scene/Architecture.tsx`, `src/scene/architectureGeometry.ts` | Floor architecture, semantic furnishings and instanced geometry preparation |
| New | `src/scene/CameraRig.tsx`, `src/scene/Overlays.tsx`, `src/scene/Primitives.tsx` | Orbit/fit/relative zoom, actual experiment overlays and reusable drawing primitives |
| Modified | `src/types.ts`, `src/useSession.ts`, `src/lib.ts` | Shared TypeScript contracts, serial session synchronization and sample/export helpers |
| New | `src/workspace.ts`, `src/workspace.test.tsx` | Floor/view/layer/metrics logic and behavioral contracts |
| Modified | `src/lib.test.ts` | Coordinate and export regression coverage |
| Modified | `package.json`, `package-lock.json` | Package version 3.0.0; dependency set preserved |
| Modified | `README.md`, `CONTRIBUTING.md`, `docs/architecture.md`, `docs/assumptions-limitations.md`, `docs/build-status.md` | Setup, architecture, contribution boundaries, limitations and historical status pointer |
| New | `docs/v3-audit.md`, `docs/v3-contract.md`, `docs/v3-handoff.md`, `docs/v3-status.md` | Audit, shared contract, remaining team work and this executed-check report |

## Remaining work and verification gaps

- **Visual acceptance:** the computer-use service returned `No browser is available`, an empty browser inventory, and `Browser is not available: iab`. No screenshots, browser clicks, responsive inspection, keyboard-focus behavior or WebGL/GPU performance are claimed as verified. This was a missing browser surface, not an automatic approval rejection. The handoff includes a concrete desktop/mobile acceptance pass.
- **Actual floor transitions:** stairs/lifts and connection topology exist, but automatic floor detection, cross-floor propagation, continuous vertical state and tracker handoff do not. Manual floor selection starts a new independent 2D run; reset restores the creation floor/settings.
- **AI-assisted comparison:** unavailable until trained weights, preprocessing, leakage-safe inference, grouped splits and held-out evaluation exist. There is no fabricated third method in the UI.
- **Anchor optimization:** candidate sites and installed layouts are represented separately; no optimization action is exposed without a real evaluator and configuration-producing job. The existing benchmark still uses the named A1–A8 subsets.
- **Evaluation expansion:** smoke runs establish functionality only. New algorithms/layouts require versioned protocols and honest calibration/availability reporting. Historical results remain available unchanged.
- **Architecture/model limits:** the hospital is fictional. The current fixture renderer assumes the supplied south-facing access-core entries; furniture is visual and is not a radio obstruction model. The lazy scene bundle remains large, as reported by Vite.
- **Runtime limits:** sessions and jobs are in memory over REST; no persistent experiment library, benchmark cancellation, WebSockets, physical UWB/BLE ingestion or public deployment is provided in this phase.

The existing local services were verified at [the frontend](http://127.0.0.1:5173) and [FastAPI](http://127.0.0.1:8000/docs). Attempts to start duplicate servers encountered occupied ports; the running services were inspected and verified instead of being terminated. Follow the README for a fresh start. These addresses describe local development services, not a deployed site.

The working tree is ready for review and the parallel workstreams in [the handoff](v3-handoff.md). Final visual sign-off still requires a browser-enabled session.
