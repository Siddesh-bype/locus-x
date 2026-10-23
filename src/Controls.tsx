import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Radio, RotateCw, SlidersHorizontal } from 'lucide-react';
import { fmt, scenarioNames } from './lib';
import type { Floor, Frame, Scenario } from './types';
import type { UpdateSettings } from './useSession';

function Slider({ label, value, max, step, unit, onCommit }: { label: string; value: number; max: number; step: number; unit: string; onCommit: (value: number) => Promise<boolean> }) {
  const [draft, setDraft] = useState(value);
  const committed = useRef(value);
  useEffect(() => { setDraft(value); committed.current = value; }, [value]);
  const commit = async () => {
    if (committed.current === draft) return;
    committed.current = draft;
    if (!await onCommit(draft)) { setDraft(value); committed.current = value; }
  };
  return <label className="slider-field"><span>{label}<output>{fmt(unit === '%' ? draft * 100 : draft, unit === '%' ? 0 : 2)}<small>{unit}</small></output></span>
    <input type="range" min="0" max={max} step={step} value={draft} style={{ '--range': String(draft / max * 100) + '%' } as React.CSSProperties}
      onChange={event => setDraft(Number(event.target.value))} onPointerUp={() => void commit()} onPointerCancel={() => setDraft(value)} onBlur={() => void commit()}
      onKeyUp={event => { if (event.key.startsWith('Arrow') || ['Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) void commit(); }} aria-label={label} />
    <span className="range-labels"><small>0</small><small>{unit === '%' ? max * 100 : max} {unit}</small></span>
  </label>;
}

export default function Controls({ frame, floor, update, routes }: { frame: Frame; floor: Floor; update: UpdateSettings; routes?: Record<string, string> }) {
  const settings = frame.settings;
  const multifloor = frame.localization_mode !== '2d_on_known_floor';
  // The API repeats an anchor ID on every storey. A bare disabled anchor applies to all
  // of them, so show each ID once rather than pretending they are independent sites.
  const anchorIds = multifloor ? [...new Set(frame.anchors.map(anchor => anchor.id))] : frame.anchors.map(anchor => anchor.id);
  const active = anchorIds.filter(id => !settings.disabled_anchors.includes(id)).length;
  const routeOptions = routes ?? Object.fromEntries(Object.keys(floor.routes).map(id => [id, floor.route_names[id] ?? id]));
  return <div className="experiment-controls">
    <section className="control-section">
      <div className="section-title"><h3>Scenario</h3><SlidersHorizontal size={14} /></div>
      <label className="select-wrap"><select aria-label="Scenario preset" value={settings.scenario} onChange={event => void update({ scenario: event.target.value as Scenario })}>{Object.entries(scenarioNames).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><ChevronDown size={14} /></label>
      <p className="control-note">{settings.scenario === 'anchor_failure' ? 'Alternating anchors fail at 12–22 s of each 40 s cycle.' : settings.scenario === 'chaos' ? 'Wall bias, range noise, lost packets and timed anchor outages.' : settings.scenario === 'nlos' ? 'Obstructed ranges receive positive wall-intersection bias.' : 'Both methods receive exactly the same seeded observations.'}</p>
      <Slider label="Range noise · σ" value={settings.noise_std} max={3} step={0.05} unit="m" onCommit={noise_std => update({ noise_std })} />
      <Slider label="NLOS bias" value={settings.nlos_bias} max={10} step={0.1} unit="m" onCommit={nlos_bias => update({ nlos_bias })} />
      <Slider label="Packet loss" value={settings.dropout} max={1} step={0.01} unit="%" onCommit={dropout => update({ dropout })} />
      <label className="switch-line"><span>Wall range bias<small>Apply obstruction in the simulator</small></span><input type="checkbox" checked={settings.walls_enabled} onChange={event => void update({ walls_enabled: event.target.checked })} /><i /></label>
    </section>
    <section className="control-section">
      <div className="section-title"><h3>Anchor network</h3><span className="mono">{active}/{frame.anchors.length}</span></div>
      <div className="anchor-toggles">{anchorIds.map(id => {
        const enabled = !settings.disabled_anchors.includes(id);
        const available = frame.simulator_labels.find(l => l.anchor_id === id)?.available;
        return <button key={id} className={(enabled ? 'enabled' : 'disabled') + (enabled && !available ? ' missing' : '')}
          aria-pressed={enabled} aria-label={'Toggle anchor ' + id}
          onClick={() => void update(current => ({ disabled_anchors: current.disabled_anchors.includes(id) ? current.disabled_anchors.filter(item => item !== id) : [...current.disabled_anchors, id] }))}>
          <Radio size={12} />{id}<i />
        </button>;
      })}</div>
      <p className="small-note">{multifloor ? 'Each ID is installed on every storey; this toggle disables that ID everywhere. ' : ''}At least three non-collinear anchors for a range fix.</p>
    </section>
    <section className="control-section">
      <div className="section-title"><h3>{multifloor ? 'Route & reproducibility' : 'Target & reproducibility'}</h3><RotateCw size={14} /></div>
      <label className="field-label" htmlFor="target-route">{multifloor ? 'Multi-floor route' : 'Service route'}</label>
      <label className="select-wrap"><select id="target-route" aria-label="Target route" value={settings.route} onChange={event => void update({ route: event.target.value })}>
        {Object.entries(routeOptions).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
      </select><ChevronDown size={14} /></label>
      <div className="compact-controls">
        <label>Speed · m/s<input aria-label="Target speed" type="number" min="0.2" max="3" step="0.1" key={settings.speed} defaultValue={settings.speed}
          onBlur={event => { const value = Number(event.target.value); if (event.target.value.trim() && value >= 0.2 && value <= 3) { if (value !== settings.speed) void update({ speed: value }); } else event.target.value = String(settings.speed); }}
          onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} /></label>
        <label>Random seed<input aria-label="Random seed" type="number" min="0" max="2147483647" key={settings.seed} defaultValue={settings.seed}
          onBlur={event => { const value = Number(event.target.value); if (event.target.value.trim() && Number.isInteger(value) && value >= 0 && value <= 2147483647) { if (value !== settings.seed) void update({ seed: value }); } else event.target.value = String(settings.seed); }}
          onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }} /></label>
      </div>
      <p className="small-note">Route, speed and seed edits restart time. Reset restores the session’s initial floor and settings.</p>
    </section>
  </div>;
}
