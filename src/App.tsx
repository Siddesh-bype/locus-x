import { lazy, Suspense, useReducer, useState } from 'react';
import { Activity, ArrowDownToLine, Box, ChartNoAxesCombined, Check, ChevronRight, CircleHelp, Crosshair, Focus, Layers3, LoaderCircle, Maximize2, Minus, Pause, Play, Plus, RotateCcw, Settings2, SkipForward, X } from 'lucide-react';
import Benchmark from './Benchmark';
import { ErrorChart } from './Charts';
import Diagnostics from './Diagnostics';
import EnvironmentPanel from './EnvironmentPanel';
import HelpDialog from './HelpDialog';
import { download, fmt, samplesCsv } from './lib';
import MultifloorStatus from './MultifloorStatus';
import { useSession } from './useSession';
import { DEFAULT_LAYERS, floorsOf, INITIAL_VIEW, isBuilding, isMultifloor, toggleLayer, viewReducer } from './workspace';

const Scene = lazy(() => import('./Scene'));

export default function App() {
  const session = useSession();
  const { frame, environment, floor, history, running, setRunning } = session;
  const [tab, setTab] = useState('simulation');
  const [technical, setTechnical] = useState(false);
  const [layers, setLayers] = useState(DEFAULT_LAYERS);
  const [view, dispatchView] = useReducer(viewReducer, INITIAL_VIEW);
  const [cutaway, setCutaway] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [help, setHelp] = useState(false);
  const floors = environment ? floorsOf(environment) : [];
  const multifloor = frame ? isMultifloor(frame) : false;
  const displayFloor = floor ?? floors[0] ?? null;
  // An ambiguous multi-floor storey has no floor to isolate; show the whole building.
  const activeView = floors.length === 1 ? { ...view, mode: 'floor' as const }
    : multifloor && frame?.floor_id === null && view.mode === 'floor' ? { ...view, mode: 'building' as const } : view;
  const selectFloor = (id: string) => { if (!multifloor) void session.selectFloor(id).then(ok => { if (ok) dispatchView({ type: 'floor' }); }); };
  const selectEnvironment = (id: string) => { void session.selectEnvironment(id).then(ok => {
    if (ok) dispatchView({ type: 'floor' });
  }); };
  // Walking to another building from the 3D campus keeps the current view mode.
  const visitBuilding = (id: string) => { void session.selectEnvironment(id); };
  const openTab = (value: string) => { if (value !== 'simulation') setRunning(false); setTab(value); };
  const floorLabel = frame ? frame.floor_id ?? 'floor-ambiguous' : 'unknown';
  const exportScenario = () => frame && download('locus-x-' + frame.environment_id + '-' + floorLabel + '-scenario.json', JSON.stringify({ environment_id: frame.environment_id, floor_id: frame.floor_id, settings: frame.settings }, null, 2));
  const exportHistory = () => frame && download('locus-x-' + frame.environment_id + '-' + floorLabel + '-window.csv', samplesCsv(history), 'text/csv');

  return <div className="app-shell">
    <header className="app-header">
      <a className="brand" href="/" aria-label="LOCUS-X home"><span className="brand-icon"><Crosshair size={24} strokeWidth={1.6} /></span><span>LOCUS<span className="brand-x">-X</span></span><small>SPATIAL SYSTEMS</small></a>
      <div className="header-context"><span>RESEARCH WORKSPACE</span><ChevronRight size={13} /><strong>Indoor positioning</strong><span className="version-tag">V3</span></div>
      <div className="header-right"><span className={'connection ' + (session.error ? 'offline' : frame ? '' : 'connecting')} title="Status reflects the latest HTTP response"><i />{session.error ? 'Engine interrupted' : frame ? 'Last response OK' : 'Connecting'}</span><span className="team-avatar" title="Epoch_12 · VIT Pune">E12</span></div>
    </header>
    <div className="app-body">
      <nav className="icon-nav" aria-label="Workspace navigation">
        <button aria-label="Digital twin workspace" title="Digital twin" aria-current={tab === 'simulation' ? 'page' : undefined} onClick={() => openTab('simulation')}><Box size={21} /><span>Twin</span></button>
        <button aria-label="Evaluation and benchmarks" title="Benchmarks" aria-current={tab === 'evaluation' ? 'page' : undefined} onClick={() => openTab('evaluation')}><ChartNoAxesCombined size={21} /><span>Evaluate</span></button>
        <div className="nav-spacer" /><button aria-label="Methodology and operating guide" title="Operating guide" onClick={() => setHelp(true)}><CircleHelp size={21} /><span>Guide</span></button>
        <span className="nav-version">03.0</span>
      </nav>
      <div className="workspace">
        <div className="workspace-bar"><div className="workspace-tabs">
          <button className={tab === 'simulation' ? 'selected' : ''} onClick={() => openTab('simulation')}><Layers3 size={15} />Digital twin</button>
          <button className={tab === 'evaluation' ? 'selected' : ''} onClick={() => openTab('evaluation')}><Activity size={15} />Benchmarks</button>
        </div><div className="workspace-label"><span className="synthetic-tag">SYNTHETIC RANGE MODEL</span><span>CODE APEX 3.0 / EPOCH_12</span></div></div>
        {session.error && <div className="error-banner" role="alert"><span>{session.error}</span><button disabled={session.busy} onClick={() => void session.connect()}>{session.busy ? 'Connecting…' : 'Reconnect engine'}</button></div>}
        <div hidden={tab !== 'evaluation'}><Benchmark environments={session.environments} /></div>
        {tab === 'simulation' && (!frame || !environment || !displayFloor ? <main className="loading-state" aria-live="polite">
          <LoaderCircle size={30} className={session.busy ? 'spin' : ''} /><span className="eyebrow">LOCUS-X / ENGINE CONNECTION</span><h1>{session.error ? 'The localization engine is unavailable.' : 'Preparing the experiment workspace.'}</h1>
          <p>{session.error ? 'Start the local FastAPI server on port 8000, then reconnect.' : 'Loading validated floor geometry and the first observations.'}</p>
          {session.error && <code>python -m uvicorn backend.locus_x.main:app --host 127.0.0.1 --port 8000</code>}
        </main> : <>
          <div className="simulation-heading"><div><div className="eyebrow">ENVIRONMENT LAB <span>/</span> {isBuilding(environment) ? floors.length + '-FLOOR ' + (environment.id.startsWith('hospital') ? 'HOSPITAL' : 'BUILDING') : 'REFERENCE FLOOR'}</div><h1>{environment.name}<span className="heading-dot">.</span></h1><p>Same observations. Three estimators. Measured outcomes.</p></div>
            <div className="run-controls"><span className="simulation-time"><i className={running ? 'live-dot' : 'paused-dot'} /><span>{running ? 'RUNNING' : 'PAUSED'}</span><b>{fmt(frame.timestamp, 1)}<small>s</small></b></span>
              <button className="icon-button" aria-label="Reset simulation to initial floor and settings" title="Reset to initial floor and settings" disabled={session.busy} onClick={() => void session.reset()}><RotateCcw size={16} /></button>
              <button className="icon-button" aria-label="Advance one frame" title="Advance one frame · 0.2 s" disabled={running || session.busy || !!session.error} onClick={() => void session.step()}><SkipForward size={17} /></button>
              <button className="primary-button run-button" disabled={!!session.error || (!running && session.busy)} onClick={() => setRunning(!running)}>{running ? <Pause size={15} fill="currentColor" /> : <Play size={15} fill="currentColor" />}{running ? 'Pause experiment' : 'Run experiment'}</button>
            </div>
          </div>
          <div className={'simulation-layout ' + (expanded ? 'expanded-layout' : '')}>
            {!expanded && <EnvironmentPanel environment={environment} environments={session.environments} floor={displayFloor} frame={frame} busy={session.busy && !running} update={session.update} selectEnvironment={selectEnvironment} selectFloor={selectFloor} selectMode={mode => { void session.selectMode(mode); }} layers={layers} setLayer={(key, value) => setLayers(current => toggleLayer(current, key, value))} resetLayers={() => { setLayers(DEFAULT_LAYERS); setCutaway(true); }} cutaway={cutaway} setCutaway={setCutaway} />}
            <main className="simulation-main">
              <section className={'viewport-panel ' + (expanded ? 'expanded-viewport' : '')} aria-label="Interactive building digital twin">
                <div className="viewport-top"><div className="viewport-title"><span className="viewport-status" /><span>SPATIAL VIEW</span><small>{activeView.mode === 'floor' ? displayFloor.name : floors.length + ' floors'}</small></div>
                  <div className="view-switch" role="group" aria-label="Building view">
                    {(['floor', 'building', 'exploded'] as const).map(mode => <button key={mode} aria-pressed={activeView.mode === mode} disabled={floors.length === 1 && mode !== 'floor'} onClick={() => dispatchView({ type: 'mode', mode })}>{mode === 'floor' ? 'Floor' : mode === 'building' ? 'Building' : 'Exploded'}</button>)}
                  </div>
                </div>
                <div className="scene-container"><Suspense fallback={<div className="scene-error"><LoaderCircle className="spin" />Loading building geometry…</div>}><Scene environment={environment} frame={frame} history={history} layers={layers} view={activeView} cutaway={cutaway} selectFloor={selectFloor} selectEnvironment={visitBuilding} /></Suspense></div>
                {multifloor ? <MultifloorStatus frame={frame} floors={floors} /> : <div className="scene-info"><span className="floor-badge" style={{ borderColor: displayFloor.color }}>{displayFloor.short_name}</span><div><strong>{displayFloor.name}<span>ACTIVE EXPERIMENT</span></strong><small>Known floor · 2D localization</small></div></div>}
                <div className="scene-tools"><div className="angle-switch"><button aria-label="Perspective camera" aria-pressed={activeView.angle === 'perspective'} onClick={() => dispatchView({ type: 'angle', angle: 'perspective' })}>3D</button><button aria-label="Plan camera" aria-pressed={activeView.angle === 'plan'} onClick={() => dispatchView({ type: 'angle', angle: 'plan' })}>Plan</button></div>
                  <button className="icon-button" aria-label="Zoom in" title="Zoom in" onClick={() => dispatchView({ type: 'zoom', amount: 1.2 })}><Plus size={17} /></button><button className="icon-button" aria-label="Zoom out" title="Zoom out" onClick={() => dispatchView({ type: 'zoom', amount: 1 / 1.2 })}><Minus size={17} /></button><button className="icon-button" aria-label="Fit building to viewport" title="Fit to viewport" onClick={() => dispatchView({ type: 'reset' })}><Focus size={17} /></button><button className="icon-button" aria-label={expanded ? 'Restore panels' : 'Expand viewport'} title={expanded ? 'Restore panels' : 'Expand viewport'} onClick={() => setExpanded(!expanded)}>{expanded ? <X size={17} /> : <Maximize2 size={16} />}</button>
                </div>
                <div className="viewport-bottom"><div className="scene-legend"><span><i className="legend-mark truth" />Ground truth</span><span><i className="legend-mark baseline" />Baseline</span><span><i className="legend-mark proposed" />LOCUS-X</span><span><i className="legend-mark ai" />LOCUS-X + AI</span><span><i className="legend-ellipse" />Nominal 95%</span></div><span className="orbit-hint">Drag to orbit · scroll to zoom</span></div>
              </section>
              <div className="view-caption"><span>{multifloor
                ? frame.floor_id === null
                  ? 'Storey ambiguous · target drawn at its estimated elevation · physical elevator '
                    + floors.map(item => fmt(item.elevation, 1)).join(' / ') + ' m'
                  : activeView.mode === 'exploded'
                    ? 'Inspection spacing · physical elevations ' + floors.map(item => fmt(item.elevation, 1)).join(' / ') + ' m. Target and anchors sit at their estimated/configured heights.'
                    : activeView.mode === 'building'
                      ? 'Floors at physical elevations. The target is drawn at its estimated height; storey ' + frame.floor_id + ' · ' + (frame.floor_state?.source ?? 'derived') + '.'
                      : 'Storey ' + displayFloor.name + ' · ' + (frame.floor_state?.source ?? 'derived') + ' · target at estimated elevation.'
                : activeView.mode === 'exploded' ? 'Inspection spacing · physical elevations ' + floors.map(item => fmt(item.elevation, 1)).join(' / ') + ' m. Only the active floor has live observations.' : activeView.mode === 'building' ? 'Floors at physical elevations. Inactive anchors show configured sites. Click a floor to inspect.' : displayFloor.description}</span><button className={'text-button ' + (technical ? 'active' : '')} aria-pressed={technical} onClick={() => setTechnical(!technical)}>{technical ? <Check size={13} /> : <Settings2 size={13} />}Diagnostics</button></div>
              <div className="measurement-strip">
                <div><span>OBSERVATIONS</span><strong>{frame.measurements.length}<small>/ {frame.anchors.length} anchors</small></strong></div>
                <div><span>FRAME</span><strong>{String(frame.frame_index).padStart(4, '0')}<small>Δt 0.2 s</small></strong></div>
                <div><span>FILTER UPDATE</span><strong className={'status-word ' + (frame.tracking.updated ? 'mint' : 'amber')}>{frame.tracking.updated ? 'Accepted' : frame.tracking.status === 'degraded' ? 'Prediction only' : 'Unavailable'}<small>{frame.tracking.updated ? 'Fresh range fix' : fmt(frame.tracking.outage_seconds, 1) + ' s without update'}</small></strong></div>
              </div>
              <section className="live-chart-panel"><div className="chart-heading"><div><h2>Position error <span>m</span></h2><p>Against simulator truth · identical observations</p></div><button className="text-button" onClick={exportHistory}><ArrowDownToLine size={14} />Export CSV</button></div><ErrorChart samples={history} height={155} /><div className="chart-footer"><span><i className="legend-mark baseline" />Baseline<i className="legend-mark proposed" />LOCUS-X<i className="legend-mark ai" />LOCUS-X + AI</span><span>{history.length} / 600 samples, clears on configuration changes</span></div></section>
              {technical && <section className="events-panel"><div className="chart-heading"><h2>Engine events</h2><button className="text-button" onClick={exportScenario}><ArrowDownToLine size={14} />Scenario JSON</button></div>{frame.events.slice(-6).reverse().map((event, index) => <div className="event-row" key={index}><time>{fmt(event.timestamp, 1)} s</time><span className={event.level === 'warning' ? 'amber' : ''}>{event.message}</span></div>)}</section>}
            </main>
            {!expanded && <Diagnostics frame={frame} floor={displayFloor} history={history} technical={technical} />}
          </div>
          <footer className="app-footer"><span><i className="status-dot" />{frame.environment_id} / {frame.floor_id ?? 'floor ambiguous'}<span className="footer-divider">·</span>CONFIG {frame.environment_sha256.slice(0, 8)}</span><span>Synthetic measurements · nominal uncertainty is not calibrated confidence<button onClick={() => setHelp(true)}>Operating guide ↗</button></span></footer>
        </>)}
      </div>
    </div>
    {help && <HelpDialog close={() => setHelp(false)} />}
  </div>;
}
