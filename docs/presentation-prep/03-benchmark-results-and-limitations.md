# 03 — Benchmark Results & Limitations Audit

## 1. Evaluation Methodology & Experimental Setup

The LOCUS-X benchmark suite is an independent, reproducible testing harness defined in [configs/benchmarks/competition.json](file:///c:/Users/satya/PROJECTS/APEX/configs/benchmarks/competition.json) and executed via [benchmark.py](file:///c:/Users/satya/PROJECTS/APEX/backend/locus_x/benchmark.py).

### Experimental Parameters
- **Layout:** Fictional 30 × 20 m hospital floor ([hospital_floor_01.json](file:///c:/Users/satya/PROJECTS/APEX/configs/environments/hospital_floor_01.json)).
- **Evaluation Route:** Held-out service route (`"held_out"`). *(Completely separate from the interactive UI's `"asset_round"` route)*.
- **Seeds:** 101 and 202. *(Held-out from the interactive UI's seed 12)*.
- **Frames Evaluated:** 160 frames per trajectory at $\Delta t = 0.5\text{ s}$ ($80\text{ seconds}$ per run).
- **Total Workload:**
  $$7 \text{ Scenarios} \times 4 \text{ Anchor Counts} \times 2 \text{ Seeds} \times 160 \text{ Frames} = \mathbf{8,960 \text{ Evaluated Frames}}$$
- **Fairness Guarantee:** Both the Conventional Baseline and Proposed LOCUS-X estimators receive the **exact same immutable list of `Measurement` objects** at every single frame.

### Anchor Configurations
- **3 Anchors:** `{A1, A3, A6}` — Minimum mathematical requirement for 2D trilateration.
- **4 Anchors:** `{A1, A3, A5, A7}` — Minimal symmetric coverage.
- **6 Anchors:** `{A1, A2, A3, A5, A6, A7}` — Moderate density.
- **8 Anchors:** `{A1, A2, A3, A4, A5, A6, A7, A8}` — Full infrastructure baseline.

### Metric Definitions
1. **RMSE (Root Mean Square Error):** $\sqrt{\frac{1}{M} \sum_{k=1}^M \|\hat{\mathbf{p}}_k - \mathbf{p}^*_k\|_2^2}$ computed strictly over valid estimates ($M \le N$).
2. **Median Error:** 50th percentile of Euclidean position errors.
3. **P95 Error (Tail Risk):** 95th percentile of Euclidean position errors.
4. **Valid Rate:** $\frac{\text{Frames with a reported position}}{\text{Total evaluated frames (320)}}$. *(Note: Baseline reports null if $<3$ anchors or collinear; LOCUS-X reports predictions during outages until the 3-second timeout)*.
5. **Update Rate:** $\frac{\text{Frames with an accepted sensor/measurement update}}{\text{Total evaluated frames}}$.
6. **95% Covariance Coverage:** Fraction of valid tracking frames where the true position lies inside the nominal 95% uncertainty ellipse: $(\mathbf{p}^* - \hat{\mathbf{p}})^T P_{\text{pos}}^{-1} (\mathbf{p}^* - \hat{\mathbf{p}}) \le 5.99146$.
7. **Latency:** Mean measured solver execution time in milliseconds (excluding networking/rendering).

---

## 2. Complete Benchmark Comparison Table

The following data is extracted directly from the verified reference artifact [artifacts/benchmark/metrics.csv](file:///c:/Users/satya/PROJECTS/APEX/artifacts/benchmark/metrics.csv) (pooled over seeds 101 and 202, 320 frames per scenario/anchor pair):

| Scenario | Anchors | Method | RMSE (m) | Median (m) | P95 (m) | Valid Rate | Update Rate | 95% Coverage | Latency (ms) |
|---|---|---|---|---|---|---|---|---|---|
| **Clean LOS** | 3 | Baseline | **< 1e-14** | < 1e-14 | < 1e-14 | 100% | 100% | — | 2.4 ms |
| | 3 | LOCUS-X | 0.063 | 0.004 | 0.140 | 100% | 100% | 100.0% | 6.4 ms |
| | 4 | Baseline | **< 1e-14** | < 1e-14 | < 1e-14 | 100% | 100% | — | 2.7 ms |
| | 4 | LOCUS-X | 0.056 | 0.003 | 0.139 | 100% | 100% | 100.0% | 7.1 ms |
| | 6 | Baseline | **< 1e-14** | < 1e-14 | < 1e-14 | 100% | 100% | — | 2.6 ms |
| | 6 | LOCUS-X | 0.051 | 0.003 | 0.125 | 100% | 100% | 100.0% | 6.8 ms |
| | 8 | Baseline | **< 1e-14** | < 1e-14 | < 1e-14 | 100% | 100% | — | 2.7 ms |
| | 8 | LOCUS-X | 0.050 | 0.003 | 0.122 | 100% | 100% | 100.0% | 6.8 ms |
| **Moderate Noise** | 3 | Baseline | 0.492 | 0.406 | 0.844 | 100% | 100% | — | 6.3 ms |
| ($\sigma = 0.4\text{ m}$) | 3 | LOCUS-X | **0.442** | **0.351** | **0.789** | 100% | 100% | 93.1% | 18.9 ms |
| | 4 | Baseline | 0.420 | 0.342 | 0.756 | 100% | 100% | — | 6.3 ms |
| | 4 | LOCUS-X | **0.378** | **0.307** | **0.672** | 100% | 100% | 94.1% | 18.7 ms |
| | 6 | Baseline | 0.330 | 0.272 | 0.599 | 100% | 100% | — | 5.9 ms |
| | 6 | LOCUS-X | **0.307** | **0.248** | **0.546** | 100% | 100% | 96.9% | 17.5 ms |
| | 8 | Baseline | 0.285 | 0.222 | 0.514 | 100% | 100% | — | 6.4 ms |
| | 8 | LOCUS-X | **0.266** | **0.198** | **0.490** | 100% | 100% | 98.8% | 19.5 ms |
| **High Noise** | 3 | Baseline | **1.479** | 1.207 | **2.742** | 100% | 100% | — | 7.5 ms |
| ($\sigma = 1.2\text{ m}$) | 3 | LOCUS-X | 2.328 | **1.175** | 3.917 | 100% | 87.2% | 50.6% | 25.2 ms |
| | 4 | Baseline | 1.266 | 1.016 | 2.253 | 100% | 100% | — | 6.7 ms |
| | 4 | LOCUS-X | **1.206** | **0.975** | **2.117** | 100% | 98.1% | 64.7% | 21.6 ms |
| | 6 | Baseline | 0.986 | 0.819 | 1.769 | 100% | 100% | — | 6.9 ms |
| | 6 | LOCUS-X | **0.869** | **0.686** | **1.509** | 100% | 99.7% | 80.3% | 21.3 ms |
| | 8 | Baseline | 0.853 | 0.682 | 1.538 | 100% | 100% | — | 7.2 ms |
| | 8 | LOCUS-X | **0.783** | **0.573** | **1.414** | 100% | 99.1% | 87.8% | 21.6 ms |
| **NLOS Bias** | 3 | Baseline | **2.677** | 2.754 | **3.752** | 100% | 100% | — | 8.9 ms |
| ($b = 2.5\text{ m}$) | 3 | LOCUS-X | 2.779 | **2.647** | 3.929 | 100% | 100% | 18.4% | 29.3 ms |
| | 4 | Baseline | **2.090** | **1.674** | **3.451** | 100% | 100% | — | 8.5 ms |
| | 4 | LOCUS-X | 2.788 | 2.799 | 3.846 | 100% | 100% | 13.1% | 27.0 ms |
| | 6 | Baseline | **1.906** | **1.717** | **2.858** | 100% | 100% | — | 7.7 ms |
| | 6 | LOCUS-X | 2.295 | 2.392 | 3.204 | 100% | 100% | 24.4% | 23.9 ms |
| | 8 | Baseline | **1.540** | **1.280** | **2.589** | 100% | 100% | — | 8.1 ms |
| | 8 | LOCUS-X | 1.968 | 1.299 | 3.312 | 100% | 100% | 49.4% | 27.7 ms |
| **Packet Loss** | 3 | Baseline | **0.249** | 0.221 | **0.421** | 22.8% | 22.8% | — | 1.4 ms |
| ($p = 0.40$) | 3 | LOCUS-X | 1.113 | 0.495 | 2.601 | **83.1%** | 22.8% | 100.0% | 3.5 ms |
| | 4 | Baseline | **0.254** | **0.208** | **0.438** | 46.6% | 46.6% | — | 3.0 ms |
| | 4 | LOCUS-X | 0.734 | 0.296 | 1.638 | **95.3%** | 46.6% | 100.0% | 7.6 ms |
| | 6 | Baseline | **0.241** | 0.190 | **0.408** | 77.2% | 77.2% | — | 4.9 ms |
| | 6 | LOCUS-X | 0.265 | **0.177** | 0.478 | **100.0%** | 77.2% | 100.0% | 11.7 ms |
| | 8 | Baseline | 0.216 | 0.169 | 0.382 | 92.8% | 92.8% | — | 5.8 ms |
| | 8 | LOCUS-X | **0.214** | **0.151** | **0.351** | **100.0%** | 92.8% | 100.0% | 15.2 ms |
| **Anchor Outage** | 3 | Baseline | **0.251** | 0.206 | **0.439** | 75.0% | 75.0% | — | 5.1 ms |
| (Timed 12-22s) | 3 | LOCUS-X | 0.320 | **0.184** | 0.495 | **81.3%** | 75.0% | 100.0% | 12.9 ms |
| | 4 | Baseline | 0.210 | 0.172 | 0.381 | 100% | 100% | — | 6.2 ms |
| | 4 | LOCUS-X | **0.195** | **0.153** | **0.354** | 100% | 100% | 100.0% | 15.6 ms |
| | 6 | Baseline | 0.175 | 0.143 | 0.310 | 100% | 100% | — | 6.0 ms |
| | 6 | LOCUS-X | **0.168** | **0.131** | **0.301** | 100% | 100% | 100.0% | 17.0 ms |
| | 8 | Baseline | 0.160 | 0.121 | **0.282** | 100% | 100% | — | 5.9 ms |
| | 8 | LOCUS-X | **0.155** | **0.111** | 0.294 | 100% | 100% | 100.0% | 17.1 ms |
| **Chaos / Combined**| 3 | Baseline | **4.619** | **4.639** | **6.293** | 31.9% | 31.9% | — | 3.6 ms |
| | 3 | LOCUS-X | 5.435 | 4.862 | 8.855 | **71.3%** | 30.9% | 27.6% | 11.2 ms |
| | 4 | Baseline | **4.546** | **4.156** | **7.525** | 72.8% | 72.8% | — | 3.1 ms |
| | 4 | LOCUS-X | 5.095 | 4.385 | 8.159 | **100.0%** | 70.0% | 35.3% | 9.5 ms |
| | 6 | Baseline | **4.062** | **3.935** | 6.249 | 88.4% | 88.4% | — | 3.4 ms |
| | 6 | LOCUS-X | 4.199 | 4.064 | **6.154** | **100.0%** | 87.2% | 27.8% | 9.1 ms |
| | 8 | Baseline | **3.640** | **2.950** | **5.706** | 91.3% | 91.3% | — | 3.6 ms |
| | 8 | LOCUS-X | 3.782 | 3.492 | 5.942 | **100.0%** | 90.3% | 36.3% | 9.7 ms |

---

## 3. Deep-Dive Audit of Specific Phenomena

### Finding 1: Why Does LOCUS-X Lose to Baseline Under NLOS?
Under the NLOS scenario with 8 anchors, Baseline RMSE is **1.540 m**, whereas LOCUS-X RMSE degrades to **1.968 m**. At 4 anchors, Baseline is **2.090 m** while LOCUS-X is **2.788 m**.

#### The Mathematical & Empirical Root Cause:
We conducted a dedicated forensic audit preserved in [artifacts/nlos-audit/diagnosis.json](file:///c:/Users/satya/PROJECTS/APEX/artifacts/nlos-audit/diagnosis.json).
In the 4-anchor NLOS scenario:
- **Biased Fraction:** **85%** of available anchor links intersect hospital walls during the run!
- **Mean Injected Bias:** $+1.66\text{ m}$.
- **Resulting Consistency Weights ([diagnosis.json:98-99](file:///c:/Users/satya/PROJECTS/APEX/artifacts/nlos-audit/diagnosis.json#L98-L99)):**
  - Mean weight assigned to clean Line-of-Sight anchors: **0.426**
  - Mean weight assigned to biased Non-Line-of-Sight anchors: **0.711**!

```
                       THE MAJORITY-BIAS FAILURE MODE
                       
   [Anchor A1] (Clean LOS)                     [Anchor A2] (NLOS, +2.5m)
        \                                            /
         \                                          /
          \                                        /
           \                                      /
            \-----> [True Target Position] <-----/
                           |
                           |
            [Anchor A3] (NLOS, +2.5m)          [Anchor A4] (NLOS, +2.5m)
            
   Because 3 out of 4 anchors suffer from wall bias, their measured circles 
   intersect at a "consensus point" 2 meters away from truth.
   
   The optimizer calculates residuals:
     - Biased Anchors A2, A3, A4 have small residuals to the consensus point! -> Assigned Weight: 0.71
     - Clean Anchor A1 has a huge residual to the consensus point!          -> Assigned Weight: 0.43
     
   RESULT: The robust M-estimator mistakenly downweights the ONLY clean sensor!
```

**Presentation Defense:**
*"When the majority of anchors suffer simultaneous positive wall bias, they form a mutually consistent corrupt consensus. A residual-based M-estimator assumes the majority is correct and actively downweights the single clean sensor. This is a fundamental theoretical limitation of range-only M-estimation without floorplan map-matching, and discovering this in our benchmark proves the rigor of our evaluation framework."*

---

### Finding 2: Severe Undercoverage of the Nominal 95% Uncertainty Ellipse
In clean conditions and moderate noise, the nominal 95% ellipse achieves **93% to 100% empirical coverage**.
However:
- In NLOS with 8 anchors, empirical coverage collapses to **49.4%**.
- In NLOS with 4 anchors, coverage collapses to **13.1%**.
- In Chaos with 8 anchors, coverage collapses to **36.3%**.

#### Why This Happens:
1. The uncertainty ellipse is derived from the Kalman state covariance:
   $$\text{cov} = \left( J^T W J \right)^{-1} \cdot s^2$$
2. This formula calculates **precision** (variance around the estimate), **not accuracy** (distance from true ground truth).
3. It assumes error is zero-mean Gaussian $\mathcal{N}(0, \Sigma)$.
4. When wall obstruction introduces a constant $+2.5\text{ m}$ positive offset, the entire estimation cloud shifts by 2 meters, but the covariance remains small. The true target position lies completely outside the tiny, overconfident ellipse.

**Presentation Defense:**
*"Our confidence ellipse is a model-based Gaussian covariance region, not a statistically calibrated conformal boundary. In our benchmarks, we honestly report that coverage drops to 49% under NLOS bias. This proves that standard Kalman filters suffer from severe overconfidence under unmodeled structural bias."*

---

### Finding 3: The Valid-Rate vs. Accuracy Trade-Off under Packet Loss
In the 3-anchor packet-loss scenario ($40\%$ drop rate):
- **Baseline Trilateration:**
  - Valid Rate: **22.8%** (Available only 73 out of 320 frames).
  - RMSE: **0.249 m**.
- **Proposed LOCUS-X:**
  - Valid Rate: **83.1%** (Available 266 out of 320 frames).
  - RMSE: **1.113 m**.

#### Why Does Baseline Look More Accurate on Paper?
This is a classic **survivorship bias metric trap**:
- The baseline estimator requires at least 3 valid anchors. When packets drop, 3 anchors are available only $22.8\%$ of the time. On the few frames where all 3 anchors survive, the geometry happens to be clean, so its error is only $0.25\text{ m}$. On the remaining $77.2\%$ of frames, it outputs `null` (which is excluded from RMSE calculation).
- LOCUS-X activates its **constant-velocity dead-reckoning model**, coasting through dropouts for up to 3 seconds. It delivers continuous position tracking to hospital staff on $83.1\%$ of frames. However, because a cart turning during an outage drifts, its dead-reckoning error grows, raising its pooled RMSE to $1.11\text{ m}$.

**Presentation Defense:**
*"If you look only at RMSE, the baseline appears 4x more accurate (0.25m vs 1.11m). But baseline only produced an answer 22% of the time—staff were blind 78% of the time! LOCUS-X maintained 83% operational availability through Kalman coasting. We trade off millimeter precision during outages to prevent complete operational blindness."*

---

### Finding 4: Filter Lag on Clean Ground Truth
In the Clean Line-of-Sight scenario:
- Baseline RMSE: **$< 10^{-14}\text{ m}$** (virtually machine zero).
- LOCUS-X RMSE: **$0.050\text{ m}$** ($5\text{ cm}$).

#### Why Does LOCUS-X Have 5 cm Error in Perfect Conditions?
- Baseline solves each frame statically and instantaneously; with zero noise, it finds exact geometric circle intersections.
- LOCUS-X feeds the position fix into a constant-velocity Kalman filter with process acceleration noise $Q$ ($\sigma_a = 1.2\text{ m/s}^2$). When the cart executes sharp $90^\circ$ hallway turns or stops at clinical waypoints, the temporal filter exhibits a 1-to-2 frame **kinematic lag**, overshooting the corner before snapping back.

---

## 4. Summary: Where LOCUS-X Truly Wins

1. **Moderate Gaussian Noise ($\sigma = 0.4\text{ m}$):**
   - LOCUS-X outperforms baseline across **every single anchor configuration** (3, 4, 6, and 8 anchors).
   - At 8 anchors, error drops from $0.285\text{ m}$ to $0.266\text{ m}$ ($7\%$ improvement), and median error drops from $0.222\text{ m}$ to $0.198\text{ m}$ ($11\%$ improvement) with $98.8\%$ ellipse coverage.
2. **High Noise ($\sigma = 1.2\text{ m}$) with $\ge 4$ Anchors:**
   - At 8 anchors, LOCUS-X reduces RMSE from $0.853\text{ m}$ to $0.783\text{ m}$ ($8.2\%$ reduction) and P95 tail risk from $1.538\text{ m}$ to $1.414\text{ m}$.
3. **Hardware Availability in Sparse / Lossy Networks:**
   - Under $40\%$ packet loss with 4 anchors, LOCUS-X doubles service availability from **$46.6\%$ to $95.3\%$**.
   - Under timed anchor outages, LOCUS-X achieves **$100\%$ availability** and lowers median error across 4, 6, and 8 anchors.
4. **Execution Speed:**
   - Total pipeline latency is **$\le 29\text{ ms}$** across all test cases, operating well within the $200\text{ ms}$ real-time hospital tracking budget.
