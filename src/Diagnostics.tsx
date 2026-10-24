import { Activity, ArrowUpRight, ChevronDown, Radio } from 'lucide-react';
import { fmt, percent } from './lib';
import type { Floor, Frame, Sample } from './types';
import { windowMetrics } from './workspace';

export default function Diagnostics({ frame, floor, history, technical }: { frame: Frame; floor: Floor; history: Sample[]; technical: boolean }) {
  const tracking = frame.tracking;
  const multifloor = frame.localization_mode !== '2d_on_known_floor';
  const ambiguous = frame.floor_state?.ambiguous || frame.floor_id === null;
  const baseline = windowMetrics(history, 'baseline'), proposed = windowMetrics(history, 'proposed'), ai = windowMetrics(history, 'ai');
  const baselineUnavailable = frame.baseline.status === 'not_applicable';
  const baselineCell = (value: string) => baselineUnavailable ? '—' : value;
  const aiCell = (value: string) => multifloor ? '—' : value;
  const metrics = [
    ['RMSE', baselineCell(fmt(baseline.rmse) + ' m'), fmt(proposed.rmse) + ' m', aiCell(fmt(ai.rmse) + ' m')],
    ['Median', baselineCell(fmt(baseline.median) + ' m'), fmt(proposed.median) + ' m', aiCell(fmt(ai.median) + ' m')],
    ['P95 error', baselineCell(fmt(baseline.p95) + ' m'), fmt(proposed.p95) + ' m', aiCell(fmt(ai.p95) + ' m')],
    ['Valid position', baselineCell(percent(baseline.validRate)), percent(proposed.validRate), aiCell(percent(ai.validRate))],
    ['Fresh update', baselineCell(percent(baseline.updateRate)), percent(proposed.updateRate), aiCell(percent(ai.updateRate))],
    ['Mean latency', baselineCell(fmt(baseline.latency, 1) + ' ms'), fmt(proposed.latency, 1) + ' ms', aiCell(fmt(ai.latency, 1) + ' ms')],
    ['95% coverage', baselineUnavailable ? '—' : 'N/A', percent(proposed.coverage), aiCell(percent(ai.coverage))],
  ];
  const aiStatus = frame.ai_metadata?.status ?? 'unavailable';
  const aiVersion = frame.ai_metadata?.model_version ?? 'unavailable';
  return <aside className="diagnostics-panel" aria-label="Actual localization results">
    <div className="panel-heading"><Activity size={16} /><h2>{multifloor ? 'Multi-floor result' : 'Live comparison'}</h2><span className="live-label">{multifloor ? '3D PIPELINE' : '3 METHODS'}</span></div>
    <section className="current-errors">
      <div><span><i className="legend-mark baseline" />{multifloor ? '2D baseline' : 'Baseline'}</span><strong>{baselineUnavailable ? '—' : fmt(frame.evaluation.baseline_error)}<small>{baselineUnavailable ? '' : 'm'}</small></strong></div>
      <div><span><i className="legend-mark proposed" />LOCUS-X</span><strong>{fmt(frame.evaluation.proposed_error)}<small>m</small></strong></div>
      {!multifloor && <div><span><i className="legend-mark ai" />LOCUS-X + AI</span><strong>{fmt(frame.evaluation.ai_error)}<small>m</small></strong></div>}
      <p>{multifloor ? 'Horizontal error against simulator truth · no 2D baseline exists' : `Current error against simulator truth. AI status: ${aiStatus} (${aiVersion})${frame.ai_metadata?.ambiguous ? ', ambiguous consensus' : ''}.`}</p>
    </section>
    <section className="comparison-section">
      <div className="section-title"><h3>Comparison window</h3><span className="mono">{history.length} frames</span></div>
      <table className="comparison-table"><thead><tr><th scope="col">Metric</th><th scope="col">Baseline</th><th scope="col">LOCUS-X</th><th scope="col">LOCUS-X + AI</th></tr></thead><tbody>
        {metrics.map(([label, base, value, aiValue]) => <tr key={label}><th scope="row">{label}</th><td>{base}</td><td>{value}</td><td>{aiValue}</td></tr>)}
      </tbody></table>
      <p className="small-note">{fmt(history[0]?.t, 1)}–{fmt(history.at(-1)?.t, 1)} s · {proposed.valid}/{proposed.count} valid fixes<br />Coverage: {proposed.coverageCount} available ellipses. Baseline coverage is not evaluated. Fault edits retain tracker history.</p>
    </section>
    <section className="telemetry-section">
      <div className="section-title"><h3>Position · {ambiguous ? 'floor ambiguous' : floor.short_name}</h3><span className={'tracking-state ' + tracking.status}><i />{tracking.status}</span></div>
      <div className="coordinates"><div><span>X</span><strong>{fmt(tracking.position?.[0])}<small>m</small></strong></div><div><span>Y</span><strong>{fmt(tracking.position?.[1])}<small>m</small></strong></div></div>
      <p className="tracking-reason">{tracking.reason}</p>
      <div className="telemetry-row"><span>Nominal ellipse · major</span><b>{fmt(tracking.ellipse?.major)} m</b></div>
      {multifloor ? <>
        <div className="telemetry-row"><span>Device elevation · estimate</span><b>{fmt(frame.elevation_estimate)} m</b></div>
        <div className="telemetry-row"><span>Truth elevation</span><b>{fmt(frame.evaluation.ground_truth_elevation)} m</b></div>
        <div className="telemetry-row"><span>Storey source</span><b>{frame.floor_state ? frame.floor_state.source.replace('_', ' ') : '—'}</b></div>
      </> : <div className="telemetry-row"><span>Known floor elevation</span><b>{fmt(frame.floor_elevation, 1)} m</b></div>}
      {technical && <><div className="telemetry-row"><span>Velocity · vx / vy</span><b>{fmt(tracking.velocity?.[0], 1)} / {fmt(tracking.velocity?.[1], 1)} m/s</b></div><div className="telemetry-row"><span>Geometry condition</span><b>{fmt(frame.robust.condition_number, 1)}</b></div><div className="telemetry-row"><span>Without an update</span><b>{fmt(tracking.outage_seconds, 1)} s</b></div></>}
    </section>
    {!multifloor && <details className="anchor-diagnostics" open>
      <summary><span><Radio size={14} />Range observations</span><ChevronDown size={14} /></summary>
      <table className="anchor-table"><thead><tr><th>Site</th><th>Range · m</th><th>{technical ? 'Residual · m' : 'Sim. label'}</th><th>Weight</th><th>AI p(LOS)</th><th>AI wt</th></tr></thead><tbody>
        {frame.anchors.map(anchor => {
          const label = frame.simulator_labels.find(l => l.anchor_id === anchor.id);
          const measurement = frame.measurements.find(m => m.anchor_id === anchor.id);
          const weight = frame.robust.reliability[anchor.id];
          const prediction = frame.ai_metadata?.predictions.find(p => p.anchor_id === anchor.id);
          return <tr key={anchor.id}><th><i className={'status-dot ' + label?.kind} />{anchor.id}</th><td>{fmt(measurement?.observed_range)}</td><td className={label?.kind === 'nlos' ? 'fault-text' : 'muted'}>{technical ? fmt(frame.robust.residuals[anchor.id]) : label?.kind === 'packet_loss' ? 'LOST' : label?.kind.toUpperCase()}</td><td>{fmt(weight)}</td><td>{fmt(prediction?.p_los)}</td><td>{fmt(prediction?.weight)}</td></tr>;
        })}
      </tbody></table>
      <p className="small-note">Weights measure residual consistency. AI p(LOS) and AI weight come from the loaded classifier; AI wt is applied only when the model is active. Simulator labels are evaluation-only.</p>
    </details>}
    <div className="inspector-foot"><ArrowUpRight size={13} /><span>{multifloor ? '3D ranges on a derived storey.' : '2D estimation on a known floor; AI is a learned reliability adapter.'}<br />{multifloor ? 'Floor identity is inferred or route-assisted; elevation is estimated.' : 'Elevation is configuration, not an estimate.'}</span></div>
  </aside>;
}
