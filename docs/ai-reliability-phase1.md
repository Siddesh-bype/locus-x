# AI reliability: fresh-seed Phase 1 evidence

Status: Phase 1 complete. The frozen v1 model (`ai-reliability-v1`), its 24-feature adapter, and the existing weight policy were not changed or retuned. Phase 2 is documented separately in `docs/ai-reliability-phase2.md`.

## Provenance and protocol

- Baseline freeze: `72086c3e81e80777d9ddc9a95d178fd8c53c0440`.
- Fresh-seed preregistration commit: `fd8a6eb03cfa7443b7dff8ca0c1999e7b05e81e8`.
- Evidence-code commit recorded in the run manifest: `71432dc6b3d72d15944a8972119605186c591c7d`.
- Seeds: `7001, 7013, 7019, 7027, 7039, 7043, 7057, 7069`. These are fresh and were committed before evaluation. Seeds `303/404` remain labelled previously reported.
- Protocol: original floor plus V3 `ground`, `first`, and `second`; `held_out` route; 18 base/mixed variants; 3, 4, 6, and 8 anchors; 120 frames per run; 8 fresh seeds.
- Scale: **2,304 runs**, **276,480 frames**. All three methods consume the same observations in each frame; simulator truth and fault labels enter only evaluation.
- Uncertainty: 2,000 paired run-level bootstrap resamples, 95% percentile intervals for `RMSE(AI) - RMSE(LOCUS-X)`. Negative is better for AI. A scenario is `improved` when the whole interval is below zero, `regressed` when it is above zero, otherwise `unchanged`.
- Model checksums are in `manifest.json`. No existing v1 artifact or model file was overwritten.

Artifacts are in `artifacts/ai-reliability/evidence-v1-fresh/`. Per-run checkpoints are included so the aggregation can be reproduced without rerunning the simulator.

## Pooled result

| Method | Valid | RMSE | Median | P95 | Matched RMSE | Prediction-only n / RMSE | 95% coverage | Mean ellipse area | Latency mean / median / P95 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Geometric baseline | 217,004 | 2.6599 | 0.8356 | 5.7430 | 2.6599 | n/a | 74.62% | 49.91 m² | 1.046 / 0.992 / 2.472 ms |
| LOCUS-X | 260,880 | 3.0743 | 1.1758 | 6.3142 | 2.9026 | 45,054 / 3.8762 | 63.50% | 28.23 m² | 3.122 / 3.076 / 7.286 ms |
| LOCUS-X + AI | 260,878 | **3.0678** | **1.1374** | 6.3133 | **2.8942** | 45,186 / 3.8844 | **63.85%** | **28.14 m²** | 4.602 / 4.707 / 10.107 ms |

Pooled AI change: **-0.00649 m**, 95% CI **[-0.00915, -0.00390]**, relative improvement **+0.21%** — `improved`. On frames where all three methods have a fix, the change is **-0.00840 m**, 95% CI **[-0.01140, -0.00559]**.

The AI path is slower because its measured latency includes the preliminary solve, adapter, AI-weighted robust solve, and AI tracker. LOCUS-X latency is robust solve plus tracker. The saved 0.386 ms figure remains adapter-only and is not comparable to end-to-end latency.

## Per floor/layout

| Layout | Role | LOCUS-X RMSE | AI RMSE | Change | 95% CI (AI - LOCUS-X) | Decision |
|---|---|---:|---:|---:|---:|---|
| `hospital_floor_01` | original reference | 2.3408 | 2.3304 | +0.45% | [-0.0186, -0.0031] | improved |
| `ground` | in-distribution V3 | 3.2954 | 3.2865 | +0.27% | [-0.0124, -0.0056] | improved |
| `first` | in-distribution V3 | 3.3535 | 3.3646 | **-0.33%** | [+0.0067, +0.0171] | **regressed** |
| `second` | unseen V3 layout | 3.1969 | 3.1769 | +0.62% | [-0.0260, -0.0144] | improved |

The AI branch is not universally better. The V3 `first` floor regresses overall despite good individual NLOS results.

## Per fault variant

| Variant | Fault class | LOCUS-X RMSE | AI RMSE | Change | 95% CI (AI - LOCUS-X) | Decision |
|---|---|---:|---:|---:|---:|---|
| `all_four` | chaos | 6.0040 | 6.0045 | -0.01% | [-0.0117, +0.0140] | unchanged |
| `anchor_outage` | chaos | 0.2351 | 0.2351 | -0.01% | [-0.0000, +0.0000] | unchanged |
| `chaos` | chaos | 6.0040 | 6.0045 | -0.01% | [-0.0117, +0.0140] | unchanged |
| `clean` | clean | 0.0447 | 0.0447 | 0.00% | [0.0000, 0.0000] | unchanged |
| `high_noise` | high noise | 1.1880 | 1.1885 | -0.04% | [-0.0003, +0.0012] | unchanged |
| `nlos` | NLOS | 3.5242 | 3.5029 | +0.61% | [-0.0323, -0.0122] | improved |
| `nlos_outage` | NLOS + outage | 3.6775 | 3.6574 | +0.55% | [-0.0304, -0.0113] | improved |
| `nlos_packet` | NLOS + packet loss | 3.7763 | 3.7720 | +0.11% | [-0.0178, +0.0070] | unchanged |
| `nlos_packet_outage` | NLOS + packet loss + outage | 3.8550 | 3.8532 | +0.05% | [-0.0105, +0.0059] | unchanged |
| `noise` | noise | 0.3453 | 0.3455 | -0.05% | [+0.0000, +0.0004] | regressed |
| `noise_nlos` | noise + NLOS | 3.5306 | 3.5072 | +0.66% | [-0.0348, -0.0136] | improved |
| `noise_nlos_outage` | noise + NLOS + outage | 3.6907 | 3.6679 | +0.62% | [-0.0336, -0.0131] | improved |
| `noise_nlos_packet` | noise + NLOS + packet loss | 3.8105 | 3.8059 | +0.12% | [-0.0176, +0.0053] | unchanged |
| `noise_outage` | noise + outage | 0.4100 | 0.4102 | -0.04% | [+0.0000, +0.0003] | regressed |
| `noise_packet` | noise + packet loss | 0.7926 | 0.7926 | -0.01% | [-0.0002, +0.0003] | unchanged |
| `noise_packet_outage` | noise + packet loss + outage | 0.8271 | 0.8272 | -0.01% | [-0.0002, +0.0003] | unchanged |
| `packet_loss` | packet loss | 0.5831 | 0.5831 | -0.01% | [+0.0000, +0.0001] | regressed |
| `packet_outage` | packet loss + outage | 0.5988 | 0.5989 | -0.01% | [+0.0000, +0.0001] | regressed |

The statistically detectable regressions on `noise`, `noise_outage`, `packet_loss`, and `packet_outage` are very small in absolute terms (below 0.0004 m RMSE in these aggregates). They still mean the model is not a universal improvement.

## Sparse anchors

| Layout | Anchors | LOCUS-X RMSE | AI RMSE | Change | Decision |
|---|---:|---:|---:|---:|---|
| `hospital_floor_01` | 3 | 2.6532 | 2.6532 | 0.00% | unchanged |
| `hospital_floor_01` | 4 | 2.5165 | 2.5226 | -0.24% | regressed |
| `hospital_floor_01` | 6 | 2.2319 | 2.2232 | +0.39% | unchanged |
| `hospital_floor_01` | 8 | 1.9760 | 1.9311 | +2.27% | improved |
| `ground` | 3 | 3.7924 | 3.7924 | 0.00% | unchanged |
| `ground` | 4 | 3.4266 | 3.4266 | 0.00% | unchanged |
| `ground` | 6 | 3.2224 | 3.2275 | -0.16% | regressed |
| `ground` | 8 | 2.7736 | 2.7276 | +1.66% | improved |
| `first` | 3 | 3.5621 | 3.5621 | 0.00% | unchanged |
| `first` | 4 | 3.7407 | 3.7407 | 0.00% | unchanged |
| `first` | 6 | 3.2119 | 3.2269 | -0.47% | regressed |
| `first` | 8 | 2.8829 | 2.9148 | -1.11% | regressed |
| `second` | 3 | 3.6465 | 3.6465 | 0.00% | unchanged |
| `second` | 4 | 3.3968 | 3.3968 | 0.00% | unchanged |
| `second` | 6 | 3.0926 | 3.0913 | +0.04% | unchanged |
| `second` | 8 | 2.6738 | 2.5839 | +3.36% | improved |

The gains are concentrated at eight anchors. Three-anchor cases are identical because the fallback preserves LOCUS-X. Four- and six-anchor cases are mixed and include regressions. At eight anchors, the NLOS-only variant improves by **4.48%** (AI-LOCUS-X -0.1234 m, 95% CI [-0.1599, -0.0861]); NLOS with four or six anchors regresses slightly.

## Group breakdown

`one_nlos_all` includes every frame with exactly one NLOS observation, including frames with too few available anchors to solve. `one_nlos_solvable` requires at least three available anchors.

| Group | Frames | LOCUS-X valid / RMSE | AI valid / RMSE | LOCUS-X prediction-only n / RMSE | AI prediction-only n / RMSE |
|---|---:|---:|---:|---:|---:|
| `one_nlos_all` | 10,860 | 8,235 / 4.8759 | 8,233 / 4.8712 | 7,762 / 4.9852 | 7,756 / 4.9822 |
| `one_nlos_solvable` | 500 | 500 / 2.7076 | 500 / **2.6557** | 27 / 5.3977 | 23 / 5.4699 |
| `one_nlos_coasting` | 10,360 | 7,735 / 4.9837 | 7,733 / 4.9807 | 7,735 / 4.9837 | 7,733 / 4.9807 |
| `minority_solvable` | 1,030 | 1,030 / 2.8158 | 1,030 / 2.8170 | 33 / 5.8260 | 48 / 6.6427 |
| `half_solvable` | 3,454 | 3,454 / 2.6820 | 3,454 / **2.5538** | 29 / 5.4462 | 43 / 5.7993 |
| `majority_solvable` | 103,720 | 103,720 / 4.1391 | 103,720 / **4.1295** | 1,162 / 6.0611 | 1,267 / 6.0158 |
| `all_nlos_solvable` | 58,896 | 58,896 / 4.4188 | 58,896 / 4.4286 | 560 / 5.5958 | 574 / 5.6959 |

The earlier negative "One NLOS" result was dominated by prediction-only frames: 10,360 of 10,860 one-NLOS frames have fewer than three available anchors. On solvable one-NLOS frames the AI branch improves RMSE by 1.9%. Full `(available anchors, NLOS anchors)` rows are in `groups.csv` and `groups.json`.

## Classifier evidence

Classifier samples use the 8-anchor runs only, so anchor-count resampling cannot duplicate rows. NLOS is the positive class. `clean`, `noise`, `high_noise`, and the no-NLOS fault variants have no positive labels, so AUC/AP are undefined and F1 at 0.5 is zero by construction.

Per layout:

| Layout | AUC | AP | F1@0.5 | Brier | ECE | Samples | Positives |
|---|---:|---:|---:|---:|---:|---:|---:|
| `first` | 0.9924 | 0.9894 | 0.9038 | 0.0599 | 0.0681 | 102,698 | 41,474 |
| `ground` | 0.9641 | 0.9312 | 0.8666 | 0.0817 | 0.0629 | 102,698 | 38,286 |
| `hospital_floor_01` | 0.9806 | 0.9615 | 0.8617 | 0.0688 | 0.0666 | 102,698 | 35,436 |
| `second` | 0.9816 | 0.9685 | 0.9039 | 0.0561 | 0.0409 | 102,698 | 39,301 |

Key fault variants:

| Variant | AUC | AP | F1@0.5 | Brier | ECE | Samples | Positives |
|---|---:|---:|---:|---:|---:|---:|---:|
| `nlos` | 0.9863 | 0.9947 | 0.9547 | 0.0531 | 0.0562 | 30,720 | 22,848 |
| `nlos_outage` | 0.9825 | 0.9948 | 0.9494 | 0.0606 | 0.0634 | 25,984 | 20,512 |
| `nlos_packet` | 0.9383 | 0.9715 | 0.7746 | 0.2255 | **0.2604** | 18,064 | 13,439 |
| `nlos_packet_outage` | 0.9468 | 0.9791 | 0.8345 | 0.1806 | **0.2126** | 14,320 | 11,143 |
| `noise_nlos` | 0.9819 | 0.9927 | 0.9393 | 0.0693 | 0.0801 | 30,720 | 22,848 |
| `all_four` | 0.9427 | 0.9765 | 0.9298 | 0.0863 | 0.0824 | 19,020 | 14,878 |

Calibration degrades sharply when packet loss is present. That is consistent with the weak and non-significant NLOS + packet-loss end-to-end result. Full per-variant and per-layout-variant metrics are in `classifier.csv`; PR points are in `pr-curves.json`; ten-bin reliability data are in `reliability.json`; raw classifier samples are in `classifier-samples.npz`.

## Interpretation

1. The fresh-seed pooled result is a small improvement: **+0.21%**, CI excludes zero but the effect is much smaller than the earlier 303/404 NLOS result.
2. Eight anchors are the useful operating point. The largest sparse-anchor gains are +2.27% on the original floor, +1.66% on V3 ground, and +3.36% on unseen V3 second. V3 first still regresses at eight anchors.
3. NLOS-only improves overall, and NLOS + outage also improves. NLOS + packet loss is not a confirmed improvement, and the classifier is poorly calibrated in that condition.
4. The model is neutral or microscopically worse on some clean/noisy/packet-only cases. The correct claim is conditional, not "AI always improves".
5. Three-anchor results are unchanged because fallback preserves LOCUS-X. Four- and six-anchor results are mixed. The model is not a substitute for anchor geometry.
6. The old `303/404` tables in `docs/ai-reliability.md` remain **previously reported** evidence. They are not the confirmatory fresh-seed result and must not be combined with this run.

## Artifact index

- `manifest.json`: protocol, seeds, model hashes, Git commit, counts, execution status.
- `metrics.csv` / `metrics.json`: 711 rows covering pooled, layout, scenario, variant, layout-scenario, layout-variant, layout-anchor, scenario-anchor, and variant-anchor scopes.
- `bootstrap.csv` / `bootstrap.json`: AI-vs-LOCUS-X differences, intervals, decisions.
- `sparse_anchors.csv`: 3/4/6/8-anchor results.
- `groups.csv`, `groups.json`, `named_groups.csv`: available/NLOS group analysis and coasting separation.
- `classifier.csv`, `pr-curves.json`, `reliability.json`, `classifier-samples.npz`: classifier evidence.
- `latency.json`: per-layout/per-scenario latency quantiles.
- `runs.csv`: per-run RMSE and duration.
- `checkpoints/`: 2,304 per-run NPZ files used to rebuild the aggregate without rerunning.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.ai_evidence --evaluate
```

This reuses existing checkpoints. To regenerate from the simulator, use a new `--output` directory rather than deleting or overwriting the frozen evidence. The seed manifest is intentionally left at `preregistered_unrun` as a record of when it was registered; `manifest.json` records the completed execution.

These are synthetic experiments. They do not establish real UWB/BLE accuracy, real multipath behaviour, browser/WebGL fallback behaviour, or deployment readiness.
