# 08 — Tonight's Emergency Study Plan & Self-Test Quiz

If you only have tonight to master LOCUS-X before facing the judges tomorrow at **CODE APEX 3.0**, follow this prioritized study schedule.

---

## 1. The 30-Minute Emergency Crash Course (If You Are Exhausted)

- **Minute 0–10: Memorize the Core Story (The "Elevator Pitch")**
  - Read [docs/presentation-prep/09-one-page-revision-sheet.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/09-one-page-revision-sheet.md).
  - Master the 3-sentence summary: What is the problem? What is the pipeline? What did we measure?
- **Minute 10–20: Master the 5 Taboo Traps & 5 Golden Answers**
  - Read Section 4 of [docs/presentation-prep/07-team-briefing.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/07-team-briefing.md).
  - *Never claim deep learning or real hospital hardware.* Always say: *"Statistical M-estimation in a procedural digital-twin laboratory."*
  - Understand the **Majority-Bias failure mode** (Finding 1 in [03-benchmark-results-and-limitations.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/03-benchmark-results-and-limitations.md)).
- **Minute 20–30: Rehearse the Live Demo Flow**
  - Walk through [docs/presentation-prep/04-live-demo-script.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/04-live-demo-script.md).
  - Know what each color means: White = Ground Truth, Amber = Baseline, Mint = LOCUS-X.

---

## 2. The 90-Minute Serious Preparation Plan (Recommended)

- **Block 1: System Pipeline & Data Flow (25 Minutes)**
  - Read [docs/presentation-prep/02-architecture-and-algorithms.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/02-architecture-and-algorithms.md).
  - Practice drawing the 5-stage pipeline on a scrap piece of paper from memory:
    `Input Ranges -> SVD Check -> Algebraic Init -> Huber M-Estimation -> Kalman Filter -> 95% Ellipse`.
  - Understand why the Kalman filter state is $[x, y, v_x, v_y]^T$ and why the timeout is 3.0 seconds.
- **Block 2: Benchmark Forensic Audit (25 Minutes)**
  - Read [docs/presentation-prep/03-benchmark-results-and-limitations.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/03-benchmark-results-and-limitations.md).
  - Understand the 4 key findings:
    1. Why LOCUS-X beats baseline in noise ($0.266\text{ m}$ vs $0.285\text{ m}$).
    2. Why baseline beats LOCUS-X in NLOS ($1.540\text{ m}$ vs $1.968\text{ m}$ due to majority bias).
    3. Why ellipse coverage drops to $49\%$ in NLOS (unmodeled structural bias).
    4. Why packet loss RMSE looks higher for LOCUS-X (survivorship bias: 83% availability vs 22%).
- **Block 3: Slide Review & Speaking Role (20 Minutes)**
  - Read [docs/presentation-prep/05-slide-by-slide-speaker-notes.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/05-slide-by-slide-speaker-notes.md).
  - Read aloud your assigned slide speaking script three times until it sounds natural.
- **Block 4: Mock Cross-Examination (20 Minutes)**
  - Go through the Self-Test Quiz in Section 5 below. Test yourself without looking at the answers!

---

## 3. The 15 Technical Facts You Absolutely Must Memorize

1. **Floor Dimensions:** The hospital layout is **30 × 20 metres** ($600\text{ m}^2$).
2. **Anchor Placement:** **8 fixed anchors** (A1 to A8) mounted along corridors and rooms.
3. **Sparse Infrastructure Limit:** Evaluated down to **3 anchors** (minimum required for 2D trilateration).
4. **Time Step:** Simulation advances at **$\Delta t = 0.2\text{ seconds}$** per frame ($5\text{ Hz}$).
5. **Collinearity Threshold:** Anchors rejected if SVD singular value ratio $s_1 / s_0 < 0.025$.
6. **Robust Loss:** **Huber loss** where scale parameter $k = 1.4826 \cdot \operatorname{MAD}$.
7. **Trust Weight Kernel:** Cauchy-like downweighting $q_i = \operatorname{clip}(1 / (1 + (e_i / 2s)^2), 0.08, 1.0)$.
8. **Temporal Trust Smoothing:** $w_t = 0.65 w_{t-1} + 0.35 q_t$.
9. **Kalman State Vector:** $\mathbf{x} = [x, y, v_x, v_y]^T$ (4 states: 2D position and 2D velocity).
10. **Process Noise:** Continuous acceleration model with $\sigma_a = 1.2\text{ m/s}^2$.
11. **Innovation Gate:** 2D Normalized Innovation Squared (NIS) threshold is **13.82** ($99.9\% \;\chi^2(2)$).
12. **Covariance Update:** **Joseph form** $P = (I-KH)P(I-KH)^T + KRK^T$ to prevent numerical asymmetry.
13. **Outage Fail-Safe:** **3.0 seconds maximum coasting** before position output is withheld (`status = "unavailable"`).
14. **Uncertainty Ellipse:** Semimajor/minor radii scaled by $\sqrt{5.99146}$ ($\chi^2(2)$ 95% quantile).
15. **Total Benchmark Volume:** **8,960 frames** evaluated across 7 scenarios, 4 anchor densities, and 2 held-out seeds.

---

## 4. The Top 10 Most Dangerous Judge Questions

1. *"Why did you use Huber loss instead of training a deep neural network?"*
2. *"Why does your robust estimator have worse RMSE than baseline under NLOS wall obstruction?"*
3. *"Why is your 95% uncertainty ellipse coverage only 49% in NLOS conditions?"*
4. *"Slide 4 lists PostgreSQL and WebSockets—where are they in the codebase?"*
5. *"Why is baseline RMSE in packet loss 0.25m while LOCUS-X is 1.11m?"*
6. *"Why does LOCUS-X have a 5 cm error in clean line-of-sight where baseline has 0.00 cm?"*
7. *"How do you prove that ground truth does not leak into the estimator?"*
8. *"What happens if all anchors are in a straight line?"*
9. *"Have you tested this on real physical UWB hardware?"*
10. *"How does the system know when an anchor measurement is corrupted if it doesn't have the building blueprint?"*

*(Detailed answers to all 10 are in [06-judge-question-bank.md](file:///c:/Users/satya/PROJECTS/APEX/docs/presentation-prep/06-judge-question-bank.md)).*

---

## 5. Self-Test Quiz (10 Questions)

*Answer these questions on paper before checking Section 6!*

1. **Q1:** In 2D space, what is the minimum number of distance measurements needed to compute a single unique coordinate?
2. **Q2:** Why does squaring range equations allow algebraic linear initialization?
3. **Q3:** What is the formula for the robust scale parameter $s$ using Median Absolute Deviation (MAD)?
4. **Q4:** What are the 4 state variables tracked in the Kalman filter?
5. **Q5:** What happens if the Normalized Innovation Squared (NIS) distance exceeds 13.82?
6. **Q6:** If an anchor drops packets for 4 continuous seconds, what does LOCUS-X display?
7. **Q7:** Why is the number $5.99146$ used to calculate the confidence ellipse semiaxes?
8. **Q8:** In the 4-anchor NLOS scenario, what weight was assigned to clean anchors vs biased anchors?
9. **Q9:** What communication protocol does the frontend use to advance simulation frames?
10. **Q10:** Where is session state stored in the FastAPI backend?

---

## 6. Self-Test Quiz Answers (Check After Attempting!)

- **A1:** **3 anchors**. (2 anchors yield two circle intersections; 1 anchor yields an infinite ring).
- **A2:** Squaring eliminates the nonlinear $\|\mathbf{p}\|^2 = x^2 + y^2$ term when subtracting the reference anchor equation: $(r_0^2 - r_i^2) = 2(\mathbf{a}_i - \mathbf{a}_0)^T \mathbf{p} + (\|\mathbf{a}_0\|^2 - \|\mathbf{a}_i\|^2)$.
- **A3:** $\text{scale} = \max(0.2, \; 1.4826 \cdot \operatorname{median}(|\mathbf{e} - \operatorname{median}(\mathbf{e})|))$.
- **A4:** $\mathbf{x} = [x, y, v_x, v_y]^T$ (X-position, Y-position, X-velocity, Y-velocity).
- **A5:** The measurement fix is classified as physically impossible and rejected. The filter updates status to `"degraded"` and coasts on state prediction without updating covariance with $K$.
- **A6:** After 3.0 seconds without an accepted fix, the tracker sets `status = "unavailable"`, withholding both position and the uncertainty ellipse (`position = None`, `ellipse = None`) to prevent false guidance.
- **A7:** $5.99146$ is the $95\%$ quantile of the Chi-square distribution with 2 degrees of freedom: $\chi^2_{0.95}(2) = -2 \ln(1 - 0.95) = -2 \ln(0.05) \approx 5.9914645$.
- **A8:** Clean anchors received a lower weight of **$0.426$**, while biased NLOS anchors received a higher weight of **$0.711$** due to majority bias.
- **A9:** **Serial HTTP REST requests** (`POST /api/v1/sessions/{id}/advance` with monotonic revision tokens), NOT WebSockets.
- **A10:** In an in-memory Python dictionary (`sessions: dict[str, SimulationSession] = {}`), NOT in PostgreSQL.
