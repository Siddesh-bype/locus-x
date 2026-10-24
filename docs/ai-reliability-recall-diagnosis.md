# AI Reliability Recall Diagnosis

Analysis-only post-hoc diagnosis of NLOS recall for the frozen LOCUS-X reliability models.
No model, policy, estimator, tracker, simulator, feature schema, or inference path was changed.
No new seed was evaluated.

## Answer

A recall-only fix is **not worth doing**.

The classifier misses many NLOS readings on the inspected final test, but the frozen policy
does not depend on those readings in the way the recall number suggests. Most NLOS readings
occur in frames where the policy already falls back and applies no AI weights. Replacing the
classifier with true per-anchor NLOS labels improves validation RMSE by only `0.0062 m`, or
`0.192%` relative (`95% CI -0.264% to -0.124%`). That is far less than the `0.503%` RMSE
regression that v2 needs to overcome, and even perfect labels leave the all-NLOS and
fallback-heavy frames unchanged.

The larger issue is frame-level fallback and weak-evidence NLOS, not insufficient classifier
capacity. The frozen runtime remains v1.

## Scope and data

| Source | Runs | Frames | Classifier samples | Status |
|---|---:|---:|---:|---|
| Final test | 3,456 | 414,720 | 1,635,824 | post-hoc diagnostic on inspected data |
| Validation | 3,648 | 437,760 | 1,733,609 | validation selection data, already inspected |
| Train | 1,920 | - | 362,705 | training data |

Final-test per-anchor true range, walls crossed, injected bias, and frames-since-last-observation
were not saved. Those final-test strata are therefore unavailable and were not regenerated.
Validation has full truth and supports the per-anchor strata and oracle experiment.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.diagnostics.recall_diagnosis
```

Artifacts are in `artifacts/ai-reliability/recall-diagnosis/`. The completed run took
`5,085 s`, including `677 s` for the Part 4 retraining probes. The seed manifest is
`configs/ai/recall-fix-test-seeds-v1.json` with SHA-256
`61097d1d78fe4377a5507a6d7f68ed005a0500da5ab395af812e22c86ffd083a`.
Those eight seeds remain reserved and were not evaluated.

## Findings

### 1. Recall is lower on final test than validation, and calibration shifted

At the standard `0.5` threshold:

| Source | Model | Precision | Recall | ECE |
|---|---|---:|---:|---:|
| Validation | v1 | 0.9128 | 0.8578 | 0.0521 |
| Validation | v2 | 0.9492 | 0.8845 | 0.0330 |
| Final test | v1 | 0.8965 | 0.6686 | 0.1318 |
| Final test | v2 | 0.9386 | 0.6663 | 0.1237 |

The final-test F1-optimal thresholds collapse to `0.02` for v1 and `0.03` for v2, versus
`0.16` and `0.27` on validation. At those low thresholds final-test recall rises to `0.881`
(v1) and `0.899` (v2), but precision falls to `0.803` and `0.820`. The model is not merely
under-classifying; its probabilities are shifted on the inspected final distribution.

The frozen policy does not weight at `0.5`. It reduces NLOS weights at `p(NLOS) >= 0.95`.
At that bar final-test recall is only `0.4402` for v1 and `0.3407` for v2, with precision
`0.9381` and `0.9850`. The low `0.5` recall is a symptom of prior shift and calibration,
not the policy's operating point.

### 2. Most NLOS readings never reach the weighting rule

The v2 policy uses a frame-level fallback when fewer than three anchors are predicted LOS:

| Split | Frames | Share | NLOS readings | Changed rate |
|---|---:|---:|---:|---:|
| Fewer than 3 available | 74,232 | 17.9% | 0 | 0.0% |
| Predicted LOS under 3 | 125,697 | 30.3% | 437,888 | 0.0% |
| Other fallback | 2,236 | 0.5% | 0 | 0.0% |
| Not fallback | 212,555 | 51.3% | 244,997 | 13.1% |

Fallback covers `48.8%` of frames. The `predicted LOS under 3` split alone contains `64.1%`
of all final-test NLOS readings. Those readings get no reduced weight even though `43.1%`
of them individually exceed `p(NLOS) >= 0.95`; the frame-level gate suppresses the change.
In the non-fallback split only `17.9%` of NLOS readings exceed the weight bar.

This is the main reason a recall fix has little leverage. All-NLOS and high-NLOS frames
fall back by design, and an oracle classifier also falls back on them.

### 3. Perfect labels buy very little

Validation counterfactual, same tracker, same estimator, same fallback rule:

| Condition | Relative RMSE vs statistical LOCUS-X | 95% CI | Fallback | Changed |
|---|---:|---:|---:|---:|
| v2 aggressive `p(NLOS) > 0.5` | -0.177% | -0.294% to -0.066% | 53.6% | 5.7% |
| Oracle true labels | -0.192% | -0.264% to -0.124% | 54.6% | 1.6% |
| Degraded recall 0.90 | -0.094% | -0.235% to +0.038% | 54.0% | 11.7% |
| Degraded recall 0.80 | +0.122% | -0.052% to +0.293% | 50.8% | 14.8% |
| Degraded recall 0.67 | +0.387% | +0.159% to +0.622% | 45.6% | 19.2% |
| Degraded recall 0.50 | +0.702% | +0.444% to +0.950% | 38.3% | 24.3% |

Negative means better than statistical LOCUS-X. The oracle's absolute effect is
`-0.00616 m` (`3.20395` to `3.19779`), or `-0.192%` relative.

In the mixed-NLOS focus set (`n_nlos > 0` and `n_nlos < n_available`) oracle improves
`-0.666%` (`CI -0.866% to -0.459%`). In the all-NLOS set oracle improves only `-0.026%`
(`CI -0.078% to +0.022%`), effectively zero. Most of the remaining opportunity is therefore
in a small mixed subset, not in the bulk of missed readings.

The non-monotonic degradation is important. At `0.90` recall the method is slightly better
than the oracle because it changes fewer anchors and avoids some harmful down-weighting.
Pushing recall up at a fixed `5%` false-alarm rate makes RMSE worse. More recall is not
automatically better.

### 4. Capacity is not the bottleneck

Five-seed validation probes, retrained from saved training features:

| Probe | Recall | Precision | F1 | ECE |
|---|---:|---:|---:|---:|
| Logistic | 0.7967 | 0.7967 | 0.7967 | 0.0303 |
| Current `32-16` MLP | 0.8701 | 0.9404 | 0.9038 | 0.0435 |
| Wider/deeper `64-32` MLP | 0.8785 | 0.9421 | 0.9092 | 0.0419 |

The wider/deeper model adds only `0.84` percentage points of validation recall and `0.54`
points of F1. Class weights do not help monotonically: mean recall is `0.8757`, `0.8723`,
`0.8683`, and `0.8725` at positive weights `1, 2, 3, 5`. Learning curves are flat by the
full training set (`0.856` to `0.866` recall at 100%), so a simple data-volume fix is also
not indicated.

### 5. Missed NLOS readings are weak-evidence, not severe-bias, cases

The strongest validation separators show missed NLOS has lower residual and lower consensus
error than caught NLOS:

| Model | Feature | Missed mean | Caught mean | SMD missed vs caught |
|---|---|---:|---:|---:|
| v2 | `residual_m` | 0.684 | 1.577 | -0.623 |
| v2 | `consensus_median_norm` | 1.310 | 2.773 | -0.490 |
| v2 | `observed_range_norm` | 0.422 | 0.550 | -0.740 |
| v1 | `wall_count_scaled` | 0.535 | 0.750 | -0.744 |

Missed NLOS readings look internally consistent: their biased range agrees with the rest of
the frame, so residual and consensus features do not separate them. The misses are not
dominated by extreme wall attenuation; in validation they concentrate at one wall and at
`10-20 m` range.

Final-test stratum patterns are consistent with this:

- Three available anchors: recall `0.627` (v1), `0.677` (v2).
- All-NLOS frames: `0.645` (v1), `0.616` (v2).
- Minority-NLOS frames: `0.470` (v1), `0.718` (v2), but the v1 minority precision is `0.013`.
- Packet loss is not the driver: v2 recall is `0.617` without packet loss and `0.777` with it.
- Pure `nlos` and `noise_nlos` variants are the weakest (`0.64/0.59` and `0.62/0.56`);
  `chaos` and `all_four` are stronger (`0.83/0.88`).

## Interpretation

The diagnosis separates three questions:

1. **Is the classifier accurate enough on validation?** Yes for selection purposes:
   `0.858` / `0.884` recall with high precision. Capacity probes add little.
2. **Does the final test expose a real recall gap?** Yes, but it coincides with a large
   calibration/prior shift and a fallback gate that removes most NLOS readings from the
   weighting decision.
3. **Would fixing recall improve the final RMSE enough to matter?** No. The oracle upper
   bound is `0.192%` relative on validation, against a `0.503%` final-test regression.
   The all-NLOS portion, which contains a large share of misses, gains essentially nothing.

A recall-only change would therefore be a poor use of a fresh test set. It would also risk
lowering precision and down-weighting LOS readings in the mixed subset where the policy
does act.

## Recommendation

- Do **not** retrain or promote a recall-only classifier, and do not change the frozen
  `0.95` policy bar on this evidence.
- Keep v1 as the live runtime.
- If reliability work continues, target the frame-level fallback and all-NLOS decision
  boundary, and the weak-evidence NLOS cases where residual/consensus signals are absent.
  Test that as an explicit policy/estimator change, not as a classifier recall target.
- Any future candidate must be selected on validation only, then confirmed on the reserved
  seeds in `configs/ai/recall-fix-test-seeds-v1.json`. Those seeds were not evaluated here.

## Limitations

- Final-test tables are post-hoc diagnostics on inspected data.
- Final-test per-anchor truth is missing; final wall/bias/range strata are not available.
- The oracle and degradation experiments use validation, not final test.
- Degradation injects a fixed `5%` false-alarm rate; other operating points could change
  the ordering.
- Retraining probes are validation-selection experiments only and are not promotion evidence.
- No new seeds were evaluated, and no model or policy artifact was modified.

## Artifacts

- `part0_scope.json`: sources, missing fields, frozen hashes, seed-manifest status.
- `part1_pr_curve.csv`, `part1_thresholds.csv`: precision/recall and threshold tradeoffs.
- `part2_strata.csv`, `part2_ranked_missed_nlos.csv`: final/validation strata and miss ranking.
- `part3_feature_separation.csv`, `part3_top_separators.csv`: feature separation and effect sizes.
- `part4_*.csv/json`: training health, protocol, capacity, class-weight, and learning-curve probes.
- `part5_overall.csv`, `part5_focus.csv`, `part5_by_count.csv`, `part5_bootstrap.csv`:
  oracle, aggressive, and degraded-recall counterfactuals.
- `part6_fallback_split.csv`, `part6_fallback_by_count.csv`, `part6_reduced_weights.csv`:
  final-test fallback decomposition.
- `summary.json`: full machine-readable summary.
