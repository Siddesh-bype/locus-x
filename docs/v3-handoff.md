# V3 team handoff

## Shared foundation and ownership

All workstreams consume `configs/environments/*.json`, the `Building`/`Floor` contracts and the coordinate convention in [v3-contract.md](v3-contract.md). Preserve V1 and the existing statistical estimator as comparison methods. The current runtime is REST, in-memory sessions, one worker and synthetic 2D ranging on a known floor.

| Workstream | Suggested owner / isolated scope | Integration point |
| --- | --- | --- |
| Reliability classifier | Applied ML: new `backend/locus_x/ml/`, training scripts, model bundle and model tests | Consume an explicitly versioned inference-feature object derived from `Measurement` and causal estimator history |
| Anchor optimizer | Infrastructure: new `backend/locus_x/optimization/`, candidate/layout evaluation and tests | Produce a validated building configuration with real selected anchor coordinates and a new layout/version ID |
| Expanded evaluation | Evaluation: new versioned benchmark configs, shared observation recorder, metrics and reports | Run every method/layout on identical observations, with frozen split manifests and configuration/model hashes |
| Floor transitions | Simulation/tracking: route connection events, floor-state logic and tests | Replace manual reset-only switching with explicit transition handling; keep selected-floor 2D mode available |

Coordinate public-schema changes through one integration owner. Prefer new modules and opt-in fields to editing the legacy inference implementation. UI work can add actual completed method/layout results once those interfaces are implemented and tested. Do not add enabled placeholder actions.

## 1. Trained reliability model

Deliver a lightweight supervised classifier or reliability predictor with:

1. A saved run manifest identifying environment/version/hash, floor, layout, route, seed, fault settings and sampling schedule. Freeze grouped train/validation/test assignment before model selection. Keep complete correlated runs together; prevent related observations from the same underlying seed/layout stream crossing splits.
2. A versioned feature schema with names, units, ordering, missing-value handling, preprocessing and causal window rules. Candidate features include observed range changes, time since receipt, known anchor geometry, current residuals from a declared preliminary solve, and past residual/innovation statistics. A preliminary solver's runtime is part of model latency.
3. Labels joined **only during offline training/evaluation** from simulator fault labels. Never include target truth, true range, injected bias/error magnitude, `FaultLabel.kind`, future observations, the target route or future trajectory in inference features. Packet reception/missingness may be observed causally; injected failure causes may not be exposed as features.
4. Preprocessing fitted on the training partition only; saved weights and preprocessing; inference code with explicit failure/unsupported-schema fallback to the unchanged statistical path; deterministic model metadata and checksums.
5. Validation-selected thresholds/calibration, then a frozen held-out evaluation including unused seeds and scenarios/layouts. The reference seeds 101/202 and foundation smoke seeds 17/29 are already inspected; they are not unseen ML test evidence. The existing research protocol has separately reserved/used seeds documented in its own manifest.

Acceptance: baseline, current statistical LOCUS-X and AI-assisted LOCUS-X operate on the same recorded observations. Changing truth/labels without changing those observations cannot change either inference output. Report classifier precision/recall/PR curves and downstream RMSE, tails, matched-frame error, valid/fresh rates, calibration and latency. A better classification score alone does not establish better localization.

Only then expose a third method in the comparison UI. Add an optional actual method result/registry adapter; preserve existing `baseline`, `robust` and `tracking` fields for old clients and reference runs.

## 2. Anchor placement optimizer

Input: a validated building/floor, independent `candidate_anchors`, fixed budget K, movement-region coverage targets, frozen route/scenario/seed protocol and installation constraints. The current 21 candidates per floor are possible sites, not certified mount points.

Evaluate candidate subsets using actual coordinates, geometric observability, coverage, wall intersections and specified packet/outage conditions. Use an auditable objective combining error/tails, valid/fresh-update rates and resilience under faults. Separate optimization seeds/routes from final evaluation. Compare to the existing installed layout at the **same budget** and hold geometry, faults and observations/seed policy fixed.

Output: selected candidate IDs, objective/constraint results, protocol hash and a new real configuration. Replace the relevant floor's installed `anchors`, assign a new `anchor_layout_id`, preserve other floors/geometry, update the environment ID/version and save the mapping between installed IDs and selected candidate IDs. The output must pass `Building` validation and load through the existing catalog/session/scene path.

The current reference benchmark uses named A1–A8 subsets. Add an explicit-layout evaluation mode for optimizer experiments; retain the named-subset protocol unchanged. Do not report an optimizer result by merely changing anchor display positions or recomputing a geometry score without localization evaluation. Add an Optimize action only with an actual bounded job/progress/result API and usable output configuration.

## 3. Expanded evaluation and calibration

Preserve `configs/benchmarks/competition.json` and `artifacts/benchmark/`. The V3 smoke configurations are functional checks, not a new claim that the algorithms improve.

Add versioned, prespecified protocols spanning floors, unseen layouts, anchor budgets, fault strengths, long outages and routes that exercise turns/stops/rooms/core lobbies. Record observation-stream hashes and use shared observations for method comparisons. Store complete per-frame records with run/floor/layout identity, method availability and fresh-update flags. Keep training, tuning, covariance calibration and final evaluation separated by full runs.

Report per-floor/per-scenario as well as pooled results. Include RMSE/median/P95, valid/fresh-update rates, matched-frame errors, error during prediction-only intervals, latency distributions, empirical coverage and uncertainty-region size. Use run-level resampling for uncertainty in reported differences; correlated frames are not independent trials.

Known regressions to explain rather than hide: broad NLOS and combined-fault underperformance, perfect-measurement filter lag, and undercoverage of the nominal 95% ellipse. Covariance calibration must be fitted independently and versioned. Keep raw and calibrated results distinguishable. Real-radio validation requires timestamped physical observations and independently measured reference positions.

## 4. Floor transition and vertical positioning logic

Current floor selection is a manual, revision-safe reset of independent 2D runs. The aligned `connections`/`access_cores` provide topology only. There is no inferred floor, continuous Z state, cross-floor ranging or vertical uncertainty.

Stage the extension:

1. Define a building route with per-waypoint floor identity and explicit entry/traversal/exit events through valid connection stops. Specify travel/dwell time for stairs and elevators. Test movement connectivity and vertical alignment.
2. Specify observation semantics during a transition: visible anchors by floor, inter-floor attenuation and range geometry. A 3D distance model changes the measurement contract and must use a new protocol/version. Do not insert floor truth into a learned floor selector.
3. Define state handoff: reset, transform or maintain a documented joint state/covariance at each transition, including missing observations and ambiguous floor states. Avoid teleporting a trusted 2D state into a different floor.
4. Expose floor-source status (manual/configured/inferred), transition state, Z availability and its uncertainty. Claim full multi-floor positioning only after the estimator, observation model, state handoff and evaluation are integrated.

Acceptance includes ascending/descending stairs, lift dwell/outage, rejected transitions, reacquisition, incorrect-floor hypotheses and replay with the same observations. Keep the known-floor 2D reference path reproducible.

## UI and verification follow-up

The new controls are implemented against actual data: floor/environment selection, architectural views, camera actions, scene layers, fault/anchor/route/seed controls, comparisons, exports and benchmark jobs. Missing future interactions are intentional: no AI result, optimizer action, automated floor tracking, persistent experiment library, real-radio ingestion or benchmark cancellation.

This environment exposed **no browser surface** to the computer-use tool, so desktop/mobile screenshots, WebGL rendering, real DOM clicks, keyboard focus and GPU performance still need visual acceptance. Unit/SSR contracts and live HTTP tests are documented separately; they do not substitute for that check. Test at 1440/1366/1024/390 px, inspect all three floors and both building modes, zoom/pan/fit, every layer, floor changes while running, refresh/reconnect, missing fixes, exports, benchmark completion and WebGL failure. Use the existing application and real backend for this acceptance pass.
