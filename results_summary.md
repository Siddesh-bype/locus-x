# Phase 4-6 results summary

Status: the frozen final test completed on the reserved seeds. The v2 candidate improves
classification and beats v1 end-to-end, but **does not beat statistical LOCUS-X overall**.
The committed `+0.5%` regression guard therefore rejects it for promotion. The runtime
adapter remains v1; no post-test tuning was performed.

Detailed protocol, tables, integrity notes and limitations:
[docs/ai-reliability-final.md](docs/ai-reliability-final.md).

## Final test

- Frozen test seeds: `8001, 8009, 8017, 8023, 8039, 8053, 8069, 8081`.
- Route `held_out`, `dt = 0.5 s`, 120 steps, four layouts, 18 base/mixed variants, anchor
  counts 3-8.
- 3,456 runs; 414,720 frames; 1,635,824 classifier samples.
- One valid frozen run. A defect in the first frozen harness invalidated the first
  attempt; that output is preserved separately and was not analyzed. See the integrity
  note in the full report.

## Overall end-to-end result

RMSE is metres. Availability and coverage are fractions. Latency is not a real-time claim.

| Method | RMSE | Valid | Update | 95% coverage | Mean latency |
|---|---:|---:|---:|---:|---:|
| Geometric baseline | 2.7104 | 0.8156 | 0.8156 | n/a | 6.054 ms |
| Statistical LOCUS-X | 3.2771 | 0.9619 | 0.7931 | 0.5636 | 24.576 ms |
| LOCUS-X + v1 | 3.3022 | 0.9619 | 0.7908 | 0.5569 | 36.459 ms |
| LOCUS-X + v2 | 3.2935 | 0.9619 | 0.7907 | 0.5579 | 38.805 ms |
| v2 adapter only | n/a | n/a | n/a | n/a | 4.502 ms |

Paired run-level bootstrap, 2,000 resamples:

| Comparison | Relative RMSE | 95% CI | Decision |
|---|---:|---:|---|
| v2 vs statistical LOCUS-X | **+0.503%** | [+0.351%, +0.668%] | regressed |
| v2 vs v1 | **-0.263%** | [-0.424%, -0.104%] | improved |
| v1 vs statistical LOCUS-X | +0.768% | [+0.605%, +0.928%] | regressed |

The v2 result crosses the committed `>+0.5%` regression threshold with an interval above
zero. The candidate was rejected, not retuned.

## By anchor count

| Count | Baseline | LOCUS-X | v1 | v2 | v2 vs LOCUS-X | Guard |
|---:|---:|---:|---:|---:|---:|---|
| 3 | 2.9121 | 3.5345 | 3.5345 | 3.5345 | 0.000% | unchanged |
| 4 | 2.8188 | 3.6286 | 3.6337 | 3.6465 | +0.493% | unchanged |
| 5 | 2.9178 | 3.3774 | 3.3881 | 3.3970 | +0.581% | regressed |
| 6 | 2.6759 | 3.1929 | 3.2308 | 3.2128 | +0.623% | regressed |
| 7 | 2.6191 | 3.0325 | 3.0722 | 3.0373 | +0.158% | unchanged |
| 8 | 2.4219 | 2.8931 | 2.9546 | 2.9287 | +1.233% | regressed |

At three anchors v2 is identical to LOCUS-X: fallback is 75.75% and changed weights are 0%.
v2 improves over v1 at 6-8 anchors but does not beat LOCUS-X at any count.

## By layout

| Layout | Baseline | LOCUS-X | v1 | v2 | v2 vs LOCUS-X | Guard |
|---|---:|---:|---:|---:|---:|---|
| `hospital_floor_01` | 1.9834 | 2.4483 | 2.4503 | 2.4496 | +0.056% | unchanged |
| `ground` | 3.0011 | 3.4892 | 3.5102 | 3.4970 | +0.222% | unchanged |
| `first` | 2.8258 | 3.6677 | 3.7201 | 3.7075 | +1.087% | regressed |
| `second` | 2.9085 | 3.3668 | 3.3842 | 3.3785 | +0.347% | unchanged |

## Fault slices

| Slice | Baseline | LOCUS-X | v1 | v2 | v2 vs LOCUS-X |
|---|---:|---:|---:|---:|---:|
| Solvable | 2.7104 | 3.1067 | 3.1357 | 3.1263 | +0.631% (regressed) |
| NLOS | 3.8104 | 4.5912 | 4.6273 | 4.6149 | +0.517% (regressed) |
| Majority NLOS | 3.5096 | 3.8167 | 3.8743 | 3.8476 | +0.809% (regressed) |
| All NLOS | 4.1243 | 4.8816 | 4.9126 | 4.9090 | +0.561% (regressed) |
| NLOS + packet loss | 4.7164 | 5.0788 | 5.1177 | 5.0942 | +0.304% (unchanged) |
| NLOS + outage | 6.8504 | 6.7185 | 6.7199 | 6.7153 | -0.047% (unchanged) |
| Coasting (<3 available) | n/a | 4.1326 | 4.1417 | 4.1359 | +0.080% (unchanged) |

Across 18 variants x 6 counts, v2 vs LOCUS-X was unchanged in 90/108 slices, regressed in
16/108 and improved in 2/108. v2 vs v1 was unchanged in 83/108, improved in 21/108 and
regressed in 4/108.

## Classification

| Population | Model | Precision | Recall | F1 | ROC-AUC | Brier | ECE |
|---|---|---:|---:|---:|---:|---:|---:|
| All samples | v1 | 0.8965 | 0.6686 | 0.7660 | 0.9356 | 0.1397 | 0.1318 |
| All samples | v2 | 0.9386 | 0.6663 | 0.7794 | 0.9538 | 0.1234 | 0.1237 |
| No packet loss | v1 | 0.8984 | 0.6641 | 0.7637 | 0.9410 | 0.1335 | 0.1268 |
| No packet loss | v2 | 0.9412 | 0.6171 | 0.7455 | 0.9558 | 0.1328 | 0.1382 |
| Packet loss | v1 | 0.8924 | 0.6786 | 0.7709 | 0.9195 | 0.1560 | 0.1452 |
| Packet loss | v2 | 0.9341 | 0.7766 | 0.8481 | 0.9560 | 0.0985 | 0.0855 |

v2 improves aggregate discrimination, Brier and ECE and is materially better under packet
loss. It has higher precision but lower recall/F1 on the no-loss population. Classification
quality did not translate into an overall end-to-end win.

## Fallback and policy

The frozen v2 policy is the retained current policy `hard_t005_f005_min3`; all 21 candidate
policies were disqualified on validation, so no new policy was selected. On the final test,
v2 fallback was 48.75% overall and changed weights on 6.73% of frames. Fallback by anchor
count: 75.75%, 60.46%, 47.58%, 42.16%, 36.95%, 29.59% for 3-8 anchors. Packet-loss frames
fall back 68.93% versus 33.48% without packet loss.

The high packet-loss fallback is expected: packet-loss observations are excluded from
supervision and their cached feature rows are NaN, so the policy falls back rather than
imputing missing probabilities.

## Matched-baseline check

On the 338,232 frames where the geometric baseline is valid, statistical LOCUS-X is
3.1036 m versus the baseline's 2.7104 m, **+14.508%** (95% CI [+13.648%, +15.353%]). This
is a post-hoc matched-subset check, not the preregistered AI comparison. It confirms the
historical caveat: the filter's higher availability includes prediction-only positions and
does not imply lower error. The AI methods inherit this limitation.

## Integrity and reproduction

- Freeze manifest: `artifacts/ai-reliability/final-freeze/manifest.json`, freeze commit
  `5aef613`, attempt 2, policy hash `4c38a30b...`, test-manifest hash `f7e4c0fe...`.
- The first frozen attempt (`b87bef3`) was invalid: selected anchors were passed as
  disabled anchors, and classifier `p(LOS)` was stored as `p(NLOS)`. It is preserved at
  `artifacts/ai-reliability/final-test-invalid-mask/` with
  `artifacts/ai-reliability/final-freeze/invalid-mask-manifest.json`; it was not analyzed.
- The corrected valid run used the reserved seeds once. No performance-based rerun or
  post-freeze tuning was performed.
- Full derived tables: `artifacts/ai-reliability/final-test/analysis/` and
  `results_summary.csv`. Raw `frames.npz` and `classifier-samples.npz` are retained
  locally but are not intended for the compact commit.
- Checks: backend `107 passed`; Ruff check pass and the new analysis script is formatted;
  frontend typecheck, lint, 24 tests and production build pass. Ruff format check still
  reports pre-existing formatting in 11 frozen Phase 1-5 files; those files were not
  reformatted because their hashes are frozen.
- Simulator results only. No real UWB/BLE, multipath, correlated-loss or hospital
  deployment claim.
