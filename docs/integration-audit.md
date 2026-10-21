# LOCUS-X integration audit — 15 October 2026

This report covers the final integration audit, separately from the original build verification in `build-status.md`. No redesign or major feature was added. Browser re-verification remains pending because the automatic approval service returned HTTP 503; no new browser screenshot or successful browser interaction is claimed here. At the user's request, the run concludes with verified results and this explicit gap rather than waiting for approval-service recovery.

## Fixes

| Finding | Correction and evidence |
|---|---|
| Malformed scenario arrays/objects caused HTTP 500 | Guard preset lookup by type; four adverse input cases now return 422 in both tests and live HTTP. |
| Duplicate disabled anchor IDs were accepted and could corrupt the displayed enabled count | Reject duplicates before changing a session; creation and controls return 422 and preserve the previous frame. |
| Numerical failures during SVD or algebraic initialization escaped estimator handling | Include initialization in the numerical failure boundary; injected failures return `solver_failed` with null position for both methods. |
| Reset retained edited scenario, seed, route and anchor settings | Capture an independent copy of creation settings and restore it on reset; API tests compare original settings, time, measurements, evaluation and covariance. |
| Selecting obstruction/stress after disabling walls could leave obstruction inactive | Those presets now enable walls; explicitly supplied API overrides still take precedence. |
| Initial unavailable state implied a previous accepted update | Display the tracker’s actual reason. |
| Connection label implied a continuously monitored connection | Display “Last response OK” for the last successful response. |
| README tagline implied unvalidated real-world reliability | Describe the application as a synthetic laboratory. |

The two new numerical failure tests were executed before the fix and failed with uncaught linear algebra exceptions. After the fix, all twelve new audit regression cases passed. The initial Ruff check found long lines; formatting resolved them, and the final lint/format checks passed.

## Commands and executed results

Run from `C:\Users\satya\PROJECTS\APEX` in PowerShell.

| Executed command | Final observed result |
|---|---|
| `.\.venv\Scripts\python.exe -m pytest --cov=backend.locus_x --cov-report=term-missing -q` | PASS: 39 tests, 94% statement coverage; two upstream deprecation warnings. |
| `.\.venv\Scripts\python.exe -m ruff check backend` | PASS. |
| `.\.venv\Scripts\python.exe -m ruff format --check backend` | PASS: 14 files formatted. |
| `.\.venv\Scripts\python.exe -m pip check` | PASS: no broken requirements. |
| `npm.cmd run typecheck` | PASS. |
| `npm.cmd run lint` | PASS. |
| `npm.cmd test` | BLOCKED before collection: Vite configuration helper could not read a parent directory inside the sandbox. |
| `npm.cmd test -- --configLoader native` | PASS: 2 tests, all configured anchor/waypoint coordinate mappings plus history/reset/null handling. |
| `npm.cmd run build` | PASS: 1,611 modules; advisory scene chunk warning. |
| Documented backend startup command below | PASS: fresh server startup, followed by live requests. |
| `npm.cmd run dev -- --port 5173 --strictPort` | BLOCKED by the same sandbox configuration helper error. |
| Frontend startup with `--configLoader native` below | PASS: Vite ready on port 5173. |

The native configuration loader was executed on Node 24.14.0 without changing sandbox permissions. An attempted elevated test retry was not executed because automatic approval review returned HTTP 503. This was a service availability failure, not a determination that the command was unsafe.

Start the backend in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.locus_x.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal (tested sandbox command, Node 24.14.0):

```powershell
npm.cmd run dev -- --port 5173 --strictPort --configLoader native
```

Open http://127.0.0.1:5173/. The regular documented frontend command omits `--configLoader native`; it was attempted and blocked in this audit’s sandbox. For the built application, the same backend serves http://127.0.0.1:8000/ when `dist` exists at startup.

## Integration evidence

The live API verification reused the audit test assertions with an HTTP client against the freshly started server on port 8000. All nine live behavior cases passed: four malformed scenario cases, duplicate anchors, fault controls, default reset, custom reset, and degenerate/outage handling. Health, environments, OpenAPI, API documentation, built HTML, icon, and the HTML's entry JavaScript/CSS assets returned HTTP 200. Raw results and a real before/after noise frame are saved in `artifacts/integration-audit/live-http-results.json`.

- **Genuine updates:** at a fixed simulation timestamp, changing noise, disabling A1, changing NLOS bias, toggling walls and applying combined stress changed observations and both baseline/robust positions. Revisions advanced, and each solver’s residual anchor set matched the available observation IDs.
- **Fair inputs and truth isolation:** an instrumented test captured identical observation lists passed to both estimators. Holding ranges fixed while moving simulator truth to `(200, 200)` and replacing fault labels left baseline, robust and tracking results unchanged, apart from measured runtime. Evaluation errors changed. The estimator accepts only anchor IDs/coordinates, timestamps and observed ranges, with robust mode additionally receiving historical residual-consistency weights.
- **Errors:** tests independently recompute Euclidean distance from returned positions to `evaluation.ground_truth`; unavailable positions require null errors. Frontend source renders these values and computes its session RMSE from the returned errors. No frontend error generator or fabricated performance counter was found.
- **Coordinates:** source inspection and executed frontend tests confirm `(x, y)` maps to `(x, elevation, -y)` for configured anchors and route waypoints. Rendered marker placement has not been visually rechecked during this audit.
- **Reset:** restores session-creation settings, t=0, frame index 0, range observations and initial tracking covariance. Normal defaults: NLOS, seed 12, `asset_round`, 1.2 m/s, eight anchors enabled, walls enabled, noise 0.15 m, bias 2.5 m, packet loss 0. Custom API sessions restore their custom creation settings. Route/speed/seed edits restart time while preserving the edits; they do not replace saved reset settings. Revision remains monotonic.
- **Unavailable states:** collinear anchors return `poor_geometry` and null solver positions. Total loss after a previous fix reports degraded prediction, increasing covariance, then unavailable/null position and ellipse after the three-second timeout. No initial fix is invented when geometry is invalid.
- **Controls/dependencies:** source inspection found handlers for simulation, fault controls, navigation, overlays, camera, diagnostics, exports and benchmark controls. All direct frontend dependencies have runtime or toolchain uses; Python pins support runtime, framework compatibility or checks. No unused dependency was removed. This source review does not establish browser interaction success.

## Remaining issues, sorted by severity

1. **High validation gap — browser audit pending.** The approval service prevented fresh UI interaction, console inspection, export interaction, visual coordinate checks and screenshot capture. Backend/API behavior is verified, but this report does not assert every visible control works in the updated app. Restore browser tool approval and complete that pass before calling the entire audit complete.
2. **Medium — robustness and covariance limitations.** The existing reference benchmark shows worse robust/filter RMSE under broad positive NLOS bias and undercoverage of the nominal 95% ellipse. The original eight-anchor NLOS RMSEs were 1.540 m baseline and 1.968 m proposed, with 49.4% ellipse coverage. These are prior measured results, not a new benchmark run. Correcting the estimator/calibration model is outside this integration-only change.
3. **Low — scene bundle size.** Production build emits an advisory for the 898.12 kB scene chunk (241.52 kB gzip). The build passes; no new loading-time or rendering-performance claim is made.
4. **Low — upstream test-client deprecations.** Starlette/httpx and AnyIO warnings remain; all backend tests pass.
5. **Low environment limitation — default Vite configuration loading in the sandbox.** Standard test/dev commands failed before running application code. The documented native-loader fallback passed under the same sandbox.

No remaining critical or high application defect was reproduced in the executed checks. The browser validation gap prevents a full integration sign-off. The application remains a synthetic, single-floor, single-worker tool with in-memory sessions; no real-radio or public deployment validation was performed. The full 8,960-frame reference benchmark and first-time installation were not rerun in this audit.

## Saved evidence and exact changed files

Modified application files: `backend/locus_x/schemas.py`, `backend/locus_x/session.py`, `backend/locus_x/estimators.py`, `src/App.tsx`, `src/Diagnostics.tsx`.

Modified documentation: `README.md`, `docs/architecture.md`, `docs/methodology.md`, `docs/build-status.md`.

Added: `backend/tests/test_audit.py`, `docs/integration-audit.md`, and the following files under `artifacts/integration-audit/`: `live-http-results.json`, `server-results.json`, `check-results.json`, `coverage.json`, `benchmark-metrics.csv`, `changed-files.json`.

The production build regenerated `dist/`; the exact generated filenames are listed in `changed-files.json`. Coverage and normal compiler/test caches also refreshed. This workspace has no Git metadata, so the manifest records the files edited or generated during the audit rather than a Git diff.

`benchmark-metrics.csv` is a verified byte-for-byte copy of the latest saved 56-row reference CSV, with its source timestamp and SHA256 recorded in `check-results.json`. The original `artifacts/benchmark/metrics.csv` and `results.json` remain unchanged. This preservation is not a newly executed benchmark.

Final server checks also returned HTTP 200 for the Vite page, transformed frontend entry source, API health/environment proxy routes, and the built Three.js scene chunk; see `server-results.json`. These are HTTP checks and do not establish browser execution or visual correctness.
