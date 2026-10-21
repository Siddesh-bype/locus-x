# 04 — Live Prototype Demo Script (2.5 to 3 Minutes)

## 1. Visual Legend: What the Screen Displays

Before beginning the demo, ensure you know what every visual element on the screen represents:

```
+------------------------------------+-----------------------+--------------------------------------------------------------+
| Screen Element                     | Color / Visual Style  | Technical Meaning & Truth Source                             |
+------------------------------------+-----------------------+--------------------------------------------------------------+
| **Simulated Target (Asset Cart)**  | White box / cart mesh | **Simulator Ground Truth**: True physical cart coordinates.  |
| **Conventional Baseline Fix**      | Amber diamond marker  | **Instantaneous NLS Fix**: Static trilateration position.    |
| **LOCUS-X Estimate**               | Mint-green circle     | **Temporal Tracking Fix**: Kalman-filtered robust estimate.  |
| **Past Trajectory Trail**          | Mint-green line strip | History of recent LOCUS-X positions (last 600 frames).       |
| **95% Confidence Ellipse**         | Semi-transparent mint | Covariance region: $\sqrt{5.991 \times \lambda_i}$ axes.     |
| **Line-of-Sight Signal Ray**       | Solid green / cyan    | Simulator ray: Direct unobstructed radio path.               |
| **Obstructed NLOS Signal Ray**     | Dashed red line       | Simulator ray: Ray intersects $\ge 1$ wall segments.         |
| **Anchor Towers (A1 to A8)**       | Glowing cyan pillars  | Known reference coordinates on the perimeter/walls.          |
| **Disabled / Missing Anchor**      | Dim grey pillar       | Suppressed by user toggle, packet drop, or timed outage.     |
| **Cutaway Hospital Walls**         | Dark teal geometry    | Physical obstacle segments (0.55m / 0.85m rendering height). |
+------------------------------------+-----------------------+--------------------------------------------------------------+
```

---

## 2. Step-by-Step 3-Minute Presentation Demo Script

### PRE-DEMO CHECKLIST (T minus 2 minutes)
1. Ensure backend is running: `.\.venv\Scripts\python.exe -m uvicorn backend.locus_x.main:app --host 127.0.0.1 --port 8000`
2. Ensure frontend is running: `npm.cmd run dev -- --port 5173 --strictPort --configLoader native`
3. Open browser to **`http://127.0.0.1:5173/`**
4. Verify top-right indicator shows: **"Last response OK"** (green dot).
5. Click **"Reset"** (circular arrow button) to ensure a clean starting state at $t = 0.0\text{ s}$.

---

### ACT 1: The Baseline & Digital Twin (0:00 – 0:45)

#### Action 1.1: Introduce the Environment
- **What to click:** Orbit the 3D scene slightly using the mouse left-click drag. Point to the screen.
- **What visibly changes:** 3D perspective shifts smoothly over the 30 × 20 m hospital ward, revealing rooms, corridors, and 8 glowing anchor pillars.
- **Backend computation:** Static render from cached `hospital_floor_01.json`.
- **What to say to judges:**
  > *"Judges, this is LOCUS-X. We are looking at a 3D digital twin of a 600-square-meter hospital ward with 8 wall-mounted anchors, shown as glowing pillars. Our goal is to track a mobile medical asset cart in real time using only noisy, sparse range signals."*

#### Action 1.2: Start the Simulation
- **What to click:** Click the green **"Start simulation"** button (top right of the viewport).
- **What visibly changes:**
  - Timer starts ticking ($0.2\text{ s}, 0.4\text{ s}, 0.6\text{ s}\dots$).
  - White cart begins moving down the central corridor.
  - Amber diamond (baseline) and mint circle (LOCUS-X) appear, closely following the cart.
  - Solid signal rays connect the cart to anchors.
  - Live error chart at the bottom begins plotting error curves.
- **Backend computation:** Serial loop sends `POST /advance` every 200 ms; backend solves baseline NLS, robust M-estimator, and Kalman filter.
- **What to say to judges:**
  > *"As I start the simulation, the white cart navigates a clinical route. Notice the two localization fixes: the amber diamond is the standard industry trilateration baseline; the mint-green circle is our proposed LOCUS-X pipeline. In clean line-of-sight conditions, both track the cart within centimeters."*

---

### ACT 2: Stress Testing with Noise & Jitter (0:45 – 1:30)

#### Action 2.1: Inject High Measurement Noise
- **What to click:** Drag the **"Range noise · σ"** slider from `0.15 m` up to **`0.80 m`** and release the mouse.
- **What visibly changes:**
  - Amber diamond begins violently jittering and jumping off the corridor path.
  - Mint circle (LOCUS-X) remains remarkably smooth and centered along the true route.
  - In the error chart, the amber baseline curve spikes to $>1.5\text{ m}$, while the mint LOCUS-X curve stays below $0.4\text{ m}$.
- **Backend computation:** `PATCH /controls` updates `noise_std = 0.8`. `session.py` rolls back state snapshot, regenerates noisy ranges, and solves.
- **What to say to judges:**
  > *"Now, let's inject realistic sensor noise. I'll increase range noise standard deviation to 80 centimeters. Watch the amber baseline: it begins jittering erratically, spiking over 1.5 meters of error. But look at LOCUS-X in mint green: because our constant-velocity Kalman filter models physical inertia and rejects innovation spikes, its trajectory remains smooth and clinically usable."*

---

### ACT 3: Wall Obstruction & NLOS Bias (1:30 – 2:10)

#### Action 3.1: Demonstrate Non-Line-of-Sight Propagation
- **What to click:** As the cart turns into Ward B or Room 3 behind walls, click the **"Diagnostics"** button on the bottom overlay toolbar.
- **What visibly changes:**
  - Diagnostics tray expands showing anchor residual meters and consistency weights.
  - Signal rays passing through walls turn into **dashed red lines**.
  - In the telemetry panel, the obstructed anchors show negative residuals and their consistency weights drop from 1.0 down to ~0.30.
- **Backend computation:** Ray-segment intersections trigger positive wall bias ($+2.5\text{ m}$ per wall). Robust M-estimator's Cauchy kernel reduces weight $w_i$.
- **What to say to judges:**
  > *"As the cart moves behind concrete walls, our ray-casting engine detects physical obstructions—visualized as dashed red signal rays. Notice the 'Anchor Consistency' table on the right: LOCUS-X's M-estimator dynamically identifies that these obstructed ranges disagree with geometric consensus and downweights their influence in the position fix."*
- **The Honest Nuance (Golden Judge Moment):**
  > *"However, we want to be completely honest about a fundamental limitation we discovered: if the cart moves into an interior room where 80% of anchors are behind walls, the biased anchors form a corrupt consensus and our estimator can downweight the clean sensor. Discovering this majority-bias failure mode in our benchmarks is a key insight of our research."*

---

### ACT 4: Network Chaos & Outage Coasting (2:10 – 2:45)

#### Action 4.1: Inject Combined Chaos / Anchor Failure
- **What to click:** Click the purple button: **"Inject combined failures ↗"** (or manually click anchor buttons **A1**, **A2**, **A3**, **A4** to disable them).
- **What visibly changes:**
  - 4 anchor towers turn dark grey.
  - Amber baseline disappears completely (`null` position fix) because fewer than 3 anchors are available.
  - Mint LOCUS-X tracking status shifts to **"Degraded tracking"** (amber banner).
  - The mint uncertainty ellipse **visibly expands**, reflecting growing kinematic uncertainty.
- **Backend computation:** Number of available anchors drops below 3. Baseline solver fails with `"insufficient"`. Kalman filter enters prediction-only mode, growing covariance $P$ via process noise $Q$.
- **What to say to judges:**
  > *"What happens during an infrastructure disaster or packet storm? I'll disable half our anchors. The conventional baseline immediately collapses and produces zero position—hospital staff are blind. But LOCUS-X enters 'Degraded Tracking': our Kalman filter dead-reckons the cart's momentum for up to 3 seconds, while the mint confidence ellipse automatically expands to warn staff that uncertainty has increased. If the outage exceeds 3 seconds, LOCUS-X safely withholds the position to prevent false guidance."*

---

### ACT 5: Evaluation Dashboard & Conclusion (2:45 – 3:00)

#### Action 5.1: Show the Benchmark Dashboard
- **What to click:** Click the **"Evaluation"** tab on the left navigation bar (icon with chart).
- **What visibly changes:** The 3D view pauses; the full reproducible benchmark suite opens, displaying CDF error curves and the 56-row metrics table.
- **What to say to judges:**
  > *"Finally, everything we showed you is backed by our reproducible evaluation suite of 8,960 frames across 7 stress scenarios on a held-out route. LOCUS-X demonstrates how mathematical robustness and transparent diagnostics can turn sparse, noisy indoor signals into a safe, reliable tracking system. Thank you!"*

---

## 3. Contingency & Troubleshooting Plan (What if something glitches?)

| Possible Issue | Immediate Recovery Action | What to Say Calmly to Judges |
|---|---|---|
| **Cart is stuck or not moving** | Check if "PAUSED" is displayed. Click the **"Start simulation"** / Play button. If still frozen, click **"Reset"** (circular arrow). | *"Let me quickly reset our deterministic simulation clock to t=0."* |
| **Error banner: "Connection interrupted"** | Click the **"Reconnect"** button in the red banner. If backend died, restart it in the PowerShell terminal. | *"Our frontend maintains strict session leases; reconnecting to our local FastAPI worker."* |
| **3D scene does not render (blank canvas)** | Click the **"Plan"** view button in the top right of the viewport to switch from 3D perspective to 2D Orthographic Plan view. | *"I'll switch to 2D Plan view for a cleaner geometric representation."* |
| **Camera zoomed out too far or lost** | Click the **"Recenter camera"** button (crosshair icon) in the bottom-right tools overlay. | *"Centering our camera back on the central corridor."* |
| **A slider value didn't take effect** | Remember: Sliders commit when the mouse button is **released** (onPointerUp), not while dragging. Drag and let go. | *"Releasing the parameter control to commit the updated noise variance."* |
