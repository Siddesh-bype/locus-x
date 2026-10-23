import { ArrowUpDown, RadioTower } from 'lucide-react';
import { fmt, percent } from './lib';
import type { Floor, Frame } from './types';
import { isMultifloor, sourceLabel, transitionLabel } from './workspace';

/**
 * The multi-floor storey is reported, never selected. This chip says which storey the
 * backend claims, why, and whether it is ambiguous; the banner says which traverse the
 * tracker currently believes it is in.
 */
export default function MultifloorStatus({ frame, floors }: { frame: Frame; floors: Floor[] }) {
  if (!isMultifloor(frame) || !frame.floor_state) return null;
  const state = frame.floor_state;
  const floor = floors.find(item => item.id === state.floor_id) ?? null;
  const ambiguous = state.ambiguous || state.floor_id === null;
  const transition = frame.transition;
  return <div className="multifloor-status" role="status" aria-live="polite">
    <div className="floor-chip">
      <span className="floor-badge" style={{ borderColor: ambiguous ? '#9b805e' : floor?.color }}>{ambiguous ? '?' : floor?.short_name ?? '—'}</span>
      <div>
        <strong>{ambiguous ? 'Floor ambiguous' : floor?.name ?? state.floor_id}<span className={'source-badge ' + state.source}>{sourceLabel(state)}</span></strong>
        <small>{ambiguous
          ? 'No storey is claimed while the vertical uncertainty spans floors.'
          : state.source === 'route_assisted' ? 'Storey follows the configured route, not inference.'
            : state.source === 'configured' ? 'Storey fixed by the selected configuration.' : 'Storey derived from the 3D ranges.'}</small>
      </div>
    </div>
    <div className="multifloor-facts">
      {state.source === 'inferred' && state.confidence != null && <span>Confidence <b>{percent(state.confidence)}</b></span>}
      {frame.elevation_estimate != null && <span>Elevation estimate <b>{fmt(frame.elevation_estimate)} m</b></span>}
      {frame.evaluation.ground_truth_elevation != null && <span>Truth elevation <b>{fmt(frame.evaluation.ground_truth_elevation)} m</b></span>}
    </div>
    {transition && <div className="transition-banner">
      <ArrowUpDown size={14} aria-hidden />
      <strong>{transitionLabel(transition)}</strong>
      {transition.progress != null && <span>{percent(transition.progress)} through the traverse</span>}
      <RadioTower size={12} aria-hidden />
    </div>}
  </div>;
}
