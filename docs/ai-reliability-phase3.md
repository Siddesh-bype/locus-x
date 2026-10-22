# AI reliability: Phase 3 v2 features and evaluation

Status: Phase 3 complete. The frozen v1 model, v1 adapter, policy, schema, baseline, tracker, simulator, and Phase 1/2 evidence were not modified. The selected v2 artifact is installed at `models/ai-reliability-v2/`; runtime integration is deliberately left to Phase 4.

Phase 2 is complete at `468f7e7`. Phase 3 preregistration commits are the extra-seed config `e6ed695`, the plan `cc5cb0d`, the v2 dataset config `67e65ca`, and the addendum `e75ce59`.

## Outcome

The preregistered rule selects **G5**, the map-free G0 baseline plus the four reception/available-anchor features. G5 is promoted with 26 features and five seeds. It removes measurable error on majority-NLOS and packet-loss frames without a significant regression above the `+0.5%` tolerance in any focus slice, layout, or anchor count.

The broad/all-NLOS improvement is small and its interval crosses zero relative to statistical LOCUS-X, but G5 does improve over v1 on the same frames. The exact result is therefore: packet-loss and majority-NLOS improve; all-NLOS is not shown to improve relative to LOCUS-X; there is no evidence that broad NLOS is fully identifiable from these observables.

## Protocol and provenance

- Training: 4 layouts, all four Phase 2 training layouts, `362,705` samples, `96,000` frames, `1,920` runs, `604.5 s`.
- Validation: 4 layouts, 8 seeds (`11, 22, 6007, 6011, 6019, 6029, 6037, 6043`), anchor counts `3-8`, 19 variants, `1,733,609` samples, `437,760` frames, `3,648` runs, `14,430.5 s`.
- The 19 validation variants are `clean, noise, high_noise, nlos, packet_loss, anchor_outage, chaos, noise_nlos, noise_packet, noise_outage, nlos_packet, nlos_outage, packet_outage, noise_nlos_packet, noise_nlos_outage, noise_packet_outage, nlos_packet_outage, all_four, anchor_failure`.
- All splits are by whole run and identical across variants. Calibration uses the Phase 2 fixed whole-run 80/20 training split. Validation seeds are the decision set. Test seeds `8001..8081` and Phase 1 seeds were not used.
- G0 reproduction: `362,705` samples, `max_abs_g0_difference = 0.0`, labels equal, v2 feature version `2.0.0`, first 24 features exactly the v1 schema.
- Truth boundary: every v2 feature uses only observations, anchor geometry, preliminary estimate, causal history, and the statistical tracker state. No truth, label, bias, route, or future sample is passed to the adapter.
- Policy is unchanged: floor `0.05`, threshold `0.05`, ambiguous fallback when predicted LOS count is below `3`.

## Feature schema v2

The first 24 columns are the unchanged v1 schema and order. The new columns are appended after them. All windows are backward-looking and include the current frame only where stated. Unknown values use explicit indicators or documented neutral values; zero is not used as an implicit unknown for G1.

| Order | Feature | Definition / unit | Source and window | Missing value |
|---:|---|---|---|---|
| 0 | `observed_range_norm` | observed range divided by floor diagonal | current available observation | row absent if unavailable |
| 1 | `predicted_range_norm` | range from preliminary estimate divided by diagonal | current estimate | row absent if no estimate |
| 2 | `residual_m` | observed minus predicted range, metres | current frame | row absent if unavailable |
| 3 | `residual_norm` | residual divided by robust scale, `1.4826 * MAD` | current frame | robust scale floored at `0.2 m` |
| 4 | `abs_residual_norm` | absolute normalized residual | current frame | none |
| 5 | `instant_reliability` | clipped Cauchy reliability of normalized residual, floor `0.08` | current frame | none |
| 6 | `anchor_x_norm` | anchor x divided by floor width | anchor geometry | none |
| 7 | `anchor_y_norm` | anchor y divided by floor height | anchor geometry | none |
| 8 | `relative_dx_norm` | anchor x minus estimate x, divided by diagonal | current estimate | row absent if no estimate |
| 9 | `relative_dy_norm` | anchor y minus estimate y, divided by diagonal | current estimate | row absent if no estimate |
| 10 | `bearing_sin` | `dy / distance` | current estimate and anchor | zero distance floored at `1e-9` |
| 11 | `bearing_cos` | `dx / distance` | current estimate and anchor | zero distance floored at `1e-9` |
| 12 | `available_fraction` | available anchors divided by 8 | current frame | none |
| 13 | `condition_log` | `log1p(min(condition, 1e6)) / log(1e6)` | current solve | condition `1e6` if absent |
| 14 | `wall_count_scaled` | `min(wall crossings, 3) / 3` | current estimate and static map | map feature only |
| 15 | `frame_wall_mean_scaled` | mean wall crossings over available anchors, `/3` | current frame and static map | map feature only |
| 16 | `consensus_median_norm` | median normalized residual | current frame | none |
| 17 | `consensus_mad_norm` | MAD of normalized residual | current frame | none |
| 18 | `observed_to_predicted_delta` | `observed / predicted - 1` | current frame | predicted floored at `1e-9` |
| 19 | `residual_sign` | sign of residual | current frame | `-1, 0, +1` |
| 20 | `previous_residual_norm` | last normalized residual for this anchor | prior history | neutral `0` plus G1 indicator |
| 21 | `residual_delta_norm` | current minus previous normalized residual | prior history | previous neutral `0` plus G1 indicator |
| 22 | `position_delta_norm` | displacement from previous estimate divided by diagonal | prior estimate | `0` on first frame |
| 23 | `time_delta_s` | capped time since previous frame, seconds | prior timestamp | `0` on first frame, capped at `2 s` |
| 24 | `range_window_mean_norm` | mean of last 5 ranges including current, divided by diagonal | 4 prior + current | prior window empty uses current only |
| 25 | `range_window_variance_norm` | variance of that range window divided by diagonal squared | 4 prior + current | prior empty uses current only |
| 26 | `range_last_diff_norm` | current range minus previous range, divided by diagonal | prior range | `0` with no prior |
| 27 | `range_rate_norm` | last range difference divided by elapsed time and max speed | prior range/timestamp | `0` when `dt <= 1e-9` |
| 28 | `implied_speed_ratio` | `abs(last_diff) / (dt * 3.0 m/s)` | prior range/timestamp | `0` when `dt <= 1e-9` |
| 29 | `pair_violation_count_norm` | triangle-inequality violation count divided by `n-1` | current frame, all anchor pairs | `0` with fewer than 2 valid anchors |
| 30 | `pair_violation_magnitude_norm` | summed violation divided by diagonal, capped at `2` | current frame, all anchor pairs | `0` with fewer than 2 valid anchors |
| 31 | `tracker_range_residual_norm` | observed range minus range predicted from tracker state, divided by diagonal | prior statistical tracker state | `0` minus G4 availability indicator |
| 32 | `tracker_prediction_available` | `1` if tracker state is finite and age is `0..3 s` | prior tracker state | `0` |
| 33 | `tracker_state_age_norm` | tracker age divided by `3 s`, capped | prior tracker state | `1` |
| 34 | `reception_rate` | received fraction over the last 8 frames including current | 7 prior + current | prior empty uses current only |
| 35 | `reception_miss_streak_norm` | trailing consecutive-miss count divided by 8, capped | prior reception history | `0` |
| 36 | `reception_last_frame` | `1` if the previous frame for this anchor was received, else `0` | prior reception history | `0` |
| 37 | `available_anchor_count_norm` | available anchor count divided by 8 | current frame | none |
| 38 | `has_previous_observation` | `1` if this anchor has a previous observation | prior history | `0` |
| 39 | `frames_since_last_observation_norm` | frame gap divided by 8, capped | prior history | `1` when never seen |
| 40 | `previous_residual_missing` | `1` if no previous residual exists for this anchor | prior history | `1` when never seen |

G5 is `G0 map-free (22) + G5 reception (4) = 26` features. The selected model excludes the two map features, G1, G2, G3, and G4.

## Classifier results

Validation rows: `1,733,609`; labels are available LOS/NLOS observations only. AUC is undefined for the all-NLOS slice because all labels are positive.

| Variant | Features | ROC-AUC | F1 | ECE | Brier |
|---|---:|---:|---:|---:|---:|
| v1 | 24 | 0.9729 | 0.8844 | 0.0521 | 0.0696 |
| G0 full | 24 | 0.9827 | 0.9008 | 0.0407 | 0.0556 |
| G0 map-free | 22 | 0.9831 | 0.8966 | 0.0502 | 0.0572 |
| G2 | 27 | 0.9832 | 0.9066 | 0.0377 | 0.0527 |
| G3 | 24 | 0.9824 | 0.8954 | 0.0501 | 0.0579 |
| G4 | 25 | 0.9835 | 0.8985 | 0.0494 | 0.0559 |
| **G5** | **26** | **0.9857** | **0.9157** | **0.0330** | **0.0477** |
| G1+G5 | 29 | 0.9859 | 0.9165 | 0.0341 | 0.0475 |
| All map-free | 39 | 0.9868 | 0.9218 | 0.0312 | 0.0446 |
| All map-inclusive | 41 | 0.9856 | 0.9209 | 0.0255 | 0.0456 |

G5 improves over v1 by `+0.0128` AUC, `+0.0313` F1, and `-0.0191` ECE overall. The all-mapfree candidate has the best ranking AUC, but its end-to-end majority-NLOS gain is smaller than G5's and it uses 13 more features.

Focus slices:

| Slice and variant | ROC-AUC | F1 | ECE | Brier |
|---|---:|---:|---:|---:|
| Majority NLOS, v1 | 0.9627 | 0.9197 | 0.0952 | 0.0910 |
| Majority NLOS, G5 | 0.9836 | 0.9415 | 0.0894 | 0.0638 |
| All NLOS, v1 | n/a | 0.9204 | 0.1641 | 0.1182 |
| All NLOS, G5 | n/a | 0.9343 | 0.1564 | 0.0945 |
| NLOS+packet loss, v1 | 0.9249 | 0.8627 | 0.1870 | 0.1649 |
| NLOS+packet loss, G5 | 0.9670 | 0.9157 | 0.1410 | 0.0975 |
| NLOS+packet loss+outage, v1 | 0.9667 | 0.9634 | 0.0877 | 0.0544 |
| NLOS+packet loss+outage, G5 | 0.9951 | 0.9766 | 0.0794 | 0.0332 |

The packet-loss calibration failure is materially smaller for G5: packet-loss ECE `0.0512` versus v1 `0.1038`; no-loss ECE `0.0268` versus `0.0356`. At 6 anchors, packet-loss ECE is `0.0524` for G5 versus `0.1062` for v1, and no-loss ECE is `0.0209` versus `0.0298`.

Per-anchor-count calibration is not uniformly good. At 5 anchors, ECE rises to `0.0871` for G5, `0.0863` for all-mapfree, and `0.0684` for v1 in no-loss data. The available-anchor-count feature reduces the packet-loss calibration gap but does not make 5-anchor calibration adequate. No new calibrator was fitted in Phase 3.

## 6-anchor diagnostic

At 6 anchors the unchanged `predicted_los < 3` fallback fires on `52.1%` of frames for G5 and `51.9%` for v1. Mean predicted NLOS count is `1.77` for G5 and `1.76` for v1, against a true mean of `2.16`. G5 reduces per-anchor misclassification from `8.65%` (v1) to `5.87%`, and all-mapfree reaches `5.13%`, but the fallback rate remains high.

The Phase 2 6-anchor end-to-end regression did not reproduce on this expanded validation set: v1 is `-0.229%` (improved) and G5 is `+0.004%` (unchanged) at 6 anchors. No candidate shows a significant regression above `+0.5%` at 6 anchors. The high fallback rate is therefore a remaining diagnostic concern, not evidence that G5 caused a 6-anchor regression. The cause of the Phase 2 6-anchor result is still not identified.

## End-to-end results

All values are paired AI-assisted LOCUS-X versus statistical LOCUS-X on identical validation frames, with run-level bootstrap intervals. Negative relative values are improvements.

| Variant | Overall | Majority NLOS | All NLOS | NLOS+packet | 6 anchors | 8 anchors |
|---|---:|---:|---:|---:|---:|---:|
| v1 | -0.040% | -0.353% | +0.132% | +0.061% | -0.229% | -0.390% |
| G0 full | -0.071% | -0.301% | +0.043% | +0.019% | +0.017% | -0.499% |
| G0 map-free | -0.125% | -0.438% | +0.005% | -0.042% | +0.002% | -0.749% |
| G2 | -0.214% | -0.708% | -0.016% | -0.062% | +0.066% | -1.076% |
| G3 | -0.141% | -0.471% | +0.013% | -0.040% | -0.080% | -0.911% |
| G4 | -0.110% | -0.381% | -0.003% | -0.023% | +0.013% | -0.652% |
| **G5** | **-0.268%** | **-0.789%** | **-0.034%** | **-0.220%** | **+0.004%** | **-1.529%** |
| G1 | -0.133% | -0.489% | +0.021% | -0.039% | -0.043% | -0.942% |
| G1+G5 | -0.227% | -0.776% | +0.002% | -0.123% | +0.016% | -1.264% |
| All map-free | -0.199% | -0.691% | -0.004% | -0.124% | -0.017% | -1.246% |
| All map-inclusive | -0.222% | -0.599% | -0.048% | -0.193% | -0.014% | -1.306% |

G5 intervals: overall `[-0.351%, -0.196%]`, majority NLOS `[-0.944%, -0.643%]`, all NLOS `[-0.114%, +0.036%]`, NLOS+packet `[-0.326%, -0.113%]`, NLOS+packet+outage `[-0.468%, +0.220%]`, 8 anchors `[-2.084%, -1.014%]`.

There are no `regressed` rows for G5 under the fixed rule. The largest relative worsening is at 6 anchors, `+0.004%`, far inside the `+0.5%` tolerance. Every layout is improved: hospital floor `-0.182%`, ground `-0.112%`, first `-0.123%`, second `-0.650%`. Coasting is `-0.078%` and unsupported; the gain is concentrated in solvable and packet-loss frames.

### Direct comparison with v1

The preregistered selection rule defaults to v1 unless a v2 candidate improves over v1 on the majority/all-NLOS focus. G5 passes that check:

| Scope | G5 minus v1 | 95% CI | v1 RMSE | G5 RMSE |
|---|---:|---:|---:|---:|
| Overall | -0.00731 m | `[-0.00990, -0.00480]` | 3.2025 | 3.1952 |
| Majority NLOS | -0.00885 m | `[-0.01180, -0.00603]` | 4.3707 | 4.3619 |
| All NLOS | -0.01067 m | `[-0.01271, -0.00881]` | 4.4063 | 4.3956 |
| NLOS+packet loss | -0.01439 m | `[-0.02116, -0.00757]` | 5.1305 | 5.1161 |

The all-NLOS result versus statistical LOCUS-X is not supported (`-0.034%`, interval crossing zero), but the all-NLOS result versus v1 is supported. This distinction is the main limit of the Phase 3 conclusion.

## Selection and promotion

The preregistered rule was applied without relaxation. All map-free candidates passed the no-regression screen; on the end-to-end AI-versus-statistical metric used by the rule, G5 had the largest supported majority-NLOS improvement (`-0.0303 m` paired difference), ahead of G1+G5 (`-0.0298 m`), G2 (`-0.0272 m`), and all-mapfree (`-0.0265 m`). G5 is also simpler than the tied alternatives and map-free. The separate G5-versus-v1 check above also passes on both majority and all-NLOS focus.

Promotion:

- Selected: `g5`, 26 features, 5 checkpoints.
- Installed: `models/ai-reliability-v2/` with `config.json`, five seed checkpoints, `selection.json`, and `promotion.json`.
- Load validation: `ReliabilityV2Model.load("models/ai-reliability-v2")` succeeds with no error.
- v1 remains untouched at `models/ai-reliability-v1/`. `backend/locus_x/session.py` was not modified.
- Runtime integration is Phase 4 work. The installed v2 artifact is not silently substituted for v1 by this phase.

The map-inclusive comparison is not selected. It has the best classifier ECE (`0.0255`) but a smaller majority-NLOS end-to-end gain (`-0.599%`) than G5 (`-0.789%`), no all-NLOS advantage, and 41 features. This is consistent with Phase 2: map features have small marginal value and are not the source of the classifier's skill.

## Latency

Measured on one validation run per layout and anchor count (24 runs), including feature extraction, two solves, tracker update, and model inference. The v1 control is computed through the shared v2 feature path for an identical pipeline; its total is therefore not a v1-only production latency.

| Variant | Features | Feature mean | Model mean | Total mean | Total P95 |
|---|---:|---:|---:|---:|---:|
| v1 | 24 | 2.381 ms | 0.147 ms | 9.065 ms | 12.076 ms |
| G5 | 26 | 2.720 ms | 0.554 ms | 10.580 ms | 14.159 ms |
| G1+G5 | 29 | 2.671 ms | 0.556 ms | 10.475 ms | 13.431 ms |
| All map-free | 39 | 2.704 ms | 0.544 ms | 10.875 ms | 14.923 ms |

G5 model inference is `0.554 ms` mean. The v2 feature path adds about `0.34 ms` mean feature time relative to the v1-style feature path in this shared harness; the total is dominated by the two solves and tracker update.

## Interpretation

1. **Reception and available-anchor count are the useful new information.** G5 is the strongest single-group candidate on the classifier and the end-to-end majority-NLOS and packet-loss slices. The gain is not from map features and does not require G1, G2, G3, or G4.
2. **Broad NLOS is still not shown to be identifiable.** The all-NLOS end-to-end change relative to statistical LOCUS-X is `-0.034%` with an interval crossing zero. The model improves over v1 there, but the absolute effect is small. This is consistent with the preregistered identifiability caveat: a common positive bias on all anchors is not exposed by the lower-bound/triangle checks.
3. **The 6-anchor fallback remains high.** About half of 6-anchor frames still fall back, despite better per-anchor classification. The Phase 2 6-anchor regression did not reproduce on this validation set, so no causal claim is made.
4. **Calibration improved but is not solved.** G5 cuts packet-loss ECE roughly in half and improves every anchor count except 5, where both v1 and v2 remain poorly calibrated. Per-anchor-count recalibration is a Phase 4 question.
5. **The selection is a real but modest win.** G5 improves overall RMSE by `0.268%` relative to statistical LOCUS-X and by `0.0073 m` relative to v1, with the largest gain at 8 anchors (`-1.529%`). It is not a transformation of the tracking result.

## Verification

- `pytest -q`: `91 passed`.
- `ruff check backend/locus_x backend/tests`: passed.
- `npm run typecheck`: passed.
- G0 check: `362705` samples, `max_abs_g0_difference = 0.0`, labels equal.
- v2 model load: available, no error, 41-feature schema with the selected G5 subset.
- Preregistered test seeds and Phase 1 seeds were not evaluated.
- v1 model directory, v1 feature/schema/policy/fallback, baseline, LOCUS-X, tracker, simulator, and earlier evidence were not modified.

## Artifacts

- `artifacts/ai-reliability/phase3-v2/dataset/train/manifest.json`
- `artifacts/ai-reliability/phase3-v2/dataset/validation/manifest.json`
- `artifacts/ai-reliability/phase3-v2/g0-control.json`
- `artifacts/ai-reliability/phase3-v2/classifier/metrics.csv`
- `artifacts/ai-reliability/phase3-v2/classifier/summary.csv`
- `artifacts/ai-reliability/phase3-v2/classifier/six-anchor.json`
- `artifacts/ai-reliability/phase3-v2/calibration/by-anchor-count.csv`
- `artifacts/ai-reliability/phase3-v2/end-to-end/metrics.csv`
- `artifacts/ai-reliability/phase3-v2/end-to-end/selected-vs-v1.json`
- `artifacts/ai-reliability/phase3-v2/end-to-end/six-anchor.json`
- `artifacts/ai-reliability/phase3-v2/latency.json`
- `artifacts/ai-reliability/phase3-v2/selection.json`
- `models/ai-reliability-v2/`

## Phase 4 proposal: not started

Phase 4 should be a controlled integration and observability phase, not another feature search:

1. Add an opt-in v2 inference path behind a configuration flag; keep v1 as default until shadow-mode checks pass.
2. Re-run the frozen-v1/v2 equivalence and fallback tests through the real session path, without changing policy or thresholds.
3. Collect shadow-mode agreement, fallback rate, packet-loss calibration, and 6-anchor diagnostics; stop if the installed model's behavior differs from the Phase 3 harness.
4. Investigate 5-anchor calibration and the remaining post-gap false-LOS tail as separate, preregistered questions.
5. Only then consider a default switch. Broad NLOS should stay an open identifiability question unless Phase 4 adds an independent observable.

No Phase 4 implementation was performed in this phase.
