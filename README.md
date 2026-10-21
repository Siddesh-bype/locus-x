# LOCUS-X

**V3 foundation: a hospital digital twin and reproducible indoor-positioning workspace.**

Built by Epoch_12, Vishwakarma Institute of Technology, Pune, for CODE APEX 3.0, Problem Statement 2. A fictional three-floor hospital shares its configuration with the range simulator, statistical localization engine and evaluation pipeline. The original single-floor environment, estimators and reference results remain available. Runs locally with React/TypeScript/Three.js and Python/FastAPI; no credentials or external services.

V3 implements **2D localization on a manually selected, known floor**. Building height is configured, not estimated. A trained LOS/NLOS reliability model now feeds a third method, but anchor optimization and integrated floor transitions remain subsequent phases. See the [AI reliability report](docs/ai-reliability.md), [audit](docs/v3-audit.md), [coordinate/configuration contract](docs/v3-contract.md), [team handoff](docs/v3-handoff.md), and [executed checks and limitations](docs/v3-status.md).

## Run locally

Tested on Windows with Python **3.13.13** and Node **24.14.0**. Use Python 3.13 and Node 22.12+ (or 24). Run commands from this directory.

First-time setup, PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
npm.cmd ci
```

Start the backend in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.locus_x.main:app --host 127.0.0.1 --port 8000
```

Start the frontend in another terminal:

```powershell
npm.cmd run dev -- --port 5173 --strictPort
```

In the restricted Codex Windows sandbox, the default Vite configuration bundler can fail with `Cannot read directory "../..": Access is denied`. With the tested Node **24.14.0**, use the supported native configuration loader, which keeps execution inside the sandbox:

```powershell
npm.cmd run dev -- --port 5173 --strictPort --configLoader native
```

Open **http://127.0.0.1:5173**. FastAPI documentation is at **http://127.0.0.1:8000/docs**. No environment variables are required. Vite proxies `/api` to the local backend. Use `python`, not the Windows `py` launcher, if the latter cannot find your installation. `requirements.txt` pins direct dependencies; `requirements-lock.txt` records the complete tested Python environment. The npm lockfile records frontend dependencies.

On macOS/Linux use `python3 -m venv .venv`, `.venv/bin/python` in place of the Windows Python path, and `npm` in place of `npm.cmd`. That workflow is documented but has not been exercised on those operating systems.

For a single-server demo, run `npm.cmd run build`, then start/restart the backend and open **http://127.0.0.1:8000**. The backend serves `dist` when that directory exists at startup. Keep one backend worker: session storage is in memory. This is a local research tool, not a public multi-user deployment.

## Use the laboratory

1. The browser opens **Meridian Hospital**, a fictional 42 × 28 m building with Ground, First and Second floors at 0, 4.2 and 8.4 m. The environment menu also loads the original 30 × 20 m reference floor.
2. Inspect **Floor**, **Building** or **Exploded** views. **Plan** isolates the selected floor. Drag to orbit, right-drag to pan, scroll or use buttons to zoom, and use Fit/Expand for inspection. Floor selection pauses and starts a fresh 2D run; building/view switches only change presentation.
3. Choose a scenario and press **Run experiment**. Adjust noise, NLOS bias, packet loss, wall range bias and individual anchors. Sliders commit on release. Routes and route names come from the selected floor configuration.
4. Compare the dark ground-truth cart, amber baseline, teal LOCUS-X estimate and violet LOCUS-X + AI estimate. **Scene layers** controls room zones/equipment, corridors, labels, walls/doors, access cores, anchors, configured candidate sites, the planned route, trajectories, simulated signals and the nominal ellipse. Geometry visibility is independent of the **Wall range bias** simulation setting.
5. The live comparison shows current error and window RMSE/median/P95, valid/fresh-update rates, measured latency and empirical ellipse coverage for all three methods. Missing estimates stay missing; baseline coverage is unavailable. **Diagnostics** adds residuals, velocity, geometry condition, AI per-anchor p(LOS), events and API-replayable **Scenario JSON**. **Export CSV** saves actual samples, floor identity, errors, update flags, runtime, coverage and positions for every method.
6. **Reset restores the settings captured when the session was created**, clears tracking/history, and returns the target to t=0. For a normal browser session these are: wall-obstruction scenario, seed **12**, `asset_round` route, speed **1.2 m/s**, all **8 anchors enabled**, walls enabled, noise σ **0.15 m**, NLOS bias **2.5 m**, and **0%** packet loss. Sessions created with custom API settings restore those custom initial values. Changing route, speed, or seed restarts time with the edited settings; it does not change the saved reset values. Selecting wall obstruction or combined stress also enables wall propagation.
7. Open **Benchmarks**, choose the reference environment or a V3 floor, and run a selected scenario or the full protocol. All three methods consume identical observations. These experiments are independent of the live session. Export metrics CSV or the complete versioned JSON result. The protocol still uses named A1–A8 subsets.

The browser requests fixed 0.2-second simulation steps in a serial HTTP loop. Simulation time is independent of wall-clock rendering time. Pausing lets an already in-flight frame finish. Reloading reconnects to the same session and floor where possible, with a fresh plot window. History retains at most 600 samples and clears on configuration changes. Fault edits retain tracker history, so this window is diagnostic; use Benchmarks for controlled runs from initialization. No telemetry is fabricated in the frontend.

## Implemented

- Versioned 42 × 28 m, three-floor hospital: arrival/emergency/outpatient services; wards/nursing/treatment; ICU/theatres/recovery. Aligned stair and bed-lift cores, real wall gaps, walkable-region references, eight installed anchors and 21 independent candidate sites per floor. The original 30 × 20 m environment is unchanged.
- Seeded Gaussian range noise, positive wall bias, independent packet loss, manual disable, periodic anchor failures.
- Geometry-aware nonlinear least-squares baseline; Huber loss and smoothed residual consistency weights.
- Constant-velocity Kalman filter, innovation rejection, prediction-only status, growing outage uncertainty, three-second timeout and reacquisition.
- Interactive Three.js/React scene: floor/building/exploded modes, orbit/pan, plan, fit/zoom/expand, geometry and experiment layers. Geometry is instanced; rendering idles when nothing changes. DPR is capped at 1.5.
- Trained MLP LOS/NLOS classifier with Platt calibration, run-level train/validation/test splits, inference-safe features, validation-only weight policy and optional map geometry. It acts as a reliability adapter; the statistical robust estimator remains the fallback.
- Pydantic API, revision-safe controls/reset, serial client updates, bounded in-memory sessions and background benchmark worker.
- Independent CLI benchmark, error curves/CDF, CSV/JSON exports, schema/topology/floor/session/UI-contract tests and documentation. CLI outputs use unique directories by default and refuse to overwrite existing result files.

The learned branch predicts P(NLOS) from observed ranges, geometry and optional map context, then weights the existing robust estimator multiplicatively. It never receives simulator truth, labels, injected bias, fault flags or future samples. The validation-selected policy is conservative and falls back to LOCUS-X when fewer than three anchors are predicted LOS.

To replay an exported scenario's initial settings through the API (after saving the Diagnostics export as `scenario.json`):

```powershell
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/sessions' -ContentType 'application/json' -Body (Get-Content -LiteralPath 'scenario.json' -Raw)
```

This creates a repeatable session at t=0. It does not replay prior manual control events.

## Verify

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=backend.locus_x --cov-report=term-missing -q
.\.venv\Scripts\python.exe -m ruff check backend/locus_x backend/tests scripts
.\.venv\Scripts\python.exe -m ruff format --check backend/locus_x backend/tests scripts
npm.cmd run typecheck
npm.cmd run lint
npm.cmd test
npm.cmd run build
```

For the sandbox configuration-loading error, use `npm.cmd test -- --configLoader native` (Node 24.14.0). A restricted Windows runner may also need access to pytest's temporary directory. `backend/research` contains pre-existing lint/format findings; it is preserved separately from the production checks above. See [V3 status](docs/v3-status.md) for the precise executed results, including the browser-verification gap.

Run the AI dataset/training/evaluation pipeline:

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.ai_training --generate
.\.venv\Scripts\python.exe -m backend.locus_x.ai_training --train
.\.venv\Scripts\python.exe -m backend.locus_x.ai_evaluation --tune-policy --apply-policy
.\.venv\Scripts\python.exe -m backend.locus_x.ai_evaluation --evaluate
```

Run the full independent evaluation:

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/competition.json
```

The saved configuration evaluates 7 scenarios × 4 anchor counts × 2 seeds × 160 frames = **8,960 evaluated frames**, plus initialization frames. New output goes to a timestamped directory under `artifacts/benchmark-runs/` (56 rows). To choose a location, pass a **new** `--output` directory. The reference stays in `artifacts/benchmark/`. Accuracy metrics reproduce for the same code, configuration, seed and numerical-library versions; wall-clock latency does not.

V3 functional checks (360 frames per floor, not an expanded accuracy claim):

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/v3/ground-smoke.json
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/v3/first-smoke.json
.\.venv\Scripts\python.exe -m backend.locus_x.benchmark --config configs/benchmarks/v3/second-smoke.json
.\.venv\Scripts\python.exe scripts/verify_v3_http.py
.\.venv\Scripts\python.exe scripts/verify_v3_http.py --url http://127.0.0.1:5173
```

`configs/environments/hospital_v3.json` is the runtime source for all consumers. `python scripts/build_hospital_v3.py` regenerates that file from its deterministic authoring recipe; it never touches the legacy fixture. Restart the backend after changing configuration.

## AI reliability branch

The trained model and its reproducible evaluation are documented in [docs/ai-reliability.md](docs/ai-reliability.md). The model is a 24-feature, 24-32-16-1 NumPy MLP with Platt calibration. Held-out classification results are F1 **0.9100**, ROC-AUC **0.9895**, Brier **0.0271** and ECE **0.0127**. Model size is 14,307 bytes; the saved adapter benchmark is 0.386 ms mean and 0.434 ms p95 in this Python environment.

End-to-end results are mixed. On the primary held-out protocol, 8-anchor NLOS RMSE fell from **1.9185 m** (LOCUS-X) to **1.6840 m** with AI, **12.2% lower**. NLOS + outage fell from **2.2075 m** to **2.0248 m**, **8.3% lower**. A mixed NLOS + packet-loss 8-anchor case regressed from **2.5941 m** to **2.8481 m**, **9.8% higher**. Three-anchor cases usually fall back unchanged because the model cannot establish three reliable anchors. These are simulator results, not radio validation.

## Actual results and limitations

The completed reference run on the held-out route produced these **8-anchor RMSEs**, in metres, pooled over seeds 101 and 202:

| Scenario | Baseline | LOCUS-X |
|---|---:|---:|
| Clean LOS | <0.000001 | 0.050 |
| Moderate noise | 0.285 | 0.266 |
| High noise | 0.853 | 0.783 |
| NLOS bias | 1.540 | 1.968 |
| Packet loss | 0.216 | 0.214 |
| Anchor outage | 0.160 | 0.155 |
| Combined failures | 3.640 | 3.782 |

**The proposed method does not always win.** The filter introduces lag in perfect measurements. Broad positive bias can make a mutually consistent set of bad ranges dominate reliability weighting. Eight-anchor NLOS coverage of the nominal 95% ellipse was only **49.4%**; combined-failure coverage was **36.3%**. The ellipse is a model-based region, not calibrated confidence. Compare update/availability rates as well as error: prediction-only positions are included until the three-second timeout. Errors for unavailable positions are omitted, not set to zero.

These historical metrics evaluate the original synthetic floor. V3 functional runs are stored separately and do not replace this table. Neither set validates actual UWB/BLE radios, real multipath, correlated packet loss or a real hospital. No hardware compatibility, full multi-floor positioning or public-deployment readiness is claimed.

## Architecture and next work

`configs → simulation → identical range observations → baseline / robust estimator / AI reliability → motion filter → evaluation → API → React/Three.js`

See [architecture](docs/architecture.md), [methodology](docs/methodology.md), [assumptions and limitations](docs/assumptions-limitations.md), [build status](docs/build-status.md), and the [final integration audit](docs/integration-audit.md).

Next: candidate-based anchor optimization, broader validation including mixed-fault policy tuning, floor-transition integration, persistent storage and real radio ingestion remain open. The [V3 handoff](docs/v3-handoff.md) records the earlier workstream split and acceptance criteria.
