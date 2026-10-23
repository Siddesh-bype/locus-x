import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import buildingJson from '../configs/environments/hospital_v3.json';
import legacyJson from '../configs/environments/hospital_floor_01.json';
import Controls from './Controls';
import Diagnostics from './Diagnostics';
import EnvironmentPanel from './EnvironmentPanel';
import { fromScene, toScene } from './lib';
import MultifloorStatus from './MultifloorStatus';
import { slabRectangles } from './scene/architectureGeometry';
import type { Building, Environment, Frame, Sample } from './types';
import {
  DEFAULT_LAYERS, EXPLODE_GAP, INITIAL_VIEW, displayElevation, elevatedTrajectorySegments, ellipsePoints,
  floorForFrame, floorsOf, recordFrame, sceneFloors, settingsKey, toggleLayer,
  trajectorySegments, transitionLabel, viewReducer, windowMetrics,
} from './workspace';

const building = buildingJson as unknown as Building;
const legacy = legacyJson as unknown as Environment;

/** Test-only data: production telemetry always comes from the API. */
function frame(overrides: Partial<Frame> = {}): Frame {
  const estimate = { position: [5, 14] as [number, number], status: 'valid', reason: 'Range update accepted', residuals: {}, reliability: {}, covariance: [[1, 0], [0, 1]], runtime_ms: 2, condition_number: 2 };
  return {
    session_id: 'test', environment_id: building.id, environment_version: '3.0.0', environment_sha256: 'test-config',
    floor_id: 'ground', floor_elevation: 0, localization_mode: '2d_on_known_floor',
    revision: 0, timestamp: 0, frame_index: 0,
    settings: { scenario: 'noise', noise_std: 0.4, nlos_bias: 0, dropout: 0, disabled_anchors: [], walls_enabled: true, route: 'asset_round', speed: 1.2, seed: 12 },
    anchors: building.floors[0].anchors, measurements: [], simulator_labels: [],
    baseline: estimate, robust: estimate,
    tracking: { position: [7, 14], velocity: [0, 0], covariance: [[1, 0], [0, 1]], ellipse: { major: 2, minor: 1, angle: 0, level: 0.95 }, status: 'tracking', reason: 'Range update accepted', updated: true, outage_seconds: 0, runtime_ms: 1 },
    evaluation: { ground_truth: [4, 14], baseline_error: 1, proposed_error: 3, covered_95: false, has_nlos: false, has_dropout: false }, events: [], ...overrides,
  };
}

/** A confident multi-floor frame; overrides make it ambiguous or route-assisted. */
function multifloorFrame(overrides: Partial<Frame> = {}): Frame {
  const base = frame();
  return {
    ...base, floor_id: 'first', floor_elevation: 4.2, localization_mode: 'multifloor_inferred',
    floor_state: { floor_id: 'first', source: 'inferred', confidence: 0.91, ambiguous: false },
    elevation_estimate: 5.35, floor_probabilities: { ground: 0.01, first: 0.98, second: 0.01 },
    baseline: { ...base.baseline, position: null, status: 'not_applicable', reason: 'The single-floor 2D baseline does not apply to a multi-floor session' },
    evaluation: { ...base.evaluation, baseline_error: null, ground_truth_elevation: 5.4, covered_95: true },
    ...overrides,
  };
}

describe('shared floor and scene contract', () => {
  it('adapts legacy metadata without copying or changing geometry', () => {
    const [floor] = floorsOf(legacy);
    expect(floor.anchors).toBe(legacy.anchors);
    expect(floor.walls).toBe(legacy.walls);
    expect(floor.rooms).toBe(legacy.rooms);
    expect(floor.routes).toBe(legacy.routes);
    expect(floor.elevation).toBe(0);
    expect(floor.candidate_anchors).toEqual([]);
  });
  it('takes the active floor from the backend frame rather than the first catalog item', () => {
    const active = frame({ floor_id: 'second', floor_elevation: 8.4 });
    expect(floorForFrame(building, active)).toBe(building.floors[2]);
    expect(() => floorForFrame(legacy, active)).toThrow('do not match');
    expect(() => floorForFrame(building, { ...active, floor_id: 'roof' })).toThrow('does not match');
    expect(() => floorForFrame(building, { ...active, floor_elevation: 0 })).toThrow('does not match');
  });
  it('returns no floor for an honest multi-floor ambiguity instead of guessing', () => {
    const ambiguous = frame({
      floor_id: null, floor_elevation: null, localization_mode: 'multifloor_inferred',
      floor_state: { floor_id: null, source: 'inferred', confidence: null, ambiguous: true },
    });
    expect(floorForFrame(building, ambiguous)).toBeNull();
    // A legacy frame may never drop its floor.
    expect(() => floorForFrame(legacy, { ...ambiguous, environment_id: legacy.id, localization_mode: '2d_on_known_floor' })).toThrow('missing');
    expect(() => floorForFrame(building, { ...ambiguous, floor_elevation: 4.2 })).toThrow('elevation');
  });
  it('shows precisely the selected floor, or all floors in building and exploded views', () => {
    expect(sceneFloors(building, 'first', 'floor').map(f => f.floor.id)).toEqual(['first']);
    expect(sceneFloors(building, 'first', 'building').map(f => f.elevation)).toEqual([0, 4.2, 8.4]);
    expect(sceneFloors(building, 'first', 'exploded').map(f => f.elevation)).toEqual([0, 4.2 + EXPLODE_GAP, 8.4 + 2 * EXPLODE_GAP]);
    expect(sceneFloors(building, 'first', 'building').filter(f => f.active)).toHaveLength(1);
  });
  it.each(building.floors)('round trips all coordinates on $name without changing local estimates', floor => {
    const positions = [...floor.anchors.map(a => a.position), ...floor.candidate_anchors.map(a => a.position), ...Object.values(floor.routes).flat().map(w => w.position), ...floor.walls.flatMap(w => [w.a, w.b])];
    const index = building.floors.indexOf(floor);
    for (const point of positions) {
      for (const mode of ['floor', 'building', 'exploded'] as const) {
        const elevation = displayElevation(floor, index, mode);
        expect(toScene(point, elevation)).toEqual([point[0], elevation, -point[1]]);
        expect(fromScene(toScene(point, elevation))).toEqual(point);
      }
    }
  });
  it('cuts aligned access cores out of floor slabs without negative geometry', () => {
    for (const floor of building.floors) {
      const rectangles = slabRectangles(floor);
      expect(rectangles.every(([x0, y0, x1, y1]) => x1 > x0 && y1 > y0)).toBe(true);
      const area = rectangles.reduce((sum, [x0, y0, x1, y1]) => sum + (x1 - x0) * (y1 - y0), 0);
      const openings = floor.access_cores.reduce((sum, core) => sum + core.size[0] * core.size[1], 0);
      expect(area).toBeCloseTo(floor.width * floor.height - openings);
    }
    expect(slabRectangles(floorsOf(legacy)[0])).toEqual([[0, 0, 30, 20]]);
  });
  it('rotates covariance ellipses in the backend plane before scene mapping', () => {
    const source = frame();
    const rotated = { ...source, tracking: { ...source.tracking, ellipse: { ...source.tracking.ellipse!, angle: Math.PI / 2 } } };
    const points = ellipsePoints(rotated, 8.56);
    expect(points).toHaveLength(81);
    expect(points[0][0]).toBeCloseTo(7);
    expect(points[0][1]).toBe(8.56);
    expect(points[0][2]).toBeCloseTo(-16);
    expect(ellipsePoints({ ...source, tracking: { ...source.tracking, position: null } })).toEqual([]);
  });
});

describe('view and layer controls', () => {
  it('selecting a floor enters detail mode and requests a fresh camera fit', () => {
    expect(viewReducer({ ...INITIAL_VIEW, mode: 'exploded' }, { type: 'floor' })).toEqual({ ...INITIAL_VIEW, reset: 1 });
  });
  it('plan view isolates the active floor and building views restore perspective', () => {
    const plan = viewReducer(INITIAL_VIEW, { type: 'angle', angle: 'plan' });
    expect(plan.mode).toBe('floor');
    expect(plan.angle).toBe('plan');
    expect(viewReducer(plan, { type: 'mode', mode: 'building' }).angle).toBe('perspective');
    expect(viewReducer(plan, { type: 'mode', mode: 'exploded' }).angle).toBe('perspective');
  });
  it('issues separate relative zoom commands so wheel interaction cannot saturate button state', () => {
    const first = viewReducer(INITIAL_VIEW, { type: 'zoom', amount: 1.2 });
    const second = viewReducer(first, { type: 'zoom', amount: 1.2 });
    expect(first.zoomAction).toBe(1);
    expect(second.zoomAction).toBe(2);
    expect(second.zoomFactor).toBe(1.2);
    expect(viewReducer(second, { type: 'reset' }).reset).toBe(1);
    expect(viewReducer(INITIAL_VIEW, { type: 'zoom', amount: 0 })).toBe(INITIAL_VIEW);
  });
  it('hiding truth removes its signal rays and enabling rays restores truth visibility', () => {
    const signals = toggleLayer(DEFAULT_LAYERS, 'signals', true);
    expect(signals.signals && signals.truth).toBe(true);
    expect(toggleLayer(signals, 'truth', false)).toMatchObject({ signals: false, truth: false });
    expect(toggleLayer(signals, 'walls', false)).toMatchObject({ walls: false, signals: true });
  });
  it('does not connect paths through withheld estimates or across floors', () => {
    const samples: Sample[] = [0, 1, 2, 3, 4, 5, 6].map(t => ({ t, baseline: 1, proposed: t === 2 ? null : 1, proposedPosition: t === 2 ? null : [t, 1], floor_id: t === 4 ? 'first' : 'ground' }));
    expect(trajectorySegments(samples, 'proposedPosition', 'ground')).toEqual([[[0, 1], [1, 1]], [[5, 1], [6, 1]]]);
  });
  it('draws multi-floor trails at their own elevation and breaks on a missing height', () => {
    const samples: Sample[] = [
      { t: 0, baseline: null, proposed: 1, truth: [1, 2], truthElevation: 1.2 },
      { t: 1, baseline: null, proposed: 1, truth: [2, 3], truthElevation: 3.3 },
      { t: 2, baseline: null, proposed: null, truth: [3, 4], truthElevation: null },
      { t: 3, baseline: null, proposed: 1, truth: [4, 5], truthElevation: 5.4 },
      { t: 4, baseline: null, proposed: 1, truth: [5, 6], truthElevation: 5.4 },
    ];
    expect(elevatedTrajectorySegments(samples, 'truth', 'truthElevation')).toEqual([
      [[1, 1.2, -2], [2, 3.3, -3]], [[4, 5.4, -5], [5, 5.4, -6]],
    ]);
  });
});

describe('truthful comparison windows', () => {
  it('keeps identical settings, replaces recomputation, and resets on any experiment identity change', () => {
    const initial = frame();
    const first = recordFrame([], null, initial);
    const advanced = frame({ timestamp: 1, frame_index: 1, revision: 1 });
    const second = recordFrame(first, initial, advanced);
    expect(second).toHaveLength(2);
    expect(recordFrame(second, advanced, advanced)).toHaveLength(2);
    expect(recordFrame(second, advanced, initial)).toHaveLength(1);
    for (const changed of [
      frame({ floor_id: 'first', floor_elevation: 4.2 }),
      frame({ session_id: 'new' }),
      frame({ settings: { ...initial.settings, noise_std: 0.9 } }),
      frame({ settings: { ...initial.settings, disabled_anchors: ['A1'] } }),
      frame({ settings: { ...initial.settings, walls_enabled: false } }),
    ]) expect(recordFrame(second, advanced, changed)).toHaveLength(1);
  });
  it('treats disabled anchor order as the same configuration', () => {
    const first = frame();
    expect(settingsKey({ ...first, settings: { ...first.settings, disabled_anchors: ['A2', 'A1'] } })).toBe(settingsKey({ ...first, settings: { ...first.settings, disabled_anchors: ['A1', 'A2'] } }));
  });
  it('computes errors, rates and coverage with independent correct denominators', () => {
    const samples: Sample[] = [
      { t: 0, baseline: 1, proposed: 1, baselineUpdated: true, proposedUpdated: true, covered95: true, baselineRuntime: 1, proposedRuntime: 2 },
      { t: 1, baseline: null, proposed: 3, baselineUpdated: false, proposedUpdated: false, covered95: false, baselineRuntime: 2, proposedRuntime: 4 },
      { t: 2, baseline: 5, proposed: null, baselineUpdated: true, proposedUpdated: false, covered95: null, baselineRuntime: 3, proposedRuntime: 6 },
    ];
    const metrics = windowMetrics(samples, 'proposed');
    expect(metrics.rmse).toBeCloseTo(Math.sqrt(5));
    expect(metrics.median).toBe(2);
    expect(metrics.p95).toBeCloseTo(2.9);
    expect(metrics.validRate).toBeCloseTo(2 / 3);
    expect(metrics.updateRate).toBeCloseTo(1 / 3);
    expect(metrics.latency).toBe(4);
    expect(metrics.coverage).toBe(0.5);
    expect(metrics.coverageCount).toBe(2);
    expect(windowMetrics(samples, 'baseline').coverage).toBeNull();
  });
  it('never invents metrics for absent samples or missing update/latency metadata', () => {
    const metrics = windowMetrics([], 'proposed');
    for (const key of ['rmse', 'median', 'p95', 'validRate', 'updateRate', 'latency', 'coverage'] as const) expect(metrics[key]).toBeNull();
    expect(windowMetrics([{ t: 0, baseline: null, proposed: null }], 'proposed')).toMatchObject({ validRate: 0, rmse: null, latency: null, updateRate: null, coverage: null });
  });
  it('caps the window at 600 actual samples', () => {
    const previous = frame({ timestamp: 600, frame_index: 600 });
    const history = Array.from({ length: 600 }, (_, index) => ({ t: index + 1, baseline: 1, proposed: 1 }));
    const next = recordFrame(history, previous, frame({ timestamp: 601, frame_index: 601 }));
    expect(next).toHaveLength(600);
    expect(next[0].t).toBe(2);
  });
});

describe('rendered control contracts', () => {
  it('renders backend-selected floors and configuration route choices', () => {
    const current = frame({ floor_id: 'first', floor_elevation: 4.2 });
    const markup = renderToStaticMarkup(<EnvironmentPanel environment={building} environments={[legacy, building]} floor={building.floors[1]} frame={current} busy={false} update={async () => true} selectEnvironment={vi.fn()} selectFloor={vi.fn()} selectMode={vi.fn()} layers={DEFAULT_LAYERS} setLayer={vi.fn()} resetLayers={vi.fn()} cutaway setCutaway={vi.fn()} />);
    expect(markup).toContain('aria-label="Select First floor" aria-pressed="true"');
    expect(markup).toContain('aria-label="Select Ground floor" aria-pressed="false"');
    expect(markup).toContain('aria-label="Select Second floor"');
    expect(markup).toContain('Evaluation service round');
    expect(markup).toContain('21 configured sites');
  });
  it('uses a newly declared route without hardcoded options', () => {
    const source = frame();
    const custom = { ...building.floors[0], routes: { inspection: building.floors[0].routes.asset_round }, route_names: { inspection: 'Custom inspection route' } };
    const markup = renderToStaticMarkup(<Controls floor={custom} frame={{ ...source, settings: { ...source.settings, route: 'inspection' } }} update={async () => true} />);
    expect(markup).toContain('value="inspection" selected=""');
    expect(markup).toContain('Custom inspection route');
    expect(markup).not.toContain('value="held_out"');
  });
  it('renders missing estimates, baseline coverage unavailability, and a selected known floor', () => {
    const source = frame({ floor_id: 'second', floor_elevation: 8.4 });
    const current = { ...source, tracking: { ...source.tracking, position: null, ellipse: null, updated: false, status: 'unavailable' }, evaluation: { ...source.evaluation, proposed_error: null, covered_95: null } };
    const markup = renderToStaticMarkup(<Diagnostics frame={current} floor={building.floors[2]} history={recordFrame([], null, current)} technical />);
    expect(markup).toContain('N/A');
    expect(markup).toContain('8.4');
    expect(markup).toContain('unavailable');
    expect(markup).toContain('0 available ellipses');
    expect(markup).toContain('0/1');
  });
  it('shows the multi-floor storey, its source and confidence without inventing a floor', () => {
    const confident = multifloorFrame();
    const markup = renderToStaticMarkup(<MultifloorStatus frame={confident} floors={building.floors} />);
    expect(markup).toContain('First floor');
    expect(markup).toContain('source-badge inferred');
    expect(markup).toContain('91.0%');
    expect(markup).toContain('5.35 m');
    expect(markup).toContain('Truth elevation');
    expect(markup).not.toContain('Floor ambiguous');

    const ambiguous = multifloorFrame({
      floor_id: null, floor_elevation: null, elevation_estimate: 3.3,
      floor_state: { floor_id: null, source: 'inferred', confidence: null, ambiguous: true },
      transition: { phase: 'traversing', transition_id: null, connection_id: null, from_floor_id: null, to_floor_id: null, progress: null },
    });
    const unknown = renderToStaticMarkup(<MultifloorStatus frame={ambiguous} floors={building.floors} />);
    expect(unknown).toContain('Floor ambiguous');
    expect(unknown).toContain('No storey is claimed');
    expect(unknown).toContain('Traversing · no route witness');
    expect(unknown).not.toContain('First floor');
  });
  it('labels a route-assisted traverse with its connector but a 2D frame with nothing', () => {
    expect(transitionLabel({ phase: 'entering', transition_id: 't', connection_id: 'west-stairs', from_floor_id: 'ground', to_floor_id: 'first', progress: 0.2 })).toBe('Entering connector · West stairs');
    const assisted = multifloorFrame({
      floor_id: null, floor_elevation: null, localization_mode: 'multifloor_route_assisted',
      floor_state: { floor_id: null, source: 'route_assisted', confidence: null, ambiguous: true },
      transition: { phase: 'exiting', transition_id: 'west-up', connection_id: 'west-stairs', from_floor_id: 'ground', to_floor_id: 'first', progress: 0.75 },
    });
    const markup = renderToStaticMarkup(<MultifloorStatus frame={assisted} floors={building.floors} />);
    expect(markup).toContain('route assisted');
    expect(markup).toContain('Exiting connector · West stairs');
    expect(markup).toContain('75.0% through the traverse');
    expect(renderToStaticMarkup(<MultifloorStatus frame={frame()} floors={building.floors} />)).toBe('');
  });
  it('reports the 3D pipeline honestly in diagnostics instead of faking a 2D baseline', () => {
    const current = multifloorFrame();
    const history = recordFrame(recordFrame([], null, { ...current, timestamp: 0 }), { ...current, timestamp: 0 }, { ...current, timestamp: 1, frame_index: 1 });
    const markup = renderToStaticMarkup(<Diagnostics frame={current} floor={building.floors[1]} history={history} technical />);
    expect(markup).toContain('3D PIPELINE');
    expect(markup).toContain('no 2D baseline exists');
    expect(markup).toContain('Device elevation · estimate');
    expect(markup).toContain('5.35 m');
    expect(markup).toContain('Truth elevation');
    expect(markup).not.toContain('Known floor elevation');
    expect(markup).not.toContain('Range observations');
    // A baseline that never ran is absent, not a 0 % result.
    const baselineColumn = [...markup.matchAll(/<th scope="row">[^<]+<\/th><td>([^<]*)<\/td>/g)].map(match => match[1]);
    expect(baselineColumn).toHaveLength(7);
    expect(baselineColumn.every(value => value === '—' || value === 'N/A')).toBe(true);
  });
});
