# Build status — 15 October 2026

## Latest: V3 foundation, 19 October 2026

The current implementation and executed verification are in [V3 status](v3-status.md). The V3 branch adds a versioned three-floor environment, known-floor 2D sessions, architectural inspection/layer controls and real comparison metrics. The original benchmark reproduced exactly. Browser visual acceptance remains pending because no browser surface was available in the implementation environment.

The remainder of this file is the preserved **15 October reference-build record**, not a claim about the current V3 browser verification.

## Final integration audit update

The audit fixes malformed scenario handling, duplicate disabled anchors, numerical initialization failures, reset semantics, obstruction preset activation and misleading status text. Working application code is preserved; no redesign was performed.

Executed after these code fixes: **39 backend tests passed (94% coverage)**; Ruff lint/format, TypeScript, ESLint, Python dependency consistency and production build passed. **Two frontend tests passed** with `npm.cmd test -- --configLoader native` on Node 24.14.0. Default test/dev commands encountered a sandbox directory-read error; Vite's supported native configuration loader succeeded within the sandbox.

Both servers are running. Backend startup used the regular README command; frontend startup used `npm.cmd run dev -- --port 5173 --strictPort --configLoader native`. Fresh live HTTP checks passed for fault-driven measurements and estimator updates, reset, invalid input, degenerate/outage states, built assets and the frontend API proxy.

**Browser interaction checks are blocked:** automatic approval review returned HTTP 503. No new browser verification or screenshot is claimed. At the user's request, this run concludes with that gap recorded rather than waiting for service recovery. Prior browser checks below are historical and are not evidence for the latest edits.

Current evidence: [integration report](integration-audit.md), [check results](../artifacts/integration-audit/check-results.json), [coverage](../artifacts/integration-audit/coverage.json), [live HTTP results](../artifacts/integration-audit/live-http-results.json), [server results](../artifacts/integration-audit/server-results.json), and [preserved benchmark CSV](../artifacts/integration-audit/benchmark-metrics.csv). The benchmark CSV is a byte-for-byte copy of the latest saved reference run; the full benchmark was **not rerun** during this audit.

Remaining issues, highest priority first: browser validation gap; known NLOS accuracy/covariance limitations; scene-bundle advisory and upstream deprecations. Exact changed files and commands are in the integration report and [file manifest](../artifacts/integration-audit/changed-files.json).

## Original build record

The sections below preserve the original build's delivered scope and executed checks.

## Delivered

This was an empty workspace without a Git repository. All application files are newly created; no existing user implementation, environment file, asset or secret was overwritten. No commit or push was made.

| Files / directory | Delivered work |
|---|---|
| `backend/locus_x/schemas.py`, `environment.py`, `simulation.py` | Validated floor and controls, deterministic routes, noise, NLOS, dropout and outages |
| `backend/locus_x/estimators.py`, `tracking.py`, `evaluation.py` | Tested baseline, robust range weights, Kalman tracking, uncertainty and independent errors |
| `backend/locus_x/session.py`, `main.py` | Authoritative sessions, locks, revisions, reset, local API and asynchronous benchmarks |
| `backend/locus_x/benchmark.py` | Standalone saved-config evaluation and CSV/JSON outputs |
| `backend/tests/test_core.py`, `test_api.py` | Numerical, geometry, fault, reproducibility, truth-boundary and API tests |
| `configs/environments/hospital_floor_01.json` | Shared 30 × 20 m fictional hospital with door gaps, eight anchors and two routes |
| `configs/benchmarks/competition.json` | Seven scenarios, four anchor counts, two held-out seeds and 160 frames per run |
| `src/App.tsx`, `Controls.tsx`, `Diagnostics.tsx`, `useSession.ts`, `types.ts` | Real control/telemetry workflow and typed frontend session state |
| `src/Scene.tsx`, `Charts.tsx`, `Benchmark.tsx` | Interactive procedural 3-D, signal/trajectory/ellipse overlays, live and completed experiment charts |
| `src/lib.ts`, `lib.test.ts`, `main.tsx`, `styles.css`, `public/locus.svg` | Coordinate mapping, exports, history tests, local fonts and responsive visual design |
| `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `eslint.config.js`, `index.html` | Frontend toolchain and deterministic installation |
| `requirements.txt`, `requirements-lock.txt`, `pyproject.toml`, `.gitignore` | Pinned Python environment, test/lint configuration and ignored generated files |
| `README.md`, `CONTRIBUTING.md`, `docs/*.md` | Setup, architecture, methods, assumptions, contribution guidance and this report |
| `artifacts/benchmark/metrics.csv`, `results.json` | Actual reproducible benchmark output, not a hand-authored accuracy table |
| `artifacts/screenshots/` | Browser captures from live verification |

## Executed checks

- **27 Python tests passed; 93% statement coverage** over `backend.locus_x`.
- Ruff lint and formatting checks passed.
- TypeScript type check passed.
- ESLint passed.
- **2 frontend tests passed**, covering every configured anchor/waypoint coordinate mapping and reset/recomputed/missing sample behavior.
- Production frontend build passed; React application and Three.js scene load as separate bundles.
- Full CLI benchmark completed: **8,960 evaluated frames and 56 method/scenario/count rows**. The final isolated-environment run took **227.4 seconds**, with the live app also exercised during that run. Saved outputs include configuration, environment hash, traces, distributions, RMSE, median, P95, valid/update rates, fault-conditioned errors, runtime and coverage.
- Live browser flow verified: start, target motion, combined-failure injection, changed estimator output, degraded tracking, pause, reset, anchor toggle, noise adjustment, wall propagation toggle, route/speed/seed changes, 3-D and plan views.
- Browser benchmark completed from the interface and anchor-count selection updated the plots. Exported CSV was downloaded and contained the expected eight rows for one scenario and four anchor counts.
- A 390 px viewport was checked. An initial horizontal overflow was fixed; the retest reported a 375 px document content width within a 390 px viewport (scrollbar accounts for the difference). The temporary viewport override was reset.
- API health and the single-server built frontend both returned HTTP 200. Backend and frontend were launched locally.

During setup, the sandbox blocked package downloads and the frontend build helper's directory reads. Approved runs completed those checks. A temporary environment inherited unrelated global package conflicts; it was replaced with an isolated `.venv`, and `pip check` then reported **No broken requirements found**. Global packages were not altered.

Remaining toolchain notices: Starlette's test client emits deprecation warnings for its current httpx adapter and an AnyIO alias; tests pass. Vite reports the Three.js scene bundle is over its 500 kB advisory threshold (about 242 kB gzipped); the scene is already loaded separately. These notices do not prevent startup or the verified workflow.

## Exact launch commands

From the project root, run these in separate terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.locus_x.main:app --host 127.0.0.1 --port 8000
```

```powershell
npm.cmd run dev -- --port 5173 --strictPort
```

Open http://127.0.0.1:5173. For a built demo, run `npm.cmd run build`, restart the backend, then open http://127.0.0.1:8000.

## Findings, limits and next priorities

LOCUS-X helps in some noise and packet-loss scenarios but underperforms the baseline in the included broad NLOS and combined-stress tests. At eight anchors, NLOS RMSE was about 1.97 m versus 1.54 m baseline; empirical coverage of the nominal 95% ellipse was 49.4%. The reported table and charts preserve that result.

This is synthetic validation on one layout. It does not establish real-world positioning accuracy, calibration, hardware compatibility or production deployment readiness. A learned classifier, real UWB/BLE ingestion, additional layouts, historical event replay and persistent experiment storage remain unimplemented extensions.

Highest priorities are inference-only mitigation of majority-positive-bias failure, covariance calibration on new held-out layouts, and timestamped real-radio ingestion with independent ground truth. See `assumptions-limitations.md` for the precise scope.
