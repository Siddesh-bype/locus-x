import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import buildingJson from '../configs/environments/hospital_v3.json';
import legacyJson from '../configs/environments/hospital_floor_01.json';
import Controls from './Controls';
import Diagnostics from './Diagnostics';
import EnvironmentPanel from './EnvironmentPanel';
import { fromScene, toScene } from './lib';
import { slabRectangles } from './scene/architectureGeometry';
import type { Building, Environment, Frame, Sample } from './types';
import {
  DEFAULT_LAYERS, EXPLODE_GAP, INITIAL_VIEW, displayElevation, ellipsePoints,
  floorForFrame, floorsOf, recordFrame, sceneFloors, settingsKey, toggleLayer,
  trajectorySegments, viewReducer, windowMetrics,
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
    const markup = renderToStaticMarkup(<EnvironmentPanel environment={building} environments={[legacy, building]} floor={building.floors[1]} frame={current} busy={false} update={async () => true} selectEnvironment={vi.fn()} selectFloor={vi.fn()} layers={DEFAULT_LAYERS} setLayer={vi.fn()} resetLayers={vi.fn()} cutaway setCutaway={vi.fn()} />);
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
});
