# LOCUS-X AI reliability: Phases 4-6 final report

Status: complete. The frozen final test has been executed on the reserved seeds. The v2
candidate is the better classifier and beats v1 end-to-end, but it **does not beat
statistical LOCUS-X overall**. Under the rule committed before evaluation it is rejected
for promotion. The runtime adapter remains v1 and the statistical estimator remains the
fallback. No post-test tuning was performed.

Date: 23 October 2026. This report includes negative results and the preserved invalid
pre-run required by the Phase 4-6 protocol.

## 1. Decision

| Question | Result |
|---|---|
| Does v2 beat statistical LOCUS-X? | **No.** Overall RMSE `+0.503%`, 95% CI `[+0.351%, +0.668%]`; the interval is above zero and crosses the committed `>+0.5%` regression guard. |
| Does v2 beat v1? | **Yes.** Overall RMSE `-0.263%`, 95% CI `[-0.424%, -0.104%]`. |
| Was a better policy selected on validation? | **No.** All 21 candidates were disqualified; the current policy was retained. |
| Should v2 be promoted to the live runtime? | **No.** The candidate failed its preregistered end-to-end gate. |
| Is the final test a positive result? | **Mixed/negative.** The classifier improved; localization did not beat the statistical baseline overall. |

The final-test result is consistent with validation, where the current policy was already
disqualified for regressions at higher anchor counts and in several scenario x count
slices. The final test is reported as a rejection, not as a failed attempt to be retried.

## 2. Scope and protocol

Phase 4 tuned only the v2 weight policy on cached validation frames and added robustness
tests. Phase 5 froze the model, policy, schema, seed manifest and code, then ran the
reserved final test. Phase 6 reports all outcomes.

Final test configuration:

- Reserved seeds: `8001, 8009, 8017, 8023, 8039, 8053, 8069, 8081`.
- Route `held_out`, `dt = 0.5 s`, 120 steps per run.
- Four layouts: `hospital_floor_01`, V3 `ground`, V3 `first`, V3 `second`.
- 18 base/mixed variants (clean plus 17 fault combinations), excluding the periodic
  `anchor_failure` control used only in validation.
- Anchor counts `3, 4, 5, 6, 7, 8`.
- Four methods on identical generated observations: geometric baseline, statistical
  LOCUS-X, LOCUS-X + v1, LOCUS-X + frozen v2.

This produced **3,456 runs**, **414,720 frames** and **1,635,824 classifier samples**.

The final product is simulator evidence, not radio validation. Truth, labels, injected
bias, fault flags, route and future samples are not available to any estimator feature.
Packet receipt and absence are available observations.

## 3. Freeze integrity and the invalid pre-run

The first frozen attempt (`b87bef3`) was invalid and is preserved, not analyzed:

- `artifacts/ai-reliability/final-test-invalid-mask/`
- `artifacts/ai-reliability/final-freeze/invalid-mask-manifest.json`

Two defects were found before any valid result was used:

1. The evaluator passed the selected anchor subset as `disabled_anchors`, leaving the
   complement available. Counts 3-5 were effectively reversed and counts 6-8 had fewer
   than three available anchors.
2. The classifier report stored `p(LOS)` as `p(NLOS)`.

Both defects were fixed in `5aef613`, with a regression test for the anchor-subset bug and
a probability-direction test. The refrozen manifest is
`artifacts/ai-reliability/final-freeze/manifest.json` (`attempt: 2`, freeze commit
`5aef613`). It records the invalidation reason and preserves the original freeze commit.
The reserved seeds were not changed.

The valid run used the corrected frozen code. Manifest checks after the run confirm:

- policy SHA-256 `4c38a30b7bfb19f19b9d67bd05b79c2183e4cf4ff62020d7cbb17e15d2abfca8`;
- test-seed manifest SHA-256 `f7e4c0fe7fc81cf5a09198a94640686759edd6071378ee657a6692c2b0906942`;
- `backend/locus_x/ai_final.py` hash matches the refrozen manifest;
- 3,456 run-catalog entries match 3,456 summary runs.

The invalid attempt is not included in any table below. After the valid run, only
post-processing analysis and reporting were added; the frozen model, policy, schema and
test harness were not changed.

## 4. Policy selection

The selection rule was committed before evaluation (`9cb0d2c`). It disqualifies a
candidate for any scenario x anchor-count slice, anchor count or overall comparison whose
paired bootstrap interval lies entirely above zero with relative worsening greater than
`+0.5%`. Remaining candidates are ranked by mean paired improvement over the current
policy on solvable mixed-fault, majority-NLOS and all-NLOS frames, then fallback rate and
simplicity.

Candidate grid: 21 policies (20 hard policies over threshold `{0.05, 0.1, 0.2, 0.35, 0.5}`,
floor `{0.05, 0.2}` and fallback `{min3, min2_if4}`, plus one soft policy).

Result: **all 21 candidates were disqualified**, including the current policy. The
eligible set was empty. Under the precommitted rule the current policy
`hard_t005_f005_min3` was retained. No threshold was relaxed and no new policy was
selected.

The retained policy on validation had 68.976% fallback, 14.219% changed-weight frames and
31.024% active frames. The v2 final-test bundle is therefore the v2 feature/model bundle
with the retained current policy, not a newly optimized policy.

The packet-loss limitation is explicit: packet-loss observations are excluded from
supervision, and their cached probability rows are NaN. The policy falls back when an
available probability is non-finite. It does not impute missing probabilities.

Evidence: `artifacts/ai-reliability/phase4-final/policy/selection.{json,csv}`.

## 5. End-to-end results

RMSE is metres. Availability, update and coverage are fractions. Latency is measured on
the test machine and is not a real-time guarantee.

| Method | RMSE | Valid | Update | 95% coverage | Mean latency | Median | p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Geometric baseline | 2.7104 | 0.8156 | 0.8156 | n/a | 6.054 ms | 4.427 ms | 18.731 ms |
| Statistical LOCUS-X | 3.2771 | 0.9619 | 0.7931 | 0.5636 | 24.576 ms | 19.330 ms | 69.490 ms |
| LOCUS-X + v1 | 3.3022 | 0.9619 | 0.7908 | 0.5569 | 36.459 ms | 29.587 ms | 101.764 ms |
| LOCUS-X + v2 | 3.2935 | 0.9619 | 0.7907 | 0.5579 | 38.805 ms | 31.446 ms | 107.159 ms |
| v2 adapter only | n/a | n/a | n/a | n/a | 4.502 ms | 3.508 ms | 11.972 ms |

Paired run-level bootstrap, 2,000 resamples, 95% interval:

| Comparison | Difference | Relative | 95% CI | Decision |
|---|---:|---:|---:|---|
| v2 vs statistical LOCUS-X | +0.0165 m | **+0.503%** | [+0.351%, +0.668%] | regressed |
| v2 vs v1 | -0.0087 m | **-0.263%** | [-0.424%, -0.104%] | improved |
| v1 vs statistical LOCUS-X | +0.0251 m | +0.768% | [+0.605%, +0.928%] | regressed |

The v2 interval is entirely above zero, and the point estimate exceeds the committed
`+0.5%` threshold. This is a genuine rejection under the rule, not a rounding artifact.

The statistical and AI methods share observation sets and availability. The baseline does
not: it has lower availability (81.56%) because it does not produce prediction-only
positions. A post-hoc matched-subset check is included in section 10.

### 5.1 By anchor count

| Count | Baseline | LOCUS-X | v1 | v2 | v2 vs LOCUS-X | Relative 95% CI | Guard |
|---:|---:|---:|---:|---:|---:|---:|---|
| 3 | 2.9121 | 3.5345 | 3.5345 | 3.5345 | 0.000% | [0.000%, 0.000%] | unchanged |
| 4 | 2.8188 | 3.6286 | 3.6337 | 3.6465 | +0.493% | [+0.357%, +0.646%] | unchanged |
| 5 | 2.9178 | 3.3774 | 3.3881 | 3.3970 | +0.581% | [+0.252%, +0.918%] | regressed |
| 6 | 2.6759 | 3.1929 | 3.2308 | 3.2128 | +0.623% | [+0.355%, +0.900%] | regressed |
| 7 | 2.6191 | 3.0325 | 3.0722 | 3.0373 | +0.158% | [-0.524%, +0.725%] | unchanged |
| 8 | 2.4219 | 2.8931 | 2.9546 | 2.9287 | +1.233% | [+0.505%, +1.970%] | regressed |

At three anchors, v2 is identical to LOCUS-X: 75.75% fallback and zero changed weights.
At four anchors v2 is slightly worse than both v1 and LOCUS-X. At six and eight anchors v2
improves on v1 but remains worse than LOCUS-X. There is no count at which v2 beats the
statistical method.

### 5.2 By layout

| Layout | Baseline | LOCUS-X | v1 | v2 | v2 vs LOCUS-X | Guard |
|---|---:|---:|---:|---:|---:|---|
| `hospital_floor_01` | 1.9834 | 2.4483 | 2.4503 | 2.4496 | +0.056% | unchanged |
| `ground` | 3.0011 | 3.4892 | 3.5102 | 3.4970 | +0.222% | unchanged |
| `first` | 2.8258 | 3.6677 | 3.7201 | 3.7075 | +1.087% | regressed |
| `second` | 2.9085 | 3.3668 | 3.3842 | 3.3785 | +0.347% | unchanged |

v2 improves on v1 on `ground` and `second`; the `first` layout is the clearest regression
against LOCUS-X. No layout shows a v2 win over LOCUS-X.

### 5.3 Fault slices

| Slice | Baseline | LOCUS-X | v1 | v2 | v2 vs LOCUS-X | Guard |
|---|---:|---:|---:|---:|---:|---|
| Solvable | 2.7104 | 3.1067 | 3.1357 | 3.1263 | +0.631% | regressed |
| NLOS | 3.8104 | 4.5912 | 4.6273 | 4.6149 | +0.517% | regressed |
| Majority NLOS | 3.5096 | 3.8167 | 3.8743 | 3.8476 | +0.809% | regressed |
| All NLOS | 4.1243 | 4.8816 | 4.9126 | 4.9090 | +0.561% | regressed |
| NLOS + packet loss | 4.7164 | 5.0788 | 5.1177 | 5.0942 | +0.304% | unchanged |
| NLOS + outage | 6.8504 | 6.7185 | 6.7199 | 6.7153 | -0.047% | unchanged |
| Coasting (<3 available) | n/a | 4.1326 | 4.1417 | 4.1359 | +0.080% | unchanged |

Coasting is prediction-only for the filter. The baseline has no valid estimate there, so
no baseline RMSE is defined. v2 changes coasting by less than 0.1%.

### 5.4 Slice-level guard

Across 18 variants x 6 anchor counts (108 paired comparisons):

- v2 vs LOCUS-X: 90 unchanged, 16 regressed, 2 improved.
- v2 vs v1: 83 unchanged, 21 improved, 4 regressed.

The 16 v2-vs-LOCUS-X regressions are concentrated in NLOS, noise+NLOS, NLOS+outage and
noise+NLOS+outage variants at 4-7 anchors, plus `chaos`/`all_four` at 8 anchors. This is
consistent with the aggregate result and with the validation disqualifications.

Evidence: `artifacts/ai-reliability/final-test/analysis/slice-labels.json`.

### 5.5 Fault-population groups

Frames grouped by the number of NLOS anchors among available anchors. These groups are
descriptive; they are not the preregistered bootstrap scopes.

| NLOS group | Frames | Baseline | LOCUS-X | v1 | v2 |
|---|---:|---:|---:|---:|---:|
| One NLOS | 1,061 | 2.9765 | 3.0486 | 3.1035 | 3.0847 |
| Minority NLOS | 171,821 | 0.5200 | 0.5543 | 0.5557 | 0.5569 |
| Half NLOS | 4,590 | 2.6239 | 3.1649 | 3.1949 | 3.1505 |
| Majority NLOS | 76,338 | 3.5096 | 3.8167 | 3.8743 | 3.8476 |
| All NLOS | 86,678 | 4.1243 | 4.8816 | 4.9126 | 4.9090 |

The clean/minority population dominates the frame count and keeps absolute errors low. The
AI effect is small there; the harder groups carry most of the RMSE. The frozen summary
omitted group names in this table. The corrected labels are derived in
`artifacts/ai-reliability/final-test/analysis/nlos-groups.csv` without changing
observations or decisions.

## 6. Classification and calibration

| Population | Model | Precision | Recall | F1 | ROC-AUC | Brier | ECE |
|---|---|---:|---:|---:|---:|---:|---:|
| All | v1 | 0.8965 | 0.6686 | 0.7660 | 0.9356 | 0.1397 | 0.1318 |
| All | v2 | 0.9386 | 0.6663 | 0.7794 | 0.9538 | 0.1234 | 0.1237 |
| No packet loss | v1 | 0.8984 | 0.6641 | 0.7637 | 0.9410 | 0.1335 | 0.1268 |
| No packet loss | v2 | 0.9412 | 0.6171 | 0.7455 | 0.9558 | 0.1328 | 0.1382 |
| Packet loss | v1 | 0.8924 | 0.6786 | 0.7709 | 0.9195 | 0.1560 | 0.1452 |
| Packet loss | v2 | 0.9341 | 0.7766 | 0.8481 | 0.9560 | 0.0985 | 0.0855 |

v2 is a better aggregate classifier: higher precision, F1, ROC-AUC, Brier and ECE. Its
advantage is largest under packet loss. On the no-loss population it has higher precision
but lower recall and worse F1/ECE than v1. That matters because the localization policy
uses per-anchor LOS probabilities, not aggregate F1.

The frozen `summary.json` used duplicate `condition` labels for its classifier scopes. The
corrected `condition/no_loss` and `condition/packet_loss` rows are in
`artifacts/ai-reliability/final-test/analysis/classifier.csv` and `calibration.csv`.

## 7. Fallback behaviour

On the final test, v2 fell back on **48.75%** of frames and changed any weight on **6.73%**.
Active frames were 51.25%.

| Scope | Fallback | Changed weights |
|---|---:|---:|
| Overall | 48.75% | 6.73% |
| 3 anchors | 75.75% | 0.00% |
| 4 anchors | 60.46% | 0.87% |
| 5 anchors | 47.58% | 5.77% |
| 6 anchors | 42.16% | 8.90% |
| 7 anchors | 36.95% | 10.84% |
| 8 anchors | 29.59% | 14.02% |
| Packet loss | 68.93% | 3.05% |
| No packet loss | 33.48% | 9.52% |

The 3-anchor row explains the exact v1/v2/LOCUS-X equality at that count: fallback is high
and no weights change, even though 24.25% of frames are technically "active" because the
predicted-LOS check succeeded. The validation fallback was higher (68.976%) because that
protocol includes the full fault set, including `anchor_failure` and more adverse slices.

The policy is conservative by design. It does not use a learned weight when the classifier
cannot establish the minimum LOS consensus, and it does not impute packet-loss features.

## 8. Why the classification gain did not become a localization gain

This run supports a narrow conclusion: better probability ranking is not sufficient for a
better end-to-end estimator under this policy. Possible contributors, not isolated by this
test:

- The retained hard policy changes weights on only 6.73% of final-test frames; at 3-4
  anchors it almost never acts.
- v2 has lower recall on no-loss NLOS samples than v1, so some truly NLOS anchors retain
  high `p(LOS)` and are not downweighted. Higher precision does not compensate for missed
  NLOS under a threshold policy.
- The statistical estimator already rejects many biased anchors; an extra multiplicative
  weight can add variance or over-downweight useful anchors.
- Anchor geometry and filter prediction-only frames dominate error in several slices, not
  classification alone.

These are hypotheses for future work. No post-hoc threshold was tried, because doing so
would invalidate the frozen comparison.

## 9. Latency

| Path | Mean | Median | p95 |
|---|---:|---:|---:|
| Geometric baseline | 6.054 ms | 4.427 ms | 18.731 ms |
| Statistical LOCUS-X | 24.576 ms | 19.330 ms | 69.490 ms |
| LOCUS-X + v1 | 36.459 ms | 29.587 ms | 101.764 ms |
| LOCUS-X + v2 | 38.805 ms | 31.446 ms | 107.159 ms |
| v2 adapter only | 4.502 ms | 3.508 ms | 11.972 ms |

The AI path adds roughly 12-14 ms mean over statistical LOCUS-X, of which about 4.5 ms is
the v2 adapter. This is a local benchmark, not a real-time guarantee.

## 10. Matched-baseline check and availability caveat

The baseline produces a fix on 81.56% of frames; statistical LOCUS-X is valid on 96.19%,
including prediction-only positions. A raw overall RMSE comparison therefore favors the
baseline partly because it abstains more often.

Post-hoc, on the 338,232 frames where the baseline is valid:

| Method | RMSE on common frames |
|---|---:|
| Baseline | 2.7104 |
| Statistical LOCUS-X | 3.1036 |
| v1 | 3.1326 |
| v2 | 3.1233 |

Statistical LOCUS-X is **+14.508%** worse than the baseline on this common subset (95% CI
[+13.648%, +15.353%]). This is not the preregistered AI-vs-statistical test, but it is
reported because it affects interpretation. The filter's added availability includes
prediction-only frames with higher error; higher availability is not the same as higher
accuracy. The AI methods inherit this limitation. No baseline or filter tuning was
performed after the final test.

## 11. Verification

| Check | Result |
|---|---|
| Backend pytest | 107 passed |
| Ruff check | pass |
| Ruff format check | new analysis script formatted; 11 frozen Phase 1-5 files retain pre-freeze formatting and are not reformatted because their hashes are in the freeze manifest |
| Frontend typecheck | pass |
| Frontend lint | pass |
| Frontend tests | 24 passed |
| Frontend production build | pass |
| AI-OFF reference benchmark | 56/56 accuracy rows match the preserved reference; runtime excluded |
| Freeze/output manifest hash checks | pass |

The 56-row reference comparison is in
`artifacts/ai-reliability/phase4-final/reference-ai-off/comparison.json`.

## 12. Reproduction and artifacts

Compact report artifacts:

- `results_summary.md`, `results_summary.csv`
- `artifacts/ai-reliability/final-test/summary.json`
- `artifacts/ai-reliability/final-test/metrics.csv`
- `artifacts/ai-reliability/final-test/bootstrap.csv`
- `artifacts/ai-reliability/final-test/latency.json`
- `artifacts/ai-reliability/final-test/manifest.json`
- `artifacts/ai-reliability/final-test/run-catalog.json`
- `artifacts/ai-reliability/final-test/analysis/` (corrected groups, fallback, classifier,
  calibration, slice labels)
- `artifacts/ai-reliability/final-freeze/manifest.json`
- `artifacts/ai-reliability/final-freeze/invalid-mask-manifest.json`
- `artifacts/ai-reliability/final-test-invalid-mask/` (preserved, not analyzed)
- `artifacts/ai-reliability/phase4-final/policy/selection.{json,csv}`
- `artifacts/ai-reliability/phase4-final/comparison/g5-vs-v1.{json,csv}`
- `artifacts/ai-reliability/phase4-final/reference-ai-off/`

The exact frozen command is
`.\.venv\Scripts\python.exe -m backend.locus_x.ai_final --workers 18`. The corrected
derived tables are regenerated from the saved arrays with
`.\.venv\Scripts\python.exe -m scripts.analyze_final`; this script does not regenerate
observations or change decisions.

Raw `frames.npz` (33 MB) and `classifier-samples.npz` (22 MB) remain in the local output
directory for audit and re-analysis. The compact repository commit contains the derived
tables, manifests and reports rather than the raw arrays.

## 13. Limitations

- Simulator-only results; no UWB/BLE hardware, real multipath, correlated packet loss or
  hospital deployment claim.
- The final test uses 18 base/mixed variants and counts 3-8; conclusions do not extend to
  arbitrary anchor counts, layouts or fault models.
- The baseline/filter comparison has different availability; the matched subset is
  post-hoc.
- The AI path is slower than statistical LOCUS-X and is not a real-time claim.
- The v2 candidate was rejected under the frozen gate; the live runtime remains v1.
- The invalid pre-run is preserved for transparency. Its exclusion is based on a code
  defect, not on its results.
