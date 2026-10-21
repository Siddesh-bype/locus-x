# AI reliability: Phase 3 v2 feature plan

Status: planning only. This plan is written before any Phase 3 data generation or experiment. Phase 2 is complete at `468f7e7`; its report is `docs/ai-reliability-phase2.md`. The Phase 3 addendum is incorporated here and overrides the earlier plan where they conflict.

## What Phase 2 established

### (a) Skill without map features

Dropping the two map features from the full classifier cost only `0.00066` ROC-AUC (`0.99389` to `0.99309`), `0.0048` F1, and `0.0028` ECE. Map-only scored `0.6721` AUC and wall-count-only scored `0.6599` AUC, both with F1 `0.0`. The full end-to-end change was `-0.0099 m` overall and `-0.0554 m` at 8 anchors; the no-map variant was `-0.0082 m` and `-0.0426 m`. Most ranking skill survives without map features, so the classifier is not merely rediscovering the simulator wall rule. Map features have only a small marginal value, so the default v2 candidate family is map-free; map features appear only in a labelled comparison variant.

### (b) Post-gap over-trust

Part D of the Phase 2 report gives these frozen-v1 packet-gap diagnostics:

| Frames since previous observation | Frames | AUC | F1 | ECE | Mean p(NLOS) | True NLOS rate | False LOS among true NLOS |
|---|---:|---:|---:|---:|---:|---:|---:|
| 0 (first/no prior) | 3,188 | 0.9739 | 0.8875 | 0.0290 | 0.3531 | 0.3614 | 10.9% |
| 1 (adjacent) | 111,298 | 0.9949 | 0.9504 | 0.0278 | 0.3456 | 0.3734 | 7.7% |
| 2-3 | 12,250 | 0.9814 | 0.8947 | 0.0753 | 0.3816 | 0.4569 | 17.3% |
| 4+ | 2,564 | 0.9740 | 0.8691 | 0.0561 | 0.2987 | 0.3467 | 18.6% |

About `16.7%` of post-gap frames have a zero previous residual after the gap. The over-trust diagnostic flags `8.98%` of post-gap NLOS frames that get `p(NLOS) < 0.5`, with mean predicted NLOS probability `0.884` on the missed NLOS rows. The 2-3 and 4+ buckets therefore contain real over-trust on a subset, but their mean predicted p(NLOS) is still below the true NLOS rate, so the bucket-level story is under-confidence plus a hard missed-NLOS tail rather than uniform overconfidence. G1 remains a plausible but indirect fix and is scheduled last; G5, G2, G3 and G4 are tried first because they expose the missingness, range-history and consistency information directly.

### (c) Packet-loss calibration

ECE was `0.0167` without packet loss and `0.0871` with packet loss. Reweighting no-loss predictions to the packet-loss available-anchor mix reduced ECE to `0.0229`; frames-since-gap reweighting gave `0.0221`, and geometry reweighting gave `0.0178`. Most of the degradation is a changed availability mix, but packet-loss rows also had a higher NLOS rate (`44.2%` versus `36.3%`) and the model under-predicted it. G5 and the explicit available-anchor-count feature are therefore calibration and missingness hypotheses, not just extra classifier inputs.

### (d) The 6-anchor regression

Phase 2's end-to-end benefit was large at 8 anchors, small at 4, and absent below 4, with a small regression at 6. The cause is not established. Phase 3 will test, on validation data only, whether at 6 anchors the unchanged `predicted_los < 3` fallback fires unusually often, whether anchors are misclassified, and whether the per-frame NLOS-count mix differs from the other counts. Any explanation is reported as a hypothesis unless the numbers settle it. Every v2 candidate is checked for whether it removes the regression.

### (e) Identifiability boundary

Broad or majority NLOS may be unidentifiable from these observations when all reporting anchors share a common positive bias: range residuals, triangle inequalities and tracker residuals then move together, and there is no independent anchor to expose the common offset. A clean result that says "not identifiable from these observables" is an acceptable Phase 3 outcome. The rule below is not to be relaxed toward a positive result.

## Order of work

The default candidate family is map-free. G0 full is retained only as the exact v1 reproduction control; the main ladder starts from G0 without map features.

1. Freeze the v2 schema and implement the causal v2 adapter without changing v1.
2. Generate expanded validation data with the preregistered extra seeds and anchor counts 3-8; train on the existing training protocol and use run-grouped CV for decisions.
3. Reproduce G0 full and compare it with the saved v1.
4. Run the ladder in this order: G2 (range history), G3 (cross-anchor consistency), G4 (tracker consistency), G5 (reception and available-anchor count), then G1 (missingness) last. At each stage use one group at a time on the map-free G0 baseline, plus the combined map-free candidate.
5. Add and report the map-inclusive comparison variant only as a labelled comparison, not as a main candidate.
6. Evaluate classifier, per-anchor-count calibration, the 6-anchor diagnostic, end-to-end, focus tables and latency under the fixed rule below.
7. Promote nothing unless the rule is met. Otherwise leave v1 in place and report the negative or unidentifiable result.

## Pre-registered selection rule

This rule is fixed before experiments and must not be relaxed after seeing results.

1. **No practically meaningful regression at any anchor count.** For every validation focus slice (overall, solvable, coasting, each layout, each focus fault, and anchor counts 3, 4, 5, 6, 7, 8), compute the paired relative change `100 * (AI RMSE - LOCUS-X RMSE) / LOCUS-X RMSE` and its run-level 95% bootstrap interval. A slice is a disqualifying regression when the interval is entirely above zero **and** the relative worsening is greater than `0.5%`. The tolerance is therefore `+0.5%` relative RMSE for every slice, including each individual anchor count. Changes within `+/-0.5%` that are statistically detectable are reported but do not by themselves disqualify a variant. Any larger significant regression disqualifies it.
2. **Largest mean paired improvement.** Among variants that pass (1), choose the largest mean paired improvement on solvable majority-NLOS and all-NLOS frames, then on the mixed NLOS+packet-loss and NLOS+packet-loss+outage frames. Gains must be supported by intervals that exclude zero; a large point estimate with an interval crossing zero is not a win.
3. **Smaller, simpler and map-free first.** If two variants are practically tied on (2), prefer the map-free variant within the same tolerance, then fewer features, then lower mean and P95 inference latency. Map-free is preferred because it is simpler and layout-independent; map features are not part of the default candidate family.
4. **Default to v1.** If no v2 variant passes (1) and shows a supported improvement over v1 on the majority/all-NLOS focus, v1 remains the deployed model. Phase 3 then reports that the new features did not help or that broad NLOS is not identifiable. Do not lower the bar because no candidate wins.

## Data protocol

- Extra validation seeds: `6007, 6011, 6019, 6029, 6037, 6043`, preregistered in `configs/ai/validation-seeds-extra-v1.json` (commit `e6ed695`). They are validation only, not test.
- Existing validation seeds `11, 22` remain validation. Phase 2 test seeds `8001..8081` are untouched and are not used for any decision.
- Training data remains the v1/Phase 2 training protocol with seeds `1-4`, anchor counts 3/4/6/8, and the Phase 2 training variants. Phase 3 adds no test seed and no inspected Phase 1 seed.
- Validation uses all four layouts, anchor counts 3, 4, 5, 6, 7 and 8, 120 steps at `dt=0.5`, and at minimum: `nlos`, `nlos_packet`, `nlos_packet_outage`, `packet_loss`, `packet_outage`, `noise_nlos_packet`, `all_four`, plus the existing clean/noise/high-noise/anchor-failure/chaos controls. Anchor count 5 and 7 subsets are preregistered in the v2 dataset config; they are validation-only and are not used for training.
- Splits are by whole run and identical across every variant. Preprocessing and calibration are fitted on training partitions only. Every learned variant uses five training seeds and the same hyperparameter budget.
- Artifacts go under `artifacts/ai-reliability/phase3-v2/`; the selected model goes under `models/ai-reliability-v2/` only after the rule is satisfied. Experimental models stay under artifacts.

## Feature schema v2

The v2 schema keeps the 24 v1 features in their original order and appends the new groups. Every feature is causal at frame `t`; later observations are never used. The detailed name/unit/order/source/window/missing-value table is written with the implementation in `docs/ai-reliability-phase3.md`.

- **G0 (24 features):** unchanged v1 schema and order; reproduced as the control. The map-free baseline drops the two map features, leaving 22 features. The main v2 candidates are map-free; the map-inclusive variant is labelled separately.
- **G2 range history (5 features):** per-anchor causal short-window mean, variance, last range difference, range rate, and implied speed compared with the configured maximum cart speed. The speed bound comes from the simulator's configured maximum speed, a physical limit, not route truth.
- **G3 cross-anchor consistency (2 features):** per-anchor count and magnitude of triangle-inequality violations over available anchor pairs. The doc will state that a common positive bias on all anchors is not exposed by the lower-bound check, so this group may fail to identify broad NLOS.
- **G4 tracker consistency (3 features):** current range minus the range predicted from the previous LOCUS-X tracker state, using the statistical tracker path that AI does not influence. A feedback test is mandatory.
- **G5 reception pattern (4 features):** per-anchor recent receive/miss history, the current receive state, and the number of anchors available this frame, normalised by the total anchor count. The explicit available-anchor-count feature is included for the per-anchor-count calibration diagnostic.
- **G1 missingness (3 features):** `has_previous_observation`, capped `frames_since_last_observation`, and `previous_residual_missing`. First frame and never-seen anchors use explicit indicators and documented neutral values; zero is never silently used as unknown. G1 is scheduled last.

## Experiments and outputs

1. G0 full control versus saved v1; agreement must be within the five-seed spread.
2. Map-free ladder in the fixed order G2, G3, G4, G5, G1; each group alone on the map-free G0 baseline; the combined map-free candidate; and a map-inclusive comparison variant. Also include the minimum needed candidate (G0 map-free plus G1 and G5) where it aids interpretation.
3. Classifier metrics overall, by scenario, layout, anchor count, and solvable-frame NLOS groups one/minority/half/majority/all: precision, recall, F1, ROC-AUC, AP, Brier, ECE, mean and spread.
4. Calibration no-loss versus packet-loss, v1 versus every v2 candidate, with and without G1/G5, plus reliability diagrams. Calibration is fitted on training only and is not changed in Phase 3. Additionally report ECE and reliability data stratified by available anchor count 3, 4, 5, 6, 7 and 8 for no-loss and packet-loss conditions, for v1 and each v2 candidate. State whether adding the available-anchor-count feature reduces the packet-loss ECE increase and whether per-anchor-count calibration looks necessary; do not implement a new calibrator in Phase 3.
5. End-to-end with unchanged v1 policy, solvable versus coasting, paired run-level bootstrap intervals with at least 2,000 resamples, labelled improved/unchanged/regressed. Check every anchor count separately against the `+0.5%` tolerance.
6. Focus tables for majority NLOS, all NLOS, NLOS+packet loss, NLOS+packet-loss+outage, and anchor counts 3/4/5/6/7/8.
7. The 6-anchor diagnostic on validation only: fallback rate (fewer than three predicted LOS), anchor misclassification rate, and per-frame NLOS-count mix; report whether any v2 candidate fixes the regression.
8. Latency for v2 features plus inference including the preliminary solve: mean, median, P95.

The output report is `docs/ai-reliability-phase3.md`. It will state whether majority/broad NLOS improved, stayed unchanged, or appears unidentifiable from these observables. Phase 4 policy work is out of scope.
