# AI reliability: Phase 3 v2 feature plan

Status: planning only. This plan is written before any Phase 3 data generation or experiment. Phase 2 is complete at `468f7e7`; its report is `docs/ai-reliability-phase2.md`.

## What Phase 2 established

### (a) Skill without map features

Dropping the two map features from the full classifier cost only `0.00066` ROC-AUC (`0.99389` to `0.99309`), `0.0048` F1, and `0.0028` ECE. Map-only scored `0.6721` AUC and wall-count-only scored `0.6599` AUC, both with F1 `0.0`. The full end-to-end change was `-0.0099 m` overall and `-0.0554 m` at 8 anchors; the no-map variant was `-0.0082 m` and `-0.0426 m`. Most ranking skill survives without map features, so the classifier is not merely rediscovering the simulator wall rule. Map features still have a small reproducible marginal value, especially at 8 anchors.

### (b) Post-gap over-trust

Frames 2-3 after an anchor was last seen had ECE `0.0753`; frames 4+ had `0.0561`; adjacent frames had `0.0278`. False-LOS rates among true NLOS were `17.3%` and `18.6%` for 2-3 and 4+ gaps. The over-trust diagnostic flagged `8.98%` of post-gap NLOS frames as `p(NLOS) < 0.5`, and `16.7%` of post-gap frames had a zero previous residual. Yes, some post-gap frames are over-trusted. G1 and G5 are designed to expose missingness and reception history explicitly instead of treating a reset-to-zero residual as normal.

### (c) Packet-loss calibration

ECE was `0.0167` without packet loss and `0.0871` with packet loss. Reweighting no-loss predictions to the packet-loss available-anchor mix reduced ECE to `0.0229`; frames-since-gap reweighting gave `0.0221`, and geometry reweighting gave `0.0178`. Most of the degradation is a changed availability mix, but packet-loss rows also had a higher NLOS rate (`44.2%` versus `36.3%`) and the model under-predicted it. G1/G5 are therefore calibration and missingness hypotheses, not just extra classifier inputs.

## Order of work

The Phase 2 evidence sets the priority: first make missingness/reception explicit (G1, G5), then add position-independent history and consistency clues (G2, G3), then tracker consistency (G4). At every stage the G0 control is the same code path and the same split. Map features stay in G0 for the main ladder; the best configuration is retrained without them only after selection.

1. Freeze the v2 schema and implement the causal v2 adapter without changing v1.
2. Generate expanded validation data with the preregistered extra seeds; train on the existing training protocol and use run-grouped CV for decisions.
3. Reproduce G0 and compare it with the saved v1.
4. Run the incremental ladder: G1, G2, G3, G4, G5, G1+G5, all groups, and each group alone with the minimum G0 baseline.
5. Re-run the selected best configuration without map.
6. Evaluate classifier, calibration, end-to-end, focus tables, and latency under the fixed rule below.
7. Promote nothing unless the rule is met. Otherwise leave v1 in place and report the negative result.

## Pre-registered selection rule

This rule is fixed before experiments and must not be relaxed after seeing results.

1. **No practically meaningful regression.** For every validation focus slice (overall, solvable, coasting, each layout, each focus fault, and anchor counts 3/4/6/8), compute the paired relative change `100 * (AI RMSE - LOCUS-X RMSE) / LOCUS-X RMSE` and its run-level 95% bootstrap interval. A slice is a disqualifying regression when the interval is entirely above zero **and** the relative worsening is greater than `0.5%`. Changes within `+/-0.5%` that are statistically detectable are reported but do not by themselves disqualify a variant. Any larger significant regression disqualifies it.
2. **Largest mean paired improvement.** Among variants that pass (1), choose the largest mean paired improvement on solvable majority-NLOS and all-NLOS frames, then on the mixed NLOS+packet-loss and NLOS+packet-loss+outage frames. Gains must be supported by intervals that exclude zero; a large point estimate with an interval crossing zero is not a win.
3. **Smaller size and latency.** If two variants are practically tied on (2), choose fewer features first, then lower mean and P95 inference latency.
4. **Default to v1.** If no v2 variant passes (1) and shows a supported improvement over v1 on the majority/all-NLOS focus, v1 remains the deployed model. Phase 3 then reports that the new features did not help. Do not lower the bar because no candidate wins.

## Data protocol

- Extra validation seeds: `6007, 6011, 6019, 6029, 6037, 6043`, preregistered in `configs/ai/validation-seeds-extra-v1.json` (commit `e6ed695`). They are validation only, not test.
- Existing validation seeds `11, 22` remain validation. Phase 2 test seeds `8001..8081` are untouched and are not used for any decision.
- Training data remains the v1/Phase 2 training protocol with seeds `1-4`; Phase 3 adds no test seed and no inspected Phase 1 seed.
- Validation includes all four layouts, anchor counts 3/4/6/8, 120 steps at `dt=0.5`, and at minimum: `nlos`, `nlos_packet`, `nlos_packet_outage`, `packet_loss`, `packet_outage`, `noise_nlos_packet`, `all_four`, plus the existing clean/noise/high-noise/anchor-failure/chaos controls.
- Splits are by whole run and identical across every variant. Preprocessing and calibration are fitted on training partitions only. Every learned variant uses five training seeds and the same hyperparameter budget.
- Artifacts go under `artifacts/ai-reliability/phase3-v2/`; the selected model goes under `models/ai-reliability-v2/` only after the rule is satisfied. Experimental models stay under artifacts.

## Feature schema v2

The v2 schema keeps the 24 v1 features in their original order and appends the new groups. Every feature is causal at frame `t`; later observations are never used. The detailed name/unit/order/source/window/missing-value table is written with the implementation in `docs/ai-reliability-phase3.md`.

- **G0 (24 features):** unchanged v1 schema and order; reproduced as the control.
- **G1 missingness:** `has_previous_observation`, capped `frames_since_last_observation`, `previous_residual_missing`. First frame and never-seen anchors use explicit indicators and documented neutral values; zero is never silently used as unknown.
- **G2 range history:** per-anchor causal short-window mean, variance, last range difference, range rate, and implied speed compared with the configured maximum cart speed. The speed bound comes from the simulator's configured maximum speed, a physical limit, not route truth.
- **G3 cross-anchor consistency:** per-anchor count and magnitude of triangle-inequality violations over available anchor pairs. The doc will state that a common positive bias on all anchors is not exposed by the lower-bound check, so this group may fail to identify broad NLOS.
- **G4 tracker consistency:** current range minus the range predicted from the previous LOCUS-X tracker state, using the statistical tracker path that AI does not influence. A feedback test is mandatory.
- **G5 reception pattern:** per-anchor recent receive/miss history and the count of anchors reporting this frame.

## Experiments and outputs

1. G0 control versus saved v1; agreement must be within the five-seed spread.
2. Incremental G1/G2/G3/G4/G5 ladder, all groups, each group alone with the minimum baseline, and best configuration without map.
3. Classifier metrics overall, by scenario, layout, anchor count, and solvable-frame NLOS groups one/minority/half/majority/all: precision, recall, F1, ROC-AUC, AP, Brier, ECE, mean and spread.
4. Calibration no-loss versus packet-loss, v1 versus v2, with and without G1/G5, plus reliability diagrams. Calibration is fitted on training only.
5. End-to-end with unchanged v1 policy, solvable versus coasting, paired run-level bootstrap intervals with at least 2,000 resamples, labelled improved/unchanged/regressed.
6. Focus tables for majority NLOS, all NLOS, NLOS+packet loss, NLOS+packet-loss+outage, and anchor counts 3/4/6/8.
7. Latency for v2 features plus inference including the preliminary solve: mean, median, P95.

The output report is `docs/ai-reliability-phase3.md`. It will state whether majority/broad NLOS improved, stayed unchanged, or appears unidentifiable from these observables. Phase 4 policy work is out of scope.
