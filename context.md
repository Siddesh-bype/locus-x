# LOCUS-X — project context

One-page orientation for anyone opening this repository: what the project is, which
document answers which question, where the code lives, and how to reproduce the numbers.
Detail stays in the linked documents; this file is the index, not a second source of truth.

## What this is

LOCUS-X is a local research workspace for indoor positioning, built by Epoch_12 (VIT Pune)
for CODE APEX 3.0, Problem Statement 2. A fictional three-floor hospital is shared by a
synthetic ranging simulator, statistical localizers, an evaluation pipeline and a
React/Three.js digital twin. No radios, no cloud, no credentials: one machine, one set of
JSON configs, REST in between.

Three generations coexist in the tree:

| Generation | Scope | Entry points |
| --- | --- | --- |
| Reference (V1) | one floor, 2D on a known floor, frozen benchmark | `configs/environments/hospital_floor_01.json`, `backend/locus_x/{session,estimators,evaluation,benchmark}.py`, `docs/build-status.md` |
| V3 foundation | three-floor building, manually selected known floor, still 2D | `configs/environments/hospital_v3.json`, `backend/locus_x/building.py`, `docs/v3-*.md` |
| Multi-floor | continuous 3D ranges, vertical estimate, inferred or route-assisted storey | `backend/locus_x/multifloor/`, `docs/multifloor-*.md` |

The reference generation is frozen: `hospital_floor_01.json` stays byte-identical and its
benchmark reproduces, `sha256(Environment.model_dump_json()) = fa1d5b05…760c4b39`.

## Reading order

- **New to the project** — `README.md` → this file → `docs/architecture.md` → `docs/methodology.md`
- **Judging or presenting** — `docs/multifloor-status.md` (evidence and limits) → `docs/assumptions-limitations.md` → `docs/presentation-prep/`
- **Integrating the API** — `docs/multifloor-contract.md` → `docs/v3-contract.md`
- **Contributing** — `CONTRIBUTING.md` → `docs/v3-handoff.md`
- **Resuming agent work** — `.superpowers/sdd/multifloor-positioning/progress.md` (local only, not committed)

## Documentation map

| Path | Question it answers | State |
| --- | --- | --- |
| `README.md` | What is this, how do I run it | Current |
| `CONTRIBUTING.md` | Branch, honesty and evaluation rules | Current |
| `docs/multifloor-status.md` | What was executed, with which command, and what the numbers mean | **Current — authoritative for multi-floor claims** |
| `docs/multifloor-contract.md` | Additive multi-floor session API, fields, invariants | Current |
| `docs/multifloor-audit.md` | Single-floor assumptions, migration and rollback record | Historical (Phase 1) |
| `docs/v3-status.md` | Executed V3 checks and known gaps | Current for the V3 foundation, superseded for multi-floor |
| `docs/v3-contract.md` | Building/floor configuration, coordinates, validation | Current |
| `docs/v3-audit.md` | Repository audit that started V3 | Historical (19 Oct) |
| `docs/v3-handoff.md` | Workstreams, owners, integration points | Current for ownership |
| `docs/architecture.md` | Module map and data flow | Current, with historical algorithm sections |
| `docs/methodology.md` | Range model, seeding, estimator, metrics | Current |
| `docs/assumptions-limitations.md` | What must not be claimed | Current |
| `docs/anchor-optimizer.md` | Anchor-placement optimizer and its protocol | Current (PR #1) |
| `docs/build-status.md` | Reference build record | Historical (15 Oct) |
| `docs/integration-audit.md` | Integration audit findings | Historical (15 Oct) |
| `docs/presentation-prep/01`–`09` | Problem statement, architecture, benchmark audit, demo script, speaker notes, judge Q&A, team briefing, crash course, revision sheet | Presentation material |
| `APEX_PPT_2.0.pptx` | Slide deck used with the presentation-prep notes | Binary, 17 MB |

Where two documents disagree about multi-floor behaviour, `docs/multifloor-status.md` and
the code win; the V3 documents describe the state before the multi-floor branch.

## Code map

| Path | Contents |
| --- | --- |
| `backend/locus_x/main.py` | FastAPI app, REST endpoints, serves `dist/` when built |
| `backend/locus_x/schemas.py` | Strict request/response models, presets, the only schema source |
| `backend/locus_x/session.py` | In-memory 2D session, advance/controls/benchmark |
| `backend/locus_x/{simulation,estimators,tracking,evaluation,benchmark}.py` | Reference simulator, estimators, tracker, metrics, CLI |
| `backend/locus_x/{building,geometry,environment}.py` | V3 floor composition, route/region validation, config loading |
| `backend/locus_x/multifloor/` | 3D simulation, vertical estimator, floor state, transitions, protocol CLI |
| `backend/locus_x/optimization/` | Anchor-placement optimizer (pure computation, JSON in/out) |
| `backend/tests/` | 11 test modules, including `test_multifloor_*.py` and `test_api.py` |
| `src/` | React/TypeScript UI; `src/scene/` holds the R3F renderer, overlays and camera |
| `src/workspace.ts` | Overlay geometry, exploded-view lifting, floor helpers, caption text |
| `scripts/` | `build_hospital_v3.py` (authoring recipe), `verify_v3_http.py`, `probe_vertical_observability.py`, `run_placement_experiment.py` |

## Configurations

| File | Purpose |
| --- | --- |
| `configs/environments/hospital_floor_01.json` | Frozen reference floor; never edit |
| `configs/environments/hospital_v3.json` | Three-floor hospital (`hospital_v3`, schema 3.1) |
| `configs/benchmarks/competition.json` | Frozen reference benchmark protocol |
| `configs/benchmarks/v3/multifloor-inferred.json` | 7 × 5 × 2 × 240 protocol, inferred identity |
| `configs/benchmarks/v3/multifloor-route-assisted.json` | Same matrix, route-assisted identity |
| `configs/benchmarks/v3/*-smoke.json` | Small per-floor smoke runs |
| `configs/environments/layouts/` | Optimizer-produced anchor layouts (not installed anchors) |

## Commands

Run the app (two processes; Vite proxies `/api` to the backend):

```powershell
& ".\.venv\Scripts\python.exe" -m uvicorn backend.locus_x.main:app --host 127.0.0.1 --port 8000
npm.cmd run dev -- --port 5173 --strictPort
```

Then <http://127.0.0.1:5173> (UI) and <http://127.0.0.1:8000/docs> (API).

Verification gate (what a task must pass before it is called done):

```powershell
& ".\.venv\Scripts\python.exe" -m pytest -q
& ".\.venv\Scripts\python.exe" -m pytest -q --cov=backend/locus_x --cov-report=term-missing
& ".\.venv\Scripts\python.exe" -m ruff check backend/locus_x backend/tests scripts
& ".\.venv\Scripts\python.exe" -m ruff format --check backend/locus_x backend/tests scripts
npm.cmd run typecheck
npm.cmd run lint
npm.cmd test
npm.cmd run build
```

Reproduce the evidence (each multi-floor run takes about 9–11 minutes and needs its own
output directory):

```powershell
& ".\.venv\Scripts\python.exe" -m backend.locus_x.benchmark --config configs/benchmarks/competition.json --output artifacts/multifloor-verify/reference-reproduction
& ".\.venv\Scripts\python.exe" -m backend.locus_x.multifloor.benchmark --config configs/benchmarks/v3/multifloor-inferred.json --output artifacts/multifloor-benchmark-runs/inferred
& ".\.venv\Scripts\python.exe" -m backend.locus_x.multifloor.benchmark --config configs/benchmarks/v3/multifloor-route-assisted.json --output artifacts/multifloor-benchmark-runs/route-assisted
& ".\.venv\Scripts\python.exe" scripts/verify_v3_http.py --url http://127.0.0.1:8021 --output artifacts/multifloor-verify/live-api.json
```

## Evidence and state

`artifacts/` and `.superpowers/sdd/` are gitignored: benchmark outputs, screenshots, review
packages, task briefs and the SDD ledger live only in the working copy. The durable record
is the printed tables in `docs/multifloor-status.md`; every benchmark number there is
reproduced by a command in that document.

| Run | `config_sha256` | `environment_sha256` |
| --- | --- | --- |
| Multi-floor inferred | `fd5c9f57…0c1fb2` | `7af0c587…2fd33b9` |
| Multi-floor route-assisted | `f055b900…9a954c` | `7af0c587…2fd33b9` |
| Reference reproduction | n/a (legacy `config` block) | `fa1d5b05…760c4b39` |

## Current results (multi-floor protocol, 16,800 frames per mode)

| Metric | Inferred | Route-assisted |
| --- | --- | --- |
| Horizontal RMSE / P95 | 0.493 / 0.955 m | same observations |
| Vertical RMSE | 0.572 m | same observations |
| Floor accuracy | 0.552 (6,603 / 11,970) | `n/a` (configured) |
| Transition success | 80 / 126 | 126 / 126 (freshness by construction) |
| Ambiguity rate | 0.561 | 0.287 |

These are synthetic-data results. Read the caveats in
[`docs/multifloor-status.md`](docs/multifloor-status.md) before quoting them: the slab/NLOS
term is a simulator setting, floor probabilities are likelihoods rather than calibrated
posteriors, coplanar anchors cannot name a floor, and route-assisted identity is
configuration.

## Known defects and open items

- `2D baseline —m`: the diagnostics header appends a unit to an unavailable value in every
  multi-floor session (table cells are correct). Cosmetic, recorded in the status document.
- One `exiting` and one `reacquiring` frame per route-assisted traverse return
  `connection_id: null`, so the UI says "no route witness" for those two frames although the
  route is configured.
- Recovery is `0.000` everywhere because the mandated outage removes four anchors per floor,
  not all ranges; it demonstrates wiring, not blackout recovery.
- The full "not implemented" list lives at the end of `docs/multifloor-status.md`.

## Repository state

- Development branch: `feat/multifloor-positioning` (multi-floor work merged upstream as PR
  #2; documentation follow-ups ride on this branch).
- Remotes: `origin` = `Siddesh-bype/locus-x` (fork), `upstream` =
  `satyamshrivastav955-dotcom/locus-x` (canonical, PR target), `optimizer` =
  `Priyanshaur/locus-x` (PR #1 source).
- Git identity and commit rules are in `CONTRIBUTING.md` and the repository owner's agent
  notes: feature branches only, no commits to `main`, no history rewrites without
  confirmation.
