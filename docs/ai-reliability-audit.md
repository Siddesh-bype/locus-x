# AI reliability: Phase 0 audit

Audit date: 21 October 2026. This phase was read-only with respect to production source. The only new files are this report and `artifacts/ai-reliability/audit-phase0/latency.json`.

## Verdict

The 24-feature v1 adapter respects the stated inference boundary. The existing evidence is not yet sufficient for the requested final claim: the saved latency is adapter-only, the policy validation omitted the mixed faults that later regressed, the "One NLOS" group is contaminated by coasting frames, several handoff metrics and classifier curves are absent, and no run-level confidence intervals exist.

No Git metadata is present in this checkout. Commit history, diffs and the origin of the inference fix cannot be checked. This is a reproducibility limitation, not evidence that the changes are correct.

## a) Feature truth-boundary audit

Inference enters through `extract_features()` in `backend/locus_x/reliability.py:125`. It receives observations, the static environment, the preliminary baseline estimate and causal `FeatureHistory`. The module imports the estimator, geometry and schema modules; it does not import simulation or evaluation (`reliability.py:18-20`). Rows are created only for observations accepted by `valid_observations()` (`reliability.py:137`, `estimators.py:11`).

| # | Feature | Source | Missing-value rule | Allowed? |
|---:|---|---|---|---|
| 1 | `observed_range_norm` | Observed range / floor diagonal | Absent when observation is absent; no imputation | Yes |
| 2 | `predicted_range_norm` | Distance from preliminary baseline position to anchor / diagonal | No row if preliminary position is unavailable | Yes, preliminary estimate |
| 3 | `residual_m` | Observed minus predicted range | No row without a preliminary fix | Yes |
| 4 | `residual_norm` | Residual / robust frame scale | Scale floor 0.2 m when MAD is zero (`reliability.py:145`) | Yes |
| 5 | `abs_residual_norm` | Absolute value of feature 4 | Same as feature 4 | Yes |
| 6 | `instant_reliability` | Residual consistency, clipped to 0.08-1 | Same as feature 4 | Yes |
| 7 | `anchor_x_norm` | Static anchor coordinate / floor width | None; anchor geometry always present | Yes |
| 8 | `anchor_y_norm` | Static anchor coordinate / floor height | None | Yes |
| 9 | `relative_dx_norm` | Anchor minus preliminary estimate | No row without a preliminary fix | Yes |
| 10 | `relative_dy_norm` | Anchor minus preliminary estimate | No row without a preliminary fix | Yes |
| 11 | `bearing_sin` | Relative vertical offset / distance | No row without a preliminary fix | Yes |
| 12 | `bearing_cos` | Relative horizontal offset / distance | No row without a preliminary fix | Yes |
| 13 | `available_fraction` | Valid observations / installed anchors | Computed from receipt only | Yes |
| 14 | `condition_log` | Preliminary information-matrix condition number | Defaults to 1e6 when absent, then clipped | Yes |
| 15 | `wall_count_scaled` | Static wall intersections between preliminary estimate and anchor | Zero walls is a real value, not missing | Yes, map geometry |
| 16 | `frame_wall_mean_scaled` | Mean of feature 15 across the frame | Zero when no walls cross | Yes, map geometry |
| 17 | `consensus_median_norm` | Median of current normalized residuals | Defined whenever at least three rows exist | Yes |
| 18 | `consensus_mad_norm` | MAD of current normalized residuals | Zero is a valid value | Yes |
| 19 | `observed_to_predicted_delta` | Observed/predicted range ratio minus one | Predicted is floored at 1e-9 | Yes |
| 20 | `residual_sign` | Sign of the current residual | Zero is valid | Yes |
| 21 | `previous_residual_norm` | Same anchor's normalized residual from the last feature-producing frame | Defaults to 0.0 for a newly received or reappearing anchor | Yes, causal history |
| 22 | `residual_delta_norm` | Current normalized residual minus feature 21 | First/reappearing anchor therefore uses zero as the previous value | Yes |
| 23 | `position_delta_norm` | Change since last feature-producing preliminary position / diagonal | 0.0 on first frame | Yes, causal history |
| 24 | `time_delta_s` | Current observation time minus last feature-producing time, clipped to 0-2 s | 0.0 on first frame | Yes, causal history |

The feature vector does not contain true position, true range, injected bias, `FaultLabel.kind`, a fault flag, the target route, future observations or an evaluation result. Packet receipt and absence are observable and are used through the valid-observation set and `available_fraction`.

Two qualifications matter:

1. The wall-count features are computed from map geometry and the preliminary estimate, not from simulator labels. They are allowed, but the simulator generates NLOS from true-position wall intersections. Features 15-16 therefore approximate the label-generating mechanism and may inflate classification performance. The planned ablation must quantify this.
2. Temporal history is replaced, not filled, at every feature-producing frame (`reliability.py:206`). A packet gap resets each reintroduced anchor's previous residual to zero. This is causal but may distort `residual_delta_norm` after loss.

The existing truth-isolation test covers `baseline`, `robust` and `tracking`, but does **not** assert that changed truth or labels leave `ai` and `ai_tracking` unchanged (`backend/tests/test_audit.py:202-237`). There is also no AI-specific unit test for this boundary.

## b) Missing values and causal windows

- No row is manufactured for a missing observation. Packet loss and outage simply reduce the rows and increase `available_fraction`'s denominator effect.
- The whole batch is unavailable if fewer than three valid observations exist, the preliminary estimate is missing, or the preliminary position is missing (`reliability.py:137-139`). The adapter then calls the statistical fallback.
- First-frame temporal values are explicit zeros for previous residual, position delta and time delta (`reliability.py:157-167`, `174`).
- Time delta is clipped to `[0, 2]` seconds and is computed from the previous feature-producing frame, not the previous wall-clock sample.
- `condition_number` defaults to 1e6 before being clipped and log-scaled (`reliability.py:150`, `192`).
- History is advanced only after all rows for a frame have been built (`reliability.py:205-208`). A later observation cannot change an earlier frame's features, but this has not yet been locked down by a dedicated causality test.

## c) Latency scope

The saved 0.386 ms value is **adapter-only**. `measure_inference_latency()` solves the measurements once before the loop and then times only `model.predict()` inside the loop (`ai_training.py:667-684`). It excludes the preliminary solve, the AI-weighted robust solve and tracking.

A repeat on this machine, using the existing v1 model and the same synthetic NLOS frame, is saved in `artifacts/ai-reliability/audit-phase0/latency.json` (300 iterations per row):

| Path | Mean | Median | P95 |
|---|---:|---:|---:|
| Preliminary solve only | 1.676 ms | 1.454 ms | 3.160 ms |
| Adapter only | 0.669 ms | 0.544 ms | 1.341 ms |
| Preliminary solve + adapter | 2.550 ms | 2.332 ms | 4.151 ms |
| Preliminary solve + adapter + AI-weighted solve | 19.007 ms | 20.501 ms | 24.031 ms |

These values are machine-dependent and include Python/SciPy dispatch overhead. They show why the 0.386 ms figure must not be presented as end-to-end latency.

## d) Origin of the probability inversion

This cannot be established from repository history because there is no `.git` directory (`git rev-parse` reports "not a git repository"). The available evidence is:

- `artifacts/ai-reliability/competition-ai-v1/results.json` (written 01:15) has 8-anchor NLOS AI RMSE 2.1189 m, worse than LOCUS-X at 1.9678 m.
- `artifacts/ai-reliability/competition-ai-v2/results.json` (written 01:22) has 8-anchor NLOS AI RMSE 1.4963 m, better than LOCUS-X.
- Both use seeds 101 and 202, which are already-inspected reference seeds, not validation seeds 11/22.
- There is no v1 adapter unit test for the probability-to-weight direction. The existing tests do not import `ReliabilityModel`.

The artifact sequence is consistent with the inversion being found by rerunning the already-inspected 101/202 benchmark, not by validation-only selection or a unit test. That would be a split-discipline violation even though 101/202 are not the current final test seeds. The exact causal claim remains unverified because Git history is absent. Phase 5 must add a direction test so this cannot recur.

## e) Why validation showed no regression

The policy tuner used only `noise`, `high_noise`, `nlos`, `packet_loss` and `chaos`; 3 and 8 anchors; 60 steps; seeds 11/22; original floor; `held_out` route (`ai_evaluation.py:259-291`). It did not include `nlos_packet` or `nlos_packet_outage`. The test protocol used all base and mixed variants, 3/4/6/8 anchors, 120 steps and seeds 303/404 (`ai_evaluation.py:347-371`).

Therefore the validation worst-case 0.0% is a statement about a narrower scenario set. It does not predict the NLOS + packet-loss regression. The validation set was incomplete, not falsely computed.

## f) Why "One NLOS" is worse than "Majority NLOS"

The group predicate is only `n_nlos == 1` (`ai_evaluation.py:222`). It does not require at least three available anchors and it does not restrict anchor count. A replay of the saved primary protocol produced:

| One-NLOS subgroup | Frames |
|---|---:|
| 1 available anchor | 359 |
| 2 available anchors | 357 |
| 3 available anchors | 63 |
| 4 available anchors | 10 |
| 5 available anchors | 6 |

Only 79 of 795 frames had a solvable three-or-more-anchor fix. The other 716 are coasting/prediction-only frames from packet loss, outage and combined faults. Baseline has only 79 valid errors in this group, with RMSE 1.8165 m; LOCUS-X has 633 valid tracked errors, with RMSE 3.5821 m; AI has 633, with RMSE 3.5859 m. The group mixes anchor counts and fault variants. It does not mix routes: the primary protocol uses only `held_out`.

The anomaly is therefore an aggregation artifact dominated by coasting, not evidence that one obstructed anchor is inherently harder than a majority. The Phase 1 breakdown must condition on both available and NLOS counts.

## g) Artifact inventory

Present:

- `final-evaluation/results.json`: flat baseline/proposed/AI rows, 216 primary and 216 unseen-layout rows; RMSE, median, P95, valid rate, update rate, conditioned RMSE, mean latency and proposed/AI coverage.
- `final-evaluation/metrics.csv`, `layout-generalization.csv`, `majority-bias.csv`: the same tabular data.
- `policy-tuning.json`: candidates, selected floor/threshold, validation rows.
- `dataset-v1/*`: NPZ splits, manifests, per-run catalog and SHA-256 hashes.
- `models/ai-reliability-v1/metrics.json`: aggregate test/validation classification metrics, confusion matrices, calibration bins, logistic/MLP comparison and feature ablations.
- `competition-ai-v1/`, `competition-ai-v2/`, `reference-existing/`: older competition runs; `reference-existing` has only baseline/proposed.

Missing for the requested handoff:

- Matched-frame errors (frames where all three methods have a fix).
- Error during prediction-only intervals and prediction-only coverage.
- Separate baseline coverage; baseline rows have `coverage_95: null`.
- Uncertainty-region size.
- Latency distributions (only a mean is saved).
- Run-level bootstrap intervals for paired method differences.
- Classifier precision/recall/F1/AUC/AP per scenario and per layout.
- PR-curve points and a standalone reliability-diagram data file, although ten-bin calibration data is embedded in `metrics.json`.
- Group breakdown by `(available anchors, NLOS anchors)`.
- A model checksum/manifest file; `models/ai-reliability-v1/` contains only `config.json`, `metrics.json` and `weights.npz`.

## Verification run

Commands were run from the repository root on 21 October 2026:

| Check | Result |
|---|---|
| `pytest -q` | **70 passed**, 2 upstream deprecation warnings, 9.34 s |
| `ruff check backend/locus_x` | **All checks passed** |
| `npm run typecheck` | Passed |
| `npm run lint` | Passed |
| `npm test` | **24 passed** in 2 files, 3.87 s |
| `npm run build` | Passed; Vite reports the pre-existing 905.87 kB `Scene` chunk warning |

The browser fallback display when the model file is absent was not exercised in this phase.

## Required follow-up before any final claim

1. Add fresh-seed final evidence; 303/404 are previously reported and cannot be called unseen.
2. Add baseline coverage and the missing matched/prediction-only/uncertainty/latency-distribution metrics.
3. Add run-level bootstrap confidence intervals and classify every scenario as improved, unchanged or regressed.
4. Add per-scenario and per-layout classifier curves, including AP and reliability data.
5. Rebuild the group analysis using available-anchor and NLOS counts.
6. Retune the policy on validation data that includes the mixed faults, with worst-case regression as a constraint.
7. Add the missing AI truth-boundary, inversion, fallback, checksum and causality tests.
8. Use a new model directory and new artifact directories; do not overwrite v1.
