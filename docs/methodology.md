# Measurement and estimation methodology

## Synthetic observations

For each anchor and frame:

`r = max(0.01, ||target - anchor|| + b * min(wall_intersections,3)/2 + Normal(0,sigma))`

`b` is the NLOS-bias control. Thus one wall adds half the setting, two walls add the setting, and three or more add 1.5 times the setting. Turning wall obstruction off sets propagation bias to zero without removing decorative room geometry. Manual controls are effective even after a preset is chosen.

Random streams use `SeedSequence([seed, frame_index, anchor_index])`. Results are independent of HTTP timing. Disabling one anchor leaves all surviving observations identical. Packet loss is a Bernoulli draw; disabled anchors, timed outages, then packet loss determine availability in that order. The timed-outage scenarios suppress A2/A4/A6/A8 during `[12,22)` seconds of each 40-second cycle. Simulator labels distinguish availability, LOS/NLOS and each injected loss source.

Routes are piecewise-linear waypoint loops with explicit dwell times and constant travel speed. Abrupt turns/stops intentionally challenge the smooth motion model. Frame timestamps are simulation seconds; frame index determines the random draw. Reset restores session-creation settings and repeats the initial observations; subsequent observations repeat for the same step schedule.

## Conventional baseline

1. Reject duplicate IDs, nonpositive or nonfinite ranges and nonfinite anchor coordinates/timestamps.
2. Require three valid, distinct anchors and a 2-D anchor-spread singular-value ratio ≥0.025.
3. Initialize position by algebraically differencing squared observed ranges.
4. Minimize `sum((||p-a_i||-r_i)^2)` with SciPy nonlinear least squares, at most 100 function evaluations.
5. Reject unsuccessful solves and local directional information matrices with condition number >1000.

The result exposes position or null, status/reason, raw residuals, geometry condition, covariance proxy and measured solver latency. It does not clamp the answer to floor boundaries: a biased result may appear outside the floor and remains part of evaluation.

## Robust estimate and reliability

The robust path computes its own conventional initialization using identical ranges. It estimates residual scale as `max(0.2, 1.4826 * median(abs(residual - median(residual))))`, then solves with Huber loss. For the candidate residual magnitudes, instantaneous consistency is:

`q_i = clip(1 / (1 + (abs(residual_i)/(2*scale))^2), 0.08, 1)`

Each available anchor's weight is `0.65*previous_weight + 0.35*q_i`, initialized from its current score. A second Huber solve uses `sqrt(weight_i)*residual_i`. Missing anchors do not update reliability. These are heuristic **relative consistency weights**, neither probabilities of LOS nor a trained model. Residual inconsistency could reflect noise, geometry, bias, or estimator failure.

Covariance is approximated as `s² * inverse(Jᵀ W J)`, with `s² = max(0.04, sum(w_i residual_i²)/max(1,n-2))`. This is a local approximation, not a rigorous robust sandwich estimator. Shared positive bias can shift the whole solution while leaving small residuals, producing overconfidence. With only three anchors there is little redundancy for identifying a bad measurement. Both limitations are exposed in experiments.

## Motion tracking

The state is `[x,y,vx,vy]`. A constant-velocity linear Kalman filter consumes the robust position fix. Transition entries `F[0,2]=F[1,3]=dt`; all diagonal entries are one. Process noise is `Q=1.2²*G*Gᵀ`, with independent acceleration columns `[dt²/2,0,dt,0]` and `[0,dt²/2,0,dt]`. The position measurement covariance is the robust covariance plus `0.04*I`. Initial velocity is zero with variance 4 per axis.

The filter uses a 2-D normalized innovation squared gate of 13.82 (approximately a 99.9% chi-square threshold) and the Joseph covariance update. Rejected or missing fixes trigger prediction only. The output immediately becomes `degraded`; covariance grows under the motion model. At ≥3 seconds since the last accepted update, public position/ellipse become null and status becomes `unavailable`. Internal state covariance continues to evolve. A subsequent defensible range fix reinitializes position and velocity uncertainty.

No floor plan, route, true position, wall fault label, or future observation enters this filter. It is a linear position-measurement Kalman filter, not an EKF and not a navigation solution constrained to corridors.

## Ellipse and empirical coverage

For the 2×2 position covariance, eigenvectors determine orientation; semiaxes are `sqrt(5.9914645471 * eigenvalue)`. This is the nominal 95% 2-D Gaussian region, not 1.96 times each axis. The backend returns semimajor/semiminor lengths in metres and angle in radians. The display transforms the corresponding points through the canonical coordinate map.

Coverage is evaluated independently by `errorᵀ covariance⁻¹ error <= 5.9914645471`, only when position/covariance are available. The label is deliberately “95% region,” with qualification that confidence is uncalibrated. The completed NLOS experiments demonstrate severe undercoverage.

## Benchmark definitions

The saved suite uses the `held_out` service route and seeds 101/202, separate from the default interactive asset route and seed 12. It evaluates 160 frames at 0.5 seconds per trajectory. Initialization at t=0 precedes evaluation and is not counted. The floor is shared, so this is a held-out path/seed evaluation, not a held-out-building claim.

Anchor subsets are deterministic: 3={A1,A3,A6}, 4={A1,A3,A5,A7}, 6={A1,A2,A3,A5,A6,A7}, 8=all. These avoid collinear nominal layouts, but packet loss/outages can still remove observability. Baseline and proposed receive identical available observations at every time step.

- RMSE: square root of the mean squared Euclidean position errors over valid outputs.
- Median/P95: pooled valid error quantiles over both seeds.
- Valid rate: frames with a reported position divided by all evaluated frames.
- Update rate: accepted range/filter updates divided by all frames. Prediction-only frames may be valid without being updates.
- NLOS RMSE: valid errors on frames with at least one available NLOS-labelled observation.
- Dropout RMSE: valid errors on frames with injected packet loss or timed outage. Intentionally excluded anchors for an anchor-count experiment do not mark every frame as dropout.
- Latency: mean measured solver runtime. Proposed includes its initial solve, reweighted solves, and filter, excluding network/rendering overhead.
- Coverage: fraction of available tracking positions inside the nominal ellipse; always interpret with valid rate.

The time trace uses the first seed; summary rows pool all seeds. The distribution plot uses up to 101 sampled quantiles of all valid errors. Null estimates remain gaps, never zero error. Runtime and IDs are excluded from reproducibility tests. Configurations and environment SHA256 are saved with results. No target accuracy or improvement is hardcoded.
