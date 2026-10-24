# AI reliability: Phases 4-6 plan

Status: policy rule fixed before policy evaluation. This file is committed before any
policy candidate is run.

## Scope

Phase 4 tunes only the v1/v2 weight policy on cached validation frames and adds missing
robustness tests. Phase 5 freezes the implementation and evaluates the reserved final-test
seeds once. Phase 6 reports all results, including negative results.

The v1 model, v1 schema, v1 adapter, v1 policy, geometric baseline, LOCUS-X estimator,
tracker, simulator, earlier seed manifests and Phase 1-3 evidence are frozen.

## Validation set

Policy tuning uses only the validation seeds `11, 22, 6007, 6011, 6019, 6029, 6037, 6043`.
The set is the cached Phase 3 validation frame matrix: four layouts, all 19 scenarios
(including `nlos_packet`, `nlos_packet_outage` and `all_four`), anchor counts `3-8`,
120 steps at `dt = 0.5`. Test seeds `8001, 8009, 8017, 8023, 8039, 8053, 8069, 8081` are
not evaluated during tuning. Seeds `7001-7069` remain report-only.

No classifier or calibrator training is performed in this phase.

## Candidate policy grid

Final weight is always `statistical_weight * ai_weight`.

Hard candidates:

- threshold in `{0.05, 0.1, 0.2, 0.35, 0.5}`
- floor in `{0.05, 0.2}`
- fallback minimum predicted-LOS anchors in `{3, 2 when at least 4 anchors are available}`
- `ai_weight = 1` when `p_los >= threshold`, otherwise
  `floor + (1 - floor) * p_los / threshold`

One soft candidate:

- floor `0.05`, fallback minimum predicted-LOS anchors `3`
- `ai_weight = max(floor, p_los)`

The unchanged v1 policy (`threshold=0.05`, `floor=0.05`, fallback `3`, hard) is included
as the current-policy control. That is 21 candidates in total.

For every candidate, report per anchor count:

- fallback rate;
- fraction of frames where any AI weight differs from `1`;
- active rate.

## Selection rule

The rule is applied in this order, without relaxation:

1. **No meaningful regression.** For every scenario x anchor-count slice and for every
   anchor count, compare the candidate with statistical LOCUS-X using a run-level paired
   bootstrap (2,000 resamples, 95% interval). A candidate is disqualified when the interval
   is entirely above zero and the relative worsening is greater than `+0.5%`. The overall
   slice is also checked as a guard.
2. **Largest mean paired improvement over the current policy.** On validation frames with
   at least three available anchors, compute the paired RMSE improvement
   `current_policy_rmse - candidate_rmse` for:
   - solvable mixed-fault frames (`n_nlos > 0` and dropout or outage present);
   - solvable majority-NLOS frames (`n_nlos > n_available / 2` and not all NLOS);
   - solvable all-NLOS frames (`n_nlos == n_available`).

   The score is the unweighted mean of the three paired differences. Positive is better.
   The current policy has score zero by construction.
3. **Lowest overall fallback rate.**
4. **Simplicity.** Prefer a hard policy over the soft policy; then prefer the fixed grid
   order (threshold ascending, floor ascending, fallback mode `3` before `2-if-4`).

If no candidate has a positive score in step 2, the current policy is retained. No
candidate is selected merely because it falls back less.

## Policy artifact

The chosen policy is saved as `models/ai-reliability-v2/policy-v2.json` with:

- policy version and candidate id;
- exact floor, threshold, mode and fallback rule;
- canonical SHA-256 of the policy fields;
- validation evidence path and summary;
- the selection rule and candidate count.

The file is hashed again in the Phase 5 freeze manifest.

## Phase 5 freeze and final test

Before any final-test seed is evaluated, `artifacts/ai-reliability/final-freeze/manifest.json`
records the freeze commit, model v1/v2 hashes, feature-schema hashes, policy hash, selection
rule, test-seed manifest hash and exact command lines. That manifest is committed. After the
freeze commit, no code, model, policy or config change is allowed until the single final-test
run finishes.

The final test uses the eight reserved seeds, route `held_out`, `dt=0.5`, 120 steps, the
original layout plus V3 ground/first/second, all base/mixed scenarios, and anchor counts
`3-8`. The geometric baseline, statistical LOCUS-X, LOCUS-X + v1 and LOCUS-X + frozen v2
policy are evaluated on identical generated observations. The test is run once. Any bug
found afterwards is documented and any rerun is labelled post-hoc.
