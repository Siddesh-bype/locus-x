# 09 — One-Page Final Revision Sheet (Read 5 Minutes Before Presentation)

---

## 1. Verbal Pitches for Every Context

### A. The 20-Second Pitch (If Cut Off by Time)
> *"LOCUS-X is an indoor localization and failure analysis platform for hospitals. Where GPS fails, we combine adaptive M-estimation with a 4-state Kalman filter to track critical medical equipment using as few as 3 to 8 noisy beacons. We benchmarked 8,960 frames, proving our system maintains continuous tracking during sensor dropouts while transparently diagnosing obstructed signals."*

### B. The 60-Second Elevator Pitch
> *"In emergency departments, nurses spend up to 45 minutes per shift searching for crash carts and infusion pumps because GPS cannot penetrate concrete buildings. Existing indoor positioning systems require dozens of expensive beacons and break down when signals pass through walls. We built LOCUS-X to deliver sub-meter tracking using sparse infrastructure. Instead of blindly trusting corrupted ranges, our pipeline uses robust Huber M-estimation and dynamic residual consistency weighting to suppress outliers, paired with a constant-velocity Kalman filter that enforces physical momentum and bridges network dropouts. In an interactive 3D digital twin, we display live coordinates, a nominal 95% uncertainty ellipse, and per-anchor health diagnostics—giving clinicians dependable, explainable asset tracking."*

### C. The Non-Technical Judge Version
> *"Imagine trying to locate someone shouting in a dark, noisy hospital ward. If three people give you distances to that person, but one person's voice echoed through a doorway, simple averaging leads you to the wrong room. LOCUS-X listens to all signals, figures out which beacon is echoing through a wall, downweights the bad signal, and uses the cart's forward momentum to guide staff directly to the equipment."*

### D. The Senior Systems / ML Engineer Version
> *"LOCUS-X is a decoupled estimation and state-space tracking pipeline. We avoid black-box neural networks because they overfit to room-specific multipath. Instead, we use unsupervised statistical learning: algebraic linearization initializes a non-linear Huber M-estimator scaled adaptively by the Median Absolute Deviation (MAD). Residuals are mapped through a Cauchy kernel and exponentially smoothed to estimate latent anchor trust weights. The resulting fix feeds a 4-state constant-velocity Kalman filter with 99.9% Chi-square innovation gating and Joseph-form covariance updates. Tested over 8,960 frames, it reduces median error by 11% in noise and doubles operational availability under 40% packet loss."*

---

## 2. Whiteboard Drawing Guide (If Judges Ask You to Draw It)

```
 [1. Raw Ranges] r_i = ||p - a_i|| + bias + noise
        │
        ▼
 [2. SVD Check]  Verify s_1 / s_0 >= 0.025 (Reject collinearity)
        │
        ▼
 [3. Init Fix]   2(a_i - a_0)^T p = r_0^2 - r_i^2 + ||a_i||^2 - ||a_0||^2  (lstsq)
        │
        ▼
 [4. Robust Fit] scale = 1.4826 * MAD(residuals)
                 Huber Loss Optimization -> Cauchy Weights: q_i = 1 / (1 + (e_i / 2s)^2)
                 Exponential Trust: w_t = 0.65 w_{t-1} + 0.35 q_t
        │
        ▼
 [5. Kalman KF]  State: [x, y, vx, vy]^T  |  Acceleration Noise: σ_a = 1.2 m/s²
                 2D NIS Gate: (z - Hx)^T S^-1 (z - Hx) < 13.82 (99.9% χ²)
                 Joseph Covariance: P = (I-KH)P(I-KH)^T + KRK^T
                 3-Second Timeout: Withholds fix if outage >= 3.0 s
        │
        ▼
 [6. Output]     Position (x,y), Velocity, 95% Ellipse (√5.991*λ_i), Diagnostics
```

---

## 3. The 10 Essential Numbers to Quote

1. **30 × 20 metres** ($600\text{ m}^2$) — Floorplan dimensions.
2. **8 fixed anchors** — Full infrastructure baseline (benchmarked down to **3 and 4**).
3. **8,960 frames** — Total benchmarked frames across 7 scenarios and 2 held-out seeds.
4. **0.266 m vs 0.285 m** — LOCUS-X vs Baseline RMSE under moderate noise (7% better).
5. **0.198 m vs 0.222 m** — LOCUS-X vs Baseline Median error under moderate noise (11% better).
6. **95.3% vs 46.6%** — Tracking availability under 40% packet loss (4 anchors).
7. **3.0 seconds** — Maximum coasting time before position is safely withheld.
8. **13.82** — 99.9% 2D Chi-square innovation rejection gate.
9. **5.99146** — 95% Chi-square scale factor for the uncertainty ellipse.
10. **< 20 ms** — Total pipeline execution latency per frame.

---

## 4. The 4 Big Benchmark Limitations (Own Them Confidently!)

1. **Clean Line-of-Sight:** Baseline gets $<10^{-14}\text{ m}$; LOCUS-X gets **$0.050\text{ m}$ ($5\text{ cm}$)** because the Kalman filter exhibits minor kinematic lag on sharp $90^\circ$ turns.
2. **Broad NLOS (Wall Bias):** Baseline gets $1.54\text{ m}$; LOCUS-X degrades to **$1.97\text{ m}$**. *Why?* Majority bias: when $85\%$ of anchors are behind walls, they form a corrupt consensus and downweight the clean sensor.
3. **Uncertainty Ellipse Coverage:** In noise, coverage is **$98.8\%$**; in NLOS, coverage drops to **$49.4\%$** because the ellipse models random dispersion around the estimate, not unmodeled structural wall bias.
4. **Packet Loss RMSE:** Baseline RMSE appears lower ($0.25\text{ m}$ vs $1.11\text{ m}$), but that is survivorship bias: baseline only answered on **$22\%$** of frames, while LOCUS-X provided **$83\%$** continuous tracking.

---

## 5. Golden Rules for Answering Judges

1. **Never guess.** If you don't know, say: *"In our current prototype, that parameter is fixed at $\Delta t = 0.2\text{ s}$, but our architecture allows making it dynamic."*
2. **Never claim deep learning.** Say: *"We use explainable statistical learning and robust M-estimation."*
3. **Never claim physical hospital testing.** Say: *"We validated this in a procedural 3D digital-twin laboratory based on real hospital architectural principles."*
4. **Turn weaknesses into strengths.** When asked about NLOS degradation, say: *"That is the majority-bias failure mode, which our forensic audit proved occurs when $>50\%$ of anchors are obstructed. Documenting this failure mode is one of our key contributions."*
5. **Stand together.** Look at your teammates, hand off questions smoothly, and speak with calm engineering authority.
