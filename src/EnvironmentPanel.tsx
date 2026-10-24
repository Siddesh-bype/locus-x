import { useState, type CSSProperties } from 'react';
import { Building2, Check, ChevronDown, Layers3, RotateCcw } from 'lucide-react';
import Controls from './Controls';
import type { EnvironmentDefinition, Floor, Frame } from './types';
import type { UpdateSettings, SessionMode } from './useSession';
import { floorsOf, isBuilding, LAYER_NAMES, type SceneLayer, type SceneLayers } from './workspace';

interface Props {
  environment: EnvironmentDefinition; environments: EnvironmentDefinition[]; floor: Floor;
  frame: Frame; busy: boolean; update: UpdateSettings;
  selectEnvironment: (id: string) => void; selectFloor: (id: string) => void;
  selectMode: (mode: SessionMode) => void;
  layers: SceneLayers; setLayer: (layer: SceneLayer, value: boolean) => void; resetLayers: () => void;
  cutaway: boolean; setCutaway: (value: boolean) => void;
}

export default function EnvironmentPanel(props: Props) {
  const [section, setSection] = useState('experiment');
  const { environment, floor, frame, layers, busy } = props;
  const multifloor = frame.localization_mode !== '2d_on_known_floor';
  const floors = floorsOf(environment);
  const transitionRoutes = isBuilding(environment) ? environment.transition_routes ?? [] : [];
  const routeOptions = multifloor ? Object.fromEntries(transitionRoutes.map(route => [route.id, route.name])) : undefined;
  const availability = (layer: SceneLayer) => layer === 'candidates' ? floor.candidate_anchors.length > 0 : layer === 'corridors' ? floor.corridors.length > 0 : layer === 'access' ? floor.access_cores.length > 0 : true;
  return <aside className="control-sidebar" aria-label="Environment and experiment controls">
    <div className="panel-heading"><Building2 size={16} /><h2>Environment</h2><span className="version-tag">{isBuilding(environment) ? 'V' + environment.version : 'V1 REFERENCE'}</span></div>
    <section className="environment-section">
      <label className="select-wrap"><select aria-label="Environment" value={environment.id} disabled={busy} onChange={event => props.selectEnvironment(event.target.value)}>
        {props.environments.map(env => <option key={env.id} value={env.id}>{env.name}</option>)}
      </select><ChevronDown size={14} /></label>
      <div className="environment-meta"><span>{environment.width} × {environment.height} m</span><span>{floors.length} {floors.length === 1 ? 'floor' : 'floors'}</span><span>Metre scale</span></div>
      {isBuilding(environment) && <><label className="field-label" htmlFor="session-mode">Session type</label>
        <label className="select-wrap"><select id="session-mode" aria-label="Session type" value={frame.localization_mode} disabled={busy} onChange={event => props.selectMode(event.target.value as SessionMode)}>
          <option value="2d_on_known_floor">2D · known floor</option>
          <option value="multifloor_inferred">Multi-floor · inferred</option>
          <option value="multifloor_route_assisted">Multi-floor · route assisted</option>
        </select><ChevronDown size={14} /></label></>}
      <div className="floor-list" role="group" aria-label="Simulation floor">
        {floors.map(item => <button key={item.id} className={'floor-option ' + (frame.floor_id === item.id ? 'selected' : '')}
          aria-label={'Select ' + item.name} aria-pressed={frame.floor_id === item.id} disabled={busy || multifloor}
          style={{ '--floor-color': item.color } as CSSProperties} onClick={() => props.selectFloor(item.id)}>
          <b>{item.short_name}</b><span><strong>{item.name}</strong><small>{item.elevation.toFixed(1)} m elevation · {item.rooms.length} rooms</small></span>{frame.floor_id === item.id ? <Check size={14} /> : <span className="floor-arrow">↗</span>}
        </button>)}
      </div>
      <p className="small-note">{multifloor ? 'The storey is derived from ranges or the configured route; it is not selected.' : 'Selecting another floor starts a fresh 2D run on that known floor.'}</p>
    </section>
    <div className="sidebar-tabs" role="tablist" aria-label="Control sections">
      <button role="tab" aria-selected={section === 'experiment'} aria-controls="experiment-controls" id="experiment-tab" onClick={() => setSection('experiment')}>Experiment</button>
      <button role="tab" aria-selected={section === 'layers'} aria-controls="scene-layers" id="layers-tab" onClick={() => setSection('layers')}><Layers3 size={13} />Scene layers</button>
    </div>
    <div id="experiment-controls" role="tabpanel" aria-labelledby="experiment-tab" hidden={section !== 'experiment'}><Controls frame={frame} floor={floor} update={props.update} routes={routeOptions} /></div>
    <div id="scene-layers" role="tabpanel" aria-labelledby="layers-tab" hidden={section !== 'layers'} className="layer-panel">
      {([
        ['Architecture', ['rooms', 'corridors', 'walls', 'labels', 'access']],
        ['Infrastructure', ['anchors', 'candidates']],
        ['Experiment overlays', ['route', 'truth', 'baseline', 'proposed', 'uncertainty', 'signals']],
      ] as [string, SceneLayer[]][]).map(([title, keys]) => <fieldset key={title}><legend>{title}</legend>{keys.map(key => <label className="layer-option" key={key}>
        <input type="checkbox" checked={layers[key] && availability(key)} disabled={!availability(key)} onChange={event => props.setLayer(key, event.target.checked)} />
        <span>{LAYER_NAMES[key]}{key === 'candidates' && <small>{floor.candidate_anchors.length} configured sites · no optimizer</small>}{!availability(key) && key !== 'candidates' && <small>Not declared in this configuration</small>}</span>
      </label>)}</fieldset>)}
      <label className="layer-option cutaway-control"><input type="checkbox" checked={props.cutaway} disabled={!layers.walls} onChange={event => props.setCutaway(event.target.checked)} /><span>Cutaway wall height<small>Show interiors; propagation stays unchanged</small></span></label>
      <p className="small-note">Signal paths use simulator truth. Layer visibility never changes measurements.</p>
      <button className="text-button" onClick={props.resetLayers}><RotateCcw size={13} />Restore default layers</button>
    </div>
    <div className="sidebar-foot"><span className="status-dot" />Local simulation<span>No radio hardware</span></div>
  </aside>;
}
