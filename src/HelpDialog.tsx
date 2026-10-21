import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';

export default function HelpDialog({ close }: { close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog className="help-modal" ref={dialog} onCancel={close} aria-labelledby="help-title">
    <button className="icon-button modal-close" aria-label="Close operating guide" onClick={close} autoFocus><X size={19} /></button>
    <span className="eyebrow">LOCUS-X / OPERATING GUIDE</span><h2 id="help-title">Know what you’re measuring.</h2>
    <p>Explore a fictional hospital, introduce noisy or missing ranges, and compare two estimators on identical observations.</p>
    <dl>
      <dt>Inspect the building</dt><dd>Use Floor, Building or Exploded view. Drag to orbit, right-drag to pan, and scroll to zoom. Plan view isolates the selected floor. Selecting a different floor pauses the experiment and resets its tracking state.</dd>
      <dt>Read the comparison</dt><dd>Baseline uses nonlinear least squares. LOCUS-X adds Huber loss, residual consistency weights and a constant-velocity Kalman filter. Both receive the same ranges. No trained AI classifier is integrated.</dd>
      <dt>Interpret the overlays</dt><dd>The cart, simulated signals and LOS/NLOS labels belong to the simulator. They are not inference inputs. Known floor elevation places a 2D result in the building; vertical position and floor transitions are not estimated.</dd>
      <dt>Uncertainty and missing fixes</dt><dd>The nominal 95% ellipse may be overconfident under bias. Prediction-only fixes remain available for up to three seconds; then the position is withheld. Missing errors stay missing. Baseline uncertainty coverage is not evaluated.</dd>
      <dt>Repeat an experiment</dt><dd>Reset restores the session’s initial floor, route, seed and settings. Route, speed or seed changes restart time. The live comparison window clears when settings change and retains at most 600 samples. Export its CSV or recreate a run with Scenario JSON. The benchmark workspace runs independent, versioned experiments.</dd>
      <dt>Candidate sites</dt><dd>These are configured installation candidates, distinct from the active anchor layout. They do not emit measurements. The optimization and trained reliability-model workflows are future work.</dd>
    </dl>
    <p className="small-note">Synthetic range observations. Fictional architecture. Real-radio accuracy and clinical building compliance have not been validated.</p>
    <button className="primary-button" onClick={close}>Return to workspace</button>
  </dialog>;
}
