# Assumptions and limitations

## V3 foundation update

The original single-floor reference remains available. V3 adds a fictional three-floor hospital at configured elevations, validated movement/door/core geometry, independent candidate sites and a selected-floor comparison interface. Localization itself is still **2D on a manually known floor**; floor switches reset tracking. There is no vertical estimate, cross-floor ranging or integrated transition state. Candidate sites do not constitute an optimized layout. Furniture and cutaway rendering are not propagation surfaces.

V3 functional results are versioned separately from the reference. The browser tool in the V3 implementation session had no available browser, so WebGL appearance, responsive screenshots and real UI click/keyboard acceptance remain unverified here. See [V3 status](v3-status.md) for executed checks and [handoff](v3-handoff.md) for pending work. Historical statements below about one evaluated floor describe the original reference benchmark.

## Multi-floor positioning update

The multi-floor work on branch `feat/multifloor-positioning` adds continuous 3D ranging, a vertical estimate, inferred and route-assisted storey state, and a two-mode evaluation protocol. See [multi-floor status](multifloor-status.md) for the executed evidence and the full "not implemented" list. Its limits are not small print:

- **The slab/NLOS model is synthetic.** Cross-floor attenuation is a configured simulator term chosen to make storeys separable; it is not a measured propagation model. No real-radio, calibration or deployment claim is made.
- **Coplanar anchors on one storey cannot name a floor.** If every anchor visible to the device lies on a single storey, the vertical geometry does not distinguish that storey from its neighbours. This is a geometric limitation, not a bug; multi-floor observability needs anchors on more than one storey or another vertical witness.
- **Floor probabilities are likelihood scores, not calibrated posteriors.** They are exported as finite numbers and used to decide a storey, but no calibration was performed and they must not be read as confidence guarantees.
- **Route-assisted floor identity is configuration, not inference.** In that mode the storey follows the selected route; its floor accuracy is reported as `n/a`, and its transition success measures freshness/settling by construction. It must not be compared with the inferred mode as a better result.

An inferred session is legitimately ambiguous at cold start and during vertical unobservability, and reports a null storey rather than guessing. Recovery under the mandated fault matrix is also degenerate: the timed outage leaves enough anchors that updates keep firing, so it demonstrates wiring rather than recovery from a total blackout.

## What this demonstrates

A complete local loop from synthetic observations through independently evaluated localization and an interactive 3-D display. It allows repeatable exploration of observability, inconsistent ranges, filter lag, uncertainty, outages and recovery.

## Physical model

- One fictional, single-floor hospital layout. It is not an architectural reconstruction or a measurement campaign.
- Euclidean 2-D ranges plus independent Gaussian noise and deterministic positive wall bias. No reflections, multipath path selection, diffraction, channel impulse response, antenna directionality, clock drift or synchronization error.
- Ranging is horizontal; wall/anchor rendering heights do not produce 3-D range corrections.
- Independent packet drops and a scripted periodic outage. Real radio loss may be correlated, bursty and affected by signal strength.
- Wall-material metadata is not yet used. Furniture is decorative. Walls are shown as cutaways to expose the floor.

## Statistical model

- Three anchors are necessary for general 2-D ranging but do not guarantee a good solution. SVD and local condition checks detect important degeneracies, not all ambiguity or gross inconsistency.
- A robust loss can suppress isolated inconsistent ranges, but broad positive bias can create a consistent wrong solution. Relative trust can then downweight the actually clean anchors. This failure occurs in the included benchmark and is not hidden.
- The constant-velocity filter lags at sudden turns/stops, can reject a real maneuver, and can coast inaccurately during outages. A three-second timeout is a prototype design choice, not a proven operational requirement.
- Covariance uses a local residual approximation and a Gaussian tracking model. The nominal 95% ellipse is badly undercalibrated in some biased scenarios. Displaying it is not proof of calibrated confidence.
- Metrics on available estimates can favor a method that withholds difficult frames. Always inspect valid rate and update rate. Prediction-only errors are included while the estimate remains available.

## Software scope

- In-memory state, no database, authentication or multi-tenant isolation. Bind to localhost. Do not expose the API publicly without appropriate deployment hardening.
- One Uvicorn worker is supported. A backend restart loses sessions and benchmark jobs. Browser plot history is limited to 600 samples and is not persisted across refreshes.
- Benchmark jobs are bounded and serialized but have no cancellation control. The UI polls for progress. Observed full-suite runtime was approximately 1–4 minutes on the tested computer; timing depends on concurrent load and the numerical-library build.
- The scene requires WebGL. A rendering-error message preserves access to telemetry/evaluation if 3-D fails. No static hospital image substitutes for simulation geometry.
- SVG curves interpolate the actual recorded samples. A sample contains no claim about unsampled times. Displayed CDFs are sampled quantile curves, not raw continuous radio distributions.
- Scenario JSON export saves initial settings for API replay; it does not replay the entire historical sequence of manual controls. Event-history replay and imported range logs remain extensions.
- Linux/macOS startup commands are documented, but only Windows execution was tested here.
- No learned trust classifier, real UWB/BLE adapter, hardware integration, live hospital deployment or edge device evaluation is included.

## Next validation priorities

1. Evaluate majority-positive-bias mitigation using inference-time features only; use new held-out environments before selecting an improved model.
2. Calibrate and stress-test covariance coverage, particularly on turns, bias transitions and prolonged missing data.
3. Add timestamped real-radio observations and benchmark against independently collected reference positions.
4. Extend floor-plan fixtures and geometry checks, then add reproducible control-event replay and persistent experiment results.
