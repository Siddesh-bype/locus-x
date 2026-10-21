# 07 — Team Briefing & Handoff Guide

## 1. Shared Foundation: The Problem & Solution in 3 Sentences

> **The Problem:** In critical indoor environments like hospitals, GPS signals disappear, and sparse radio beacons suffer from severe measurement noise, wall delays (NLOS), and packet drops, blinding clinical staff during emergencies.
> 
> **Our Solution:** LOCUS-X combines adaptive M-estimation (Huber loss + dynamic residual consistency weighting) with a 4-state constant-velocity Kalman filter to deliver smooth, sub-meter positioning and real-time uncertainty diagnostics using only 3 to 8 anchors.
> 
> **Our Evidence:** We benchmarked 8,960 frames across 7 stress scenarios in a 3D digital-twin laboratory, proving that LOCUS-X outperforms conventional least-squares in noisy and lossy networks while maintaining complete architectural transparency.

---

## 2. Recommended Speaker Division for the 4 Teammates

```
+-----------------------------------+--------------------+---------------------------------------------------------------+
| Team Member & Role                | Assigned Slides    | Primary Presentation & Q&A Responsibility                     |
+-----------------------------------+--------------------+---------------------------------------------------------------+
| **Satyam Shrivastav**             | **Slides 1 & 2**   | **Opening & Clinical Pain Point:** Team intro, problem        |
| *Team Leader (Computer Engg)*     | + **Live Demo**    | statement PS2, why GPS fails, hospital emergency asset pain,  |
|                                   |                    | orchestrating the live 3D prototype demo.                     |
+-----------------------------------+--------------------+---------------------------------------------------------------+
| **Siddesh Shirote**               | **Slides 3 & 4**   | **Architecture & Software Engineering:** The 5-stage pipeline,|
| *Member (Computer Engg)*          |                    | truth boundary, FastAPI backend, Three.js frontend, serial    |
|                                   |                    | REST advance protocol, and session concurrency.               |
+-----------------------------------+--------------------+---------------------------------------------------------------+
| **Chiranjilal Kumawat**           | **Slide 5**        | **Mathematics & Localization Algorithms:** Baseline NLS vs    |
| *Member (Computer Engg)*          | + **Telemetry**    | robust M-estimation, MAD scale tuning, Cauchy weights, Kalman |
|                                   |                    | tracking, Joseph covariance, and the 95% ellipse.             |
+-----------------------------------+--------------------+---------------------------------------------------------------+
| **Priyansh Gaur**                 | **Slides 6, 7, 8** | **Benchmarks, Limitations & Vision:** 8,960-frame benchmark   |
| *Member (AI & Machine Learning)*  | + **Q&A Closer**   | results, the NLOS majority-bias forensic audit, why we chose  |
|                                   |                    | statistical learning over deep learning, roadmap & Q&A.        |
+-----------------------------------+--------------------+---------------------------------------------------------------+
```

---

## 3. Verbal Handoff Cues Between Speakers

- **Satyam $\rightarrow$ Siddesh (after Slide 2):**
  > *"To show you how we solve this sparse, noisy tracking problem from an architectural perspective, I'll hand over to Siddesh to walk through our core pipeline."*
- **Siddesh $\rightarrow$ Chiranjilal (after Slide 4):**
  > *"Now, Chiranjilal will explain the mathematical algorithms that make our localization core robust to noise and packet loss."*
- **Chiranjilal $\rightarrow$ Satyam (before Live Demo / Slide 5):**
  > *"To see these mathematical principles working in real time, Satyam will now demonstrate our interactive 3D prototype."*
- **Satyam $\rightarrow$ Priyansh (after Live Demo / Slide 5):**
  > *"To explain our empirical evaluation results and the limitations we discovered, Priyansh will walk you through our benchmark audit."*
- **Priyansh $\rightarrow$ Judges (after Slide 8):**
  > *"Thank you for your time. The entire team is now ready to take your questions."*

---

## 4. Taboo Claims: What Nobody on the Team Should Ever Say

Judges at CODE APEX 3.0 include senior faculty and industry software engineers who will instantly catch exaggerations. **Memorize this list of forbidden claims:**

```
+------------------------------------------------------+--------------------------------------------------------------+
| NEVER SAY THIS (TABOO)                               | INSTEAD, SAY THIS (TRUTHFUL & DEFENSIBLE)                    |
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "We trained a deep neural network / CNN / LSTM."  | ✅ "We used an explainable statistical learning M-estimator  |
|                                                      |     with adaptive residual consistency weighting."           |
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "Our trust model is a machine learning classifier."| ✅ "Our trust model is an online recursive parameter         |
|                                                      |     estimator that computes Cauchy consistency weights."     |
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "We tested this with real hardware in a hospital."| ✅ "We evaluated this in a procedural 3D digital-twin        |
|                                                      |     laboratory based on real hospital architectural data."   |
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "Our system achieves 95% accuracy everywhere."    | ✅ "In moderate noise, our 95% ellipse achieves 98.8%        |
|                                                      |     coverage; under severe NLOS bias, coverage drops to 49%."|
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "We store all our telemetry in PostgreSQL."       | ✅ "Session state is held in-memory for sub-millisecond      |
|                                                      |     speed; PostgreSQL is our target multi-tenant schema."   |
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "We use real-time WebSockets."                    | ✅ "We use a serial HTTP REST advance protocol to decouple   |
|                                                      |     simulation physics from browser rendering framerates."   |
+------------------------------------------------------+--------------------------------------------------------------+
| ❌ "LOCUS-X beats baseline in all scenarios."        | ✅ "LOCUS-X wins in noise and dropouts; under broad NLOS,    |
|                                                      |     majority-bias degrades robust performance."              |
+------------------------------------------------------+--------------------------------------------------------------+
```

---

## 5. Rapid-Fire Glossary for the Entire Team

- **Anchor:** A fixed beacon with known physical coordinates $(x, y)$.
- **Target (Tag):** The mobile asset being tracked (the hospital crash cart).
- **Range:** The measured scalar distance from an anchor to the target.
- **NLOS (Non-Line-of-Sight):** When a wall blocks direct radio travel, injecting positive delay bias.
- **Trilateration:** Determining a 2D point from 3 or more distance circles.
- **M-Estimator:** A robust generalized maximum-likelihood estimator that bounds the influence of outliers.
- **Huber Loss:** A loss function that is quadratic ($L_2$) for small residuals and linear ($L_1$) for large residuals.
- **MAD (Median Absolute Deviation):** A robust measure of statistical dispersion; $\text{scale} = 1.4826 \cdot \text{MAD}$.
- **Cauchy Weight Kernel:** $q_i = 1 / (1 + (e_i / 2s)^2)$, downweighting inconsistent anchors toward $0.08$.
- **Kalman Filter (KF):** A recursive state estimator that fuses physical motion prediction with noisy measurements.
- **State Vector:** $\mathbf{x} = [x, y, v_x, v_y]^T$ (2D position and velocity).
- **Innovation:** The difference between measured position and predicted position ($\mathbf{y} = \mathbf{z} - H \hat{\mathbf{x}}$).
- **NIS (Normalized Innovation Squared):** The Mahalanobis distance of the innovation; gated at $13.82$ ($99.9\%$).
- **Joseph Form:** A numerically stabilized covariance update: $P = (I-KH)P(I-KH)^T + KRK^T$.
- **Coasting:** Dead-reckoning position using velocity when measurements drop out.
- **3-Second Timeout:** The fail-safe limit that suppresses position output if no update occurs for 3 seconds.
- **Confidence Ellipse:** The 2D spatial representation of position covariance, scaled by $\sqrt{5.991}$ for $95\%$ confidence.
- **Coverage:** The empirical fraction of times true position actually falls inside the $95\%$ ellipse.
- **Condition Number ($\kappa$):** The ratio of maximum to minimum eigenvalues of the information matrix; $>1000$ indicates poor geometry.
- **Majority-Bias Failure:** When $>50\%$ of anchors are obstructed, causing the robust solver to downweight the clean sensor.

---

## 6. Top 20 High-Probability Judge Questions & Uniform Team Answers

1. **Why not GPS?** Roofs and concrete slabs attenuate signals by $>30\text{ dB}$, causing total signal loss.
2. **Why not Wi-Fi RSSI?** Signal strength fluctuates by $\pm 10\text{ dB}$ when people walk by, causing 5–10m errors.
3. **What is sparse infrastructure?** Tracking with only 3 to 8 anchors across a 600 m² floor instead of 16+.
4. **Where is AI in this project?** In statistical machine learning: adaptive M-estimation, iterative MAD scale learning, and recursive trust estimation.
5. **Why not deep learning?** Neural networks overfit to room-specific multipath and fail when moved to new buildings without retraining.
6. **What is the baseline method?** Nonlinear least-squares trilateration initialized by algebraic matrix inversion.
7. **What is your robust method?** Dual-stage Huber loss optimization with Cauchy residual consistency weighting.
8. **What does the Kalman filter do?** Enforces physical cart momentum, smooths high-frequency noise, and coasts through dropouts.
9. **What happens when an anchor fails?** The system continues on surviving anchors; if $<3$ remain, the filter coasts for up to 3 seconds.
10. **Why 3 seconds?** In a hospital, coasting longer than 3 seconds without an IMU causes excessive drift during corridor turns.
11. **What is the confidence ellipse?** The 95% nominal uncertainty boundary derived from the Kalman covariance matrix using $\chi^2(2) = 5.991$.
12. **Why is NLOS coverage only 49%?** The covariance models random noise dispersion, not constant unmodeled wall bias.
13. **Why does LOCUS-X lose to baseline under NLOS?** The majority-bias failure mode: when 85% of anchors are obstructed, the solver downweights the clean sensor.
14. **Why is LOCUS-X RMSE higher in 3-anchor packet loss?** Survivorship bias: baseline only answered 22% of the time, while LOCUS-X maintained 83% tracking availability.
15. **Why does baseline beat LOCUS-X in clean conditions?** The Kalman filter exhibits 1-to-2 frames of kinematic lag on sharp $90^\circ$ hallway turns.
16. **Is ground truth leaked to the solver?** Absolutely not; verified by automated tests shifting ground truth without affecting estimates.
17. **What is the execution latency?** Total backend computation is under 20 milliseconds per frame.
18. **How many benchmark frames did you run?** 8,960 frames across 7 scenarios, 4 anchor counts, and 2 held-out seeds.
19. **What database is used?** In-memory dictionaries bounded to 64 sessions for sub-millisecond execution.
20. **What is your next priority?** Adding corridor map-matching to eliminate majority-bias NLOS errors.
