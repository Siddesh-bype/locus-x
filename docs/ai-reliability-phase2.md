# AI reliability: Phase 2 feature and layout ablations

Status: Phase 2 complete. The frozen v1 model (`ai-reliability-v1`), 24-feature adapter, weight policy, baseline, tracker, simulator, and policy/schema contracts were not changed. No Phase 2 ablation model was installed under `models/`. Phase 3 and v2 training have not started.

## Protocol and provenance

- Baseline freeze: `72086c3e81e80777d9ddc9a95d178fd8c53c0440`.
- Phase 1 evidence: `docs/ai-reliability-phase1.md`; frozen artifacts in `artifacts/ai-reliability/evidence-v1-fresh/`.
- Phase 2 reserved-test preregistration: `1c24eb4b3b4e59b7dd9a0f57822218e12fb7c90f`.
- Phase 2 ablation code: `74d245b` (`backend/locus_x/ai_ablation.py`, `backend/locus_x/ai_ablation_diagnostics.py`, `backend/tests/test_ablation.py`).
- Reserved v2 test seeds `8001, 8009, 8017, 8023, 8039, 8053, 8069, 8081` were not evaluated. Phase 1 seeds `7001..7069` are not used for any Phase 2 decision.
- Decisions use validation seeds `11, 22`. Part A uses the v1 training split with a grouped 80/20 calibration split; Part B uses the Phase 2 training split with whole-layout holdout. Splits and preprocessing are fitted on training partitions only.
- Part A trains five seeds (`20261021..20261025`) for every learned variant. Part B trains five seeds for the `full` and `drop_map` variants.
- Phase 2 data: 1,920 training runs / 362,705 samples / 100,224 NLOS; 864 validation runs / 129,300 samples / 49,200 NLOS. Each run has 50 frames at `dt=0.5`. Dataset hashes are recorded in the split manifests.
- The end-to-end comparison uses 864 validation runs and 43,200 frames per model. It is AI-assisted LOCUS-X versus statistical LOCUS-X on the same tracked frames, with the unchanged v1 policy (`floor=0.05`, `threshold=0.05`, ambiguous when fewer than three anchors are predicted LOS). Negative `RMSE(AI) - RMSE(LOCUS-X)` is better. Intervals are paired run-level bootstrap, 95% percentile.
- Artifacts are in `artifacts/ai-reliability/ablation-v1/`. No existing Phase 1 or v1 artifact was overwritten.

## Part A: feature-group ablations

Each learned variant uses the same MLP architecture, hyperparameters, training rows, and five seeds; only the input feature group changes. The frozen v1 reference is scored on the same Phase 2 validation rows but its calibration was fitted during the original v1 work, so it is descriptive rather than a clean retrain comparison.

| Variant | AUC mean +/- SD | AP | F1 | Brier | ECE |
|---|---:|---:|---:|---:|---:|
| `full` | 0.9939 +/- 0.0005 | 0.9903 | 0.9326 | 0.0378 | 0.0445 |
| `drop_map` | 0.9931 +/- 0.0010 | 0.9891 | 0.9275 | 0.0401 | 0.0473 |
| `drop_temporal` | 0.9925 +/- 0.0004 | 0.9882 | 0.9267 | 0.0416 | 0.0572 |
| `drop_geometry` | 0.9830 +/- 0.0013 | 0.9759 | 0.8950 | 0.0560 | 0.0577 |
| `drop_residual` | 0.9758 +/- 0.0012 | 0.9596 | 0.8561 | 0.0727 | 0.0714 |
| `residual_only` | 0.9663 +/- 0.0023 | 0.9543 | 0.8433 | 0.0788 | 0.0788 |
| `temporal_only` | 0.8874 +/- 0.0021 | 0.8226 | 0.7185 | 0.1376 | 0.0770 |
| `geometry_only` | 0.7766 +/- 0.0070 | 0.7047 | 0.3476 | 0.2124 | 0.1702 |
| `map_only` | 0.6721 +/- 0.0015 | 0.4974 | 0.0000 | 0.2339 | 0.1491 |
| `wall_count_only` | 0.6599 +/- 0.0000 | 0.4715 | 0.0000 | 0.2353 | 0.1498 |
| `residual_threshold` | 0.7396 +/- 0.0000 | 0.5644 | 0.6815 | 0.2500 | 0.2450 |
| `majority_class` | 0.5000 +/- 0.0000 | 0.3463 | 0.0000 | 0.2435 | 0.0883 |

Bootstrap changes versus `full` are in `classifier/bootstrap-differences.csv`. The largest losses are from removing residual (`AUC -0.0154`, `F1 -0.0743`) or geometry (`AUC -0.0098`, `F1 -0.0389`). Removing temporal is smaller (`AUC -0.0011`, `F1 -0.0085`) but worsens ECE by `0.0127`. Removing map costs `AUC -0.00066`, `F1 -0.0048`, and `ECE +0.0028`; the intervals exclude zero, but the effect is an order of magnitude smaller than removing residual or geometry.

The single-group models do not reproduce the full model. `residual_only` is the strongest single group but still loses `0.0270` AUC and `0.0943` F1. `map_only` and `wall_count_only` are close to chance in ranking and never reach the classifier decision threshold. The frozen v1 reference on these rows has AUC `0.9931`, F1 `0.9414`, Brier `0.0334`, and ECE `0.0323`.

The `residual_threshold` heuristic chooses its cutoff using validation labels and is then scored on those same rows, so its classifier and end-to-end rows are optimistic. It still regresses end-to-end, which makes that conclusion stronger rather than weaker.

By layout, `full` AUC is `0.9964` (`first`), `0.9962` (`ground`), `0.9895` (`hospital_floor_01`), and `0.9924` (`second`). `drop_map` is `0.9959`, `0.9957`, `0.9873`, and `0.9920` respectively. By scenario the full model reaches AUC `0.9976` on `nlos`, `0.9957` on `chaos`, and `0.9763` on `packet_loss`; map removal leaves `0.9962`, `0.9952`, and `0.9745`. Full per-layout and per-scenario rows, including standard deviations and calibration, are in `classifier/summary.csv`.

## Part B: leave-one-layout-out

| Model | Held-out layout | Reference AUC | LOO AUC | Drop | Reference F1 | LOO F1 |
|---|---|---:|---:|---:|---:|---:|
| `full` | `ground` | 0.9962 | 0.9957 | -0.0005 | 0.9561 | 0.9493 |
| `full` | `first` | 0.9961 | 0.9959 | -0.0003 | 0.9566 | 0.9481 |
| `full` | `second` | 0.9942 | 0.9924 | -0.0019 | 0.9328 | 0.9128 |
| `full` | `hospital_floor_01` | 0.9882 | 0.9593 | **-0.0289** | 0.8984 | 0.7299 |
| `drop_map` | `ground` | 0.9954 | 0.9949 | -0.0005 | 0.9528 | 0.9452 |
| `drop_map` | `first` | 0.9957 | 0.9948 | -0.0009 | 0.9553 | 0.9473 |
| `drop_map` | `second` | 0.9934 | 0.9920 | -0.0014 | 0.9314 | 0.9129 |
| `drop_map` | `hospital_floor_01` | 0.9861 | 0.9543 | **-0.0318** | 0.8874 | 0.7100 |

Holding out the original hospital floor is the only large transfer failure. Its F1 falls by `0.169` for `full` and `0.177` for `drop_map`, and ECE rises by `0.085` and `0.091`. Removing map does not close that gap: the held-out-hospital AUC drop is slightly larger without map. The other three layouts transfer with small ranking losses, although calibration and F1 still worsen. Exact spread over five seeds and bootstrap intervals are in `loo/summary.csv` and `loo/drops.csv`.

## Part C: end-to-end validation

| Model | Scope | Frames | LOCUS-X RMSE | AI RMSE | Change | 95% CI | Update rate (L / AI) |
|---|---|---:|---:|---:|---:|---:|---:|
| `full` | overall | 37,807 | 2.8855 | 2.8757 | **-0.0099 m (+0.34%)** | [-0.0139, -0.0060] | 0.681 / 0.681 |
| `full` | solvable | 29,808 | 2.7130 | 2.6994 | -0.0135 m (+0.50%) | [-0.0187, -0.0086] | 0.987 / 0.987 |
| `full` | coasting | 7,999 | 3.4534 | 3.4540 | +0.0006 m (-0.02%) | [-0.0014, +0.0027] | 0.000 / 0.000 |
| `drop_map` | overall | 37,807 | 2.8855 | 2.8774 | -0.0082 m (+0.28%) | [-0.0118, -0.0049] | 0.681 / 0.681 |
| `drop_geometry` | overall | 37,807 | 2.8855 | 2.8760 | -0.0095 m (+0.33%) | [-0.0154, -0.0050] | 0.681 / 0.681 |
| `residual_only` | overall | 37,807 | 2.8855 | 2.8844 | -0.0011 m (+0.04%) | [-0.0023, -0.0003] | 0.681 / 0.681 |
| `residual_threshold` | overall | 37,807 | 2.8855 | 2.8954 | **+0.0099 m (-0.34%)** | [+0.0008, +0.0198] | 0.681 / 0.680 |
| `map_only` | overall | 37,807 | 2.8855 | 2.8855 | 0.0000 m | [0.0000, 0.0000] | 0.681 / 0.681 |

The `map_only`, `wall_count_only`, `temporal_only`, `geometry_only`, and `majority_class` variants produce essentially no end-to-end change because they do not cross the unchanged policy threshold often enough to alter weights. Classifier AUC alone therefore does not imply tracking benefit. The crude `residual_threshold` rule is actively worse on solvable frames and packet loss.

Full-model results by fault class:

| Variant | Frames | LOCUS-X RMSE | AI RMSE | Change | 95% CI |
|---|---:|---:|---:|---:|---:|
| `nlos` | 2,400 | 3.1119 | 3.0877 | -0.0242 m (+0.78%) | [-0.0425, -0.0100] |
| `nlos_outage` | 2,040 | 3.2850 | 3.2547 | -0.0303 m (+0.92%) | [-0.0510, -0.0138] |
| `nlos_packet` | 2,143 | 3.9104 | 3.9068 | -0.0036 m (+0.09%) | [-0.0129, +0.0034] |
| `nlos_packet_outage` | 1,752 | 4.1526 | 4.1463 | -0.0063 m (+0.15%) | [-0.0161, -0.0006] |
| `noise_nlos_packet` | 2,142 | 3.9025 | 3.8856 | -0.0169 m (+0.43%) | [-0.0510, +0.0005] |
| `all_four` / `chaos` | 1,909 | 5.5653 | 5.5598 | -0.0056 m (+0.10%) | [-0.0204, +0.0083] |
| `packet_loss` (no NLOS) | 2,144 | 0.6317 | 0.6317 | 0.0000 m | [0.0000, 0.0000] |

The statistically reliable gains are concentrated in NLOS and NLOS-with-outage frames. The NLOS-packet-loss gain is small and its interval crosses zero. The model still helps when outage is added to NLOS without packet loss. On frames with no NLOS, packet loss alone produces no change.

Full-model results by anchor count:

| Anchors | Frames | LOCUS-X RMSE | AI RMSE | Change | 95% CI |
|---|---:|---:|---:|---:|---:|
| 3 | 10,748 | 2.9737 | 2.9737 | 0.0000 m | [0.0000, 0.0000] |
| 4 | 12,787 | 2.8043 | 2.8032 | -0.0012 m (+0.04%) | [-0.0021, -0.0004] |
| 6 | 7,108 | 2.8725 | 2.8777 | **+0.0053 m (-0.18%)** | [+0.0004, +0.0123] |
| 8 | 7,164 | 2.9069 | 2.8516 | **-0.0554 m (+1.90%)** | [-0.0772, -0.0373] |

The 8-anchor gain is the dominant end-to-end effect and agrees with Phase 1. The 6-anchor regression also persists. `drop_map` retains a significant 8-anchor gain (`-0.0426 m`) but smaller than `full` (`-0.0554 m`). Exact per-layout, per-scenario, per-variant, and variant-by-anchor rows are in `end-to-end/metrics.csv`; all 12 variants are included there.

## Packet-gap and calibration diagnostics

These diagnostics use the frozen v1 model and simulator truth only for scoring. They are isolated in `backend/locus_x/ai_ablation_diagnostics.py`; inference does not import that module.

| Condition | Frames | AUC | F1 | Brier | ECE | Mean p(NLOS) | True NLOS rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| No packet loss | 100,692 | 0.9969 | 0.9628 | 0.0203 | **0.0167** | 0.3463 | 0.3630 |
| Packet loss | 28,608 | 0.9784 | 0.8740 | 0.0794 | **0.0871** | 0.3551 | 0.4422 |

Reweighting the no-loss predictions to the packet-loss mix of available-anchor counts lowers ECE from `0.0871` to `0.0229`; reweighting by frames-since-gap gives `0.0221`; reweighting by geometry quintile gives `0.0178`. Most of the apparent packet-loss miscalibration is therefore the changed availability mix, not a broken probability scale, although the packet-loss rows also have a higher NLOS prevalence and the model under-predicts that base rate.

Post-gap behavior:

| Frames since previous observation | Frames | ECE | False LOS among true NLOS |
|---|---:|---:|---:|
| 0 (first/no prior) | 3,188 | 0.0290 | 10.9% |
| 1 (adjacent) | 111,298 | 0.0278 | 7.7% |
| 2-3 | 12,250 | 0.0753 | 17.3% |
| 4+ | 2,564 | 0.0561 | 18.6% |

About `16.7%` of post-gap frames have a zero previous residual after the gap. The over-trust diagnostic flags the `8.98%` of post-gap NLOS frames that get `p(NLOS) < 0.5`, with mean predicted NLOS probability `0.884` on the missed NLOS rows. Calibration degrades most at 2-3 frames since the previous observation. These are diagnostics only; no indicator was added to the v1 feature schema.

## Interpretation

1. The classifier is not a wall-count rule. `map_only` AUC `0.672` and `wall_count_only` AUC `0.660` are far below `full` `0.994`, and both have zero F1. Map features alone do not explain the learned ranking.
2. Map features still have a small reproducible marginal value. Removing them costs `0.00066` AUC, `0.0048` F1, and `0.0028` ECE, and reduces the 8-anchor end-to-end gain from `-0.0554 m` to `-0.0426 m`. This is real but much smaller than residual or geometry.
3. The skill is distributed across interacting groups. Residual and geometry removal cause the largest ranking losses; temporal removal mainly hurts calibration; every single-group model is substantially worse than `full`.
4. Layout transfer is not solved by removing map. The original hospital floor remains the difficult holdout: LOO AUC `0.959` (`full`) and `0.954` (`drop_map`) versus in-distribution references above `0.986`. Other layouts transfer with small AUC changes.
5. End-to-end benefit is narrow and consistent with Phase 1: large at 8 anchors, small at 4, no effect below 4, and a small regression at 6. NLOS improvement is reliable, but NLOS plus packet loss is not reliably improved.
6. Packet-loss calibration shift is mostly an available-anchor distribution shift. The model nevertheless under-predicts the higher NLOS base rate and is overconfident on some post-gap misses. This remains a diagnostic finding, not a policy change.
7. All data are simulator-generated. These ablations describe simulator skill and do not establish real-sensor generalization.

No ablation model was promoted, no policy was retuned, and no schema or baseline changed. Phase 3 remains out of scope.

## Verification and reproduction

- `pytest -q`: 75 passed (includes five new ablation invariants: exact feature masking, deterministic whole-run calibration splits, split invariance across variants, unknown-feature rejection, and inference not importing diagnostics).
- `ruff check backend/locus_x`: passed.
- `npm run typecheck`: passed.
- `npm run lint`: passed.
- `npm test -- --run`: 24 passed.
- `npm run build`: passed (existing Vite chunk-size warning only).
- Regenerate data: `python -m backend.locus_x.ai_ablation --generate` (writes only under `ablation-v1/dataset/`).
- Re-run Part A: `python -m backend.locus_x.ai_ablation --classifier`.
- Re-run Part B: `python -m backend.locus_x.ai_ablation --loo`.
- Re-run Part C: `python -m backend.locus_x.ai_ablation --end-to-end`.
- Re-run diagnostics: `python -m backend.locus_x.ai_ablation_diagnostics --run`.

Artifacts are force-added to git because the repository ignores `artifacts/`. Do not overwrite `evidence-v1-fresh/` or the frozen v1 model.
