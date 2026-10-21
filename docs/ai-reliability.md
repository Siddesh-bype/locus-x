# LOCUS-X AI reliability branch

## Scope

This document records the implemented LOS/NLOS reliability model, its data protocol, the inference contract and the measured end-to-end result. The model is a third method: it does not replace the geometric baseline or LOCUS-X. The AI branch adjusts the existing robust estimator's per-anchor weights and falls back to LOCUS-X when the model cannot establish a reliable consensus.

The implementation is in:

- `backend/locus_x/reliability.py`: feature extraction, model adapter, probability conversion and weight policy.
- `backend/locus_x/ai_training.py`: dataset generation, NumPy MLP/logistic training, calibration, ablation and model saving.
- `backend/locus_x/ai_evaluation.py`: held-out protocols, majority-bias analysis, validation-only policy tuning and final evaluation.
- `configs/ai/reliability-dataset-v1.json`: split and fault protocol.
- `models/ai-reliability-v1/`: trained weights, config and metrics.
- `artifacts/ai-reliability/`: dataset manifests, reference benchmark, policy tuning and final evaluation.

## Truth boundary and labels

Inference receives only observations, anchor geometry, the baseline estimate and optional map geometry. It never receives simulator truth, LOS/NLOS labels, injected bias, fault flags, future observations or the evaluation result.

Labels are generated independently of the model:

- `y = 1` (NLOS) iff an available observation has simulator `kind = "nlos"`.
- `y = 0` (LOS) iff an available observation has simulator `kind = "los"`.
- Disabled, outage and packet-loss observations are excluded from supervised training.

The evaluator keeps its simulator labels. They are not features and are not sent to the estimator.

## Dataset protocol

`configs/ai/reliability-dataset-v1.json` fixes the protocol:

- Training seeds: `1, 2, 3, 4`; validation seeds: `11, 22`; test seeds: `303, 404`.
- Training and evaluation are divided by run, not by random samples from the same run.
- The V3 `second` floor and its anchors are held out as an unseen layout.
- Faults include noise, high noise, NLOS, packet loss, anchor outage and combinations.
- The V3 layout is not assumed to match the original reference environment.

Generated splits:

| Split | Samples | LOS | NLOS | Runs |
|---|---:|---:|---:|---:|
| Train | 272,027 | 196,136 | 75,891 | 1,440 |
| Validation | 31,596 | 25,270 | 6,326 | 168 |
| Test | 63,776 | 51,308 | 12,468 | 336 |

Each manifest records the source run, seed, fault configuration, label and split. The canonical config hash recorded in the training manifest is `8f4103cb436fcf28e3edfa71958f9d541baab525ec2c5b1b3ac9e21a4cf62c1d`.

## Model

The saved model is a small MLP with 24 inputs, hidden layers 32 and 16, ReLU activations and one logit output. Platt calibration maps the logit to a probability. It is implemented in NumPy and saved as `weights.npz`; no training dependency is needed at inference time.

Feature groups:

- Residual: normalised range residual, absolute residual, instant reliability and residual sign.
- Geometry: normalised anchor position, relative bearing and geometry condition.
- Map: wall count and mean wall count on the anchor-to-position segment.
- Temporal: previous residual, residual delta, position delta and time delta.

The adapter converts `P(NLOS)` to `p_los = 1 - p_nlos`. The earlier inverted conversion caused the AI branch to downweight LOS anchors; that inversion was corrected and is covered by the final results.

Test-set classification metrics:

| Metric | Value |
|---|---:|
| Accuracy | 0.9654 |
| Precision | 0.9262 |
| Recall | 0.8943 |
| F1 | 0.9100 |
| ROC-AUC | 0.9895 |
| Brier score | 0.0271 |
| ECE | 0.0127 |
| Confusion | TP 11,150; FP 888; FN 1,318; TN 50,420 |

Model size is 14,307 bytes. The saved adapter benchmark measured 0.386 ms mean, 0.350 ms median and 0.434 ms p95 over 200 iterations. Latency is environment-dependent and is not a real-time guarantee.

## Weight policy

The AI output is a multiplicative modifier, not a replacement for the statistical weights:

```text
final_weight = statistical_weight * ai_weight
```

This preserves the dynamic range of the robust estimator. The selected policy is:

- `floor = 0.05`
- `threshold = 0.05`
- `ai_weight = 1` when `p_los >= threshold`
- otherwise `ai_weight = floor + (1 - floor) * p_los / threshold`
- if fewer than three anchors are predicted LOS, the frame falls back to LOCUS-X and is marked ambiguous

The policy was selected on validation seeds 11 and 22 only. Candidate policies were compared by mean relative RMSE improvement with the worst scenario tracked. The selected policy had validation mean improvement 2.17% and worst improvement 0.0%. It is deliberately conservative: three-anchor frames usually remain unchanged, and strong minority-NLOS cases receive most of the benefit.

## End-to-end results

Primary protocol: original reference floor, `held_out` route, test seeds 303 and 404. Values are tracked RMSE in metres. The AI branch is compared against LOCUS-X, not against the untracked geometric baseline.

| Condition | Anchors | LOCUS-X | LOCUS-X + AI | RMSE change (positive = improvement) |
|---|---:|---:|---:|---:|
| Clean | 8 | 0.0487 | 0.0487 | 0.0% |
| Moderate noise | 8 | 0.2546 | 0.2545 | +0.03% |
| High noise | 8 | 0.6886 | 0.6872 | +0.21% |
| NLOS | 8 | 1.9185 | 1.6840 | **+12.2%** |
| Packet loss | 8 | 0.1971 | 0.1972 | -0.07% |
| Anchor outage | 8 | 0.1480 | 0.1479 | +0.03% |
| Combined failures | 8 | 3.6803 | 3.4909 | **+5.15%** |
| NLOS + outage | 8 | 2.2075 | 2.0248 | **+8.28%** |
| NLOS + packet loss | 8 | 2.5941 | 2.8481 | **-9.79%** |
| NLOS + packet loss + outage | 8 | 2.9168 | 2.9530 | -1.24% |
| Noise + NLOS | 8 | 1.8928 | 1.6486 | **+12.90%** |
| Noise + NLOS + packet loss | 8 | 2.9409 | 2.4458 | **+16.83%** |

Unseen-layout evaluation on the V3 second floor also produced mixed results. At eight anchors, NLOS improved from **2.0712 m** to **1.5612 m** (+24.63%), noise + NLOS from **2.0247 m** to **1.6128 m** (+20.34%), and NLOS + packet loss from **2.3936 m** to **2.2732 m** (+5.03%). Combined failures regressed slightly from **4.4637 m** to **4.4922 m** (-0.64%).

Majority-bias analysis on the primary protocol:

| Group | Frames | LOCUS-X RMSE | AI RMSE | Change |
|---|---:|---:|---:|---:|
| One NLOS | 795 | 3.5821 | 3.5859 | -0.11% |
| Minority NLOS | 155 | 2.0214 | 1.9792 | +2.09% |
| Half NLOS | 555 | 1.6913 | 1.5307 | +9.50% |
| Majority NLOS | 6,133 | 3.0507 | 3.0168 | +1.11% |
| All NLOS | 2,556 | 3.1106 | 3.1156 | -0.16% |

The model does not solve the majority-bias problem. When most anchors are obstructed, the ambiguity rule often falls back, and the classifier can slightly worsen an already biased consensus.

## Interpretation

The AI branch is useful, but not universally better:

- It gives the largest gains when there are enough anchors, a minority are NLOS, and the model can identify them confidently.
- It is neutral on clean, moderate-noise and high-noise inputs under the selected conservative policy.
- It can regress when packet loss changes the available-anchor distribution or when the model misclassifies enough anchors to distort the current frame. The NLOS + packet-loss 8-anchor regression is the clearest example.
- It does not make three-anchor NLOS geometry identifiable. Falling back is the honest response.
- These are synthetic range experiments. They do not establish real UWB/BLE performance, real multipath behaviour, or calibration on a deployed hospital.

The final evaluation is saved in `artifacts/ai-reliability/final-evaluation/results.json`, with `metrics.csv`, `layout-generalization.csv` and `majority-bias.csv`. The reference historical benchmark is preserved in `artifacts/ai-reliability/reference-existing/`.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m backend.locus_x.ai_training --generate
.\.venv\Scripts\python.exe -m backend.locus_x.ai_training --train
.\.venv\Scripts\python.exe -m backend.locus_x.ai_evaluation --tune-policy --apply-policy
.\.venv\Scripts\python.exe -m backend.locus_x.ai_evaluation --evaluate
```

Training writes a new model directory; do not overwrite an existing released model unless intentionally creating a new version. The evaluation command writes the final artifacts under `artifacts/ai-reliability/final-evaluation/`.

## Verification

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check backend/locus_x
npm.cmd run typecheck
npm.cmd run lint
npm.cmd test
npm.cmd run build
```

The frontend displays the AI estimate, AI error trajectory, per-anchor p(LOS) and AI weight when present. If the model file is missing, the API exposes an explicit fallback state and the statistical method remains available.
