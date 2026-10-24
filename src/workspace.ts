import { appendSample, toScene } from './lib';
import type { Building, EnvironmentDefinition, Floor, FloorState, Frame, Point, Sample, TransitionState } from './types';

export type ViewMode = 'floor' | 'building' | 'exploded';
export type CameraAngle = 'perspective' | 'plan';
export type SceneLayer = 'rooms' | 'corridors' | 'walls' | 'labels' | 'anchors' | 'route' | 'access' | 'candidates' | 'truth' | 'baseline' | 'proposed' | 'uncertainty' | 'signals';
export type SceneLayers = Record<SceneLayer, boolean>;
export const DEFAULT_LAYERS: SceneLayers = {
  rooms: true, corridors: true, walls: true, labels: true, anchors: true, route: true,
  access: true, candidates: false, truth: true, baseline: true, proposed: true,
  uncertainty: true, signals: false,
};
export const LAYER_NAMES: Record<SceneLayer, string> = {
  rooms: 'Room zones & equipment', corridors: 'Corridors', walls: 'Walls & door openings',
  labels: 'Room labels', anchors: 'Installed anchors', route: 'Planned target route',
  access: 'Stairs & elevators', candidates: 'Candidate anchor sites', truth: 'Ground truth',
  baseline: 'Baseline trajectory', proposed: 'LOCUS-X trajectory', uncertainty: 'Nominal 95% ellipse',
  signals: 'Simulated signal paths',
};
export const EXPLODE_GAP = 5.5;

export function toggleLayer(layers: SceneLayers, key: SceneLayer, value: boolean): SceneLayers {
  return { ...layers, [key]: value, ...(key === 'truth' && !value ? { signals: false } : {}), ...(key === 'signals' && value ? { truth: true } : {}) };
}

export function isBuilding(environment: EnvironmentDefinition): environment is Building {
  return 'floors' in environment;
}

export function isMultifloor(frame: Frame): boolean {
  return frame.localization_mode !== '2d_on_known_floor';
}

/** Adapt only missing presentation metadata. Legacy geometry is returned unchanged. */
export function floorsOf(environment: EnvironmentDefinition): Floor[] {
  if (isBuilding(environment)) return environment.floors;
  return [{
    ...environment, short_name: '01', elevation: 0, color: '#318d87',
    corridors: [], doors: [], access_cores: [], movement_regions: [], candidate_anchors: [],
    anchor_layout_id: 'reference-v1', route_names: Object.fromEntries(Object.keys(environment.routes).map(id => [id, id === 'asset_round' ? 'Hospital asset round' : id === 'held_out' ? 'Held-out service round' : id])),
  }];
}

/** Resolve the reported storey. Multi-floor ambiguity has no floor; it is not a guess. */
export function floorForFrame(environment: EnvironmentDefinition, frame: Frame): Floor | null {
  if (frame.environment_id !== environment.id) throw new Error('Frame and environment do not match. Reconnect to reload the configuration.');
  if (frame.floor_id === null) {
    if (!isMultifloor(frame)) throw new Error('Frame floor is missing. Reconnect to reload the configuration.');
    if (frame.floor_elevation !== null) throw new Error('Ambiguous frame carries an elevation. Reconnect to reload the configuration.');
    return null;
  }
  const floor = floorsOf(environment).find(f => f.id === frame.floor_id);
  if (!floor || floor.elevation !== frame.floor_elevation) throw new Error('Frame floor does not match the loaded building. Reconnect to reload the configuration.');
  return floor;
}

export function displayElevation(floor: Floor, index: number, mode: ViewMode): number {
  return floor.elevation + (mode === 'exploded' ? index * EXPLODE_GAP : 0);
}

/**
 * Rendered height for a root-level overlay. Floor groups carry the exploded spacing,
 * so an overlay must be shifted by the group it belongs to; when the storey is unknown
 * the nearest physical plane is the best available group.
 */
export function overlayElevation(physical: number, floors: Floor[], mode: ViewMode, floorId: string | null): number {
  if (mode !== 'exploded' || !floors.length) return physical;
  let index = floorId == null ? -1 : floors.findIndex(floor => floor.id === floorId);
  if (index < 0) index = floors.reduce((best, floor, candidate) =>
    Math.abs(floor.elevation - physical) < Math.abs(floors[best].elevation - physical) ? candidate : best, 0);
  return physical + index * EXPLODE_GAP;
}

/** Configured routes are not all loops; only close the polyline when the route says so. */
export function routePolyline(waypoints: Point[], loop: boolean): Point[] {
  return loop && waypoints.length > 0 ? [...waypoints, waypoints[0]] : [...waypoints];
}

export function humanizeId(id: string): string {
  const words = id.replace(/[-_]+/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const SOURCE_LABELS: Record<FloorState['source'], string> = {
  configured: 'configured', route_assisted: 'route assisted', inferred: 'inferred',
};
export function sourceLabel(state: FloorState): string { return SOURCE_LABELS[state.source]; }

const PHASE_LABELS: Record<TransitionState['phase'], string> = {
  entering: 'Entering connector', traversing: 'Traversing', exiting: 'Exiting connector', reacquiring: 'Reacquiring storey',
};
/** What the tracker knows, and what it does not: an inferred traverse has no route witness. */
export function transitionLabel(transition: TransitionState): string {
  const phase = PHASE_LABELS[transition.phase];
  return transition.connection_id ? phase + ' · ' + humanizeId(transition.connection_id) : phase + ' · no route witness';
}

export function sceneFloors(environment: EnvironmentDefinition, activeFloorId: string | null, mode: ViewMode) {
  return floorsOf(environment).map((floor, index) => ({ floor, elevation: displayElevation(floor, index, mode), active: floor.id === activeFloorId }))
    .filter(({ active }) => mode !== 'floor' || active);
}

export interface ViewState { mode: ViewMode; angle: CameraAngle; reset: number; zoomAction: number; zoomFactor: number }
export type ViewAction = { type: 'mode'; mode: ViewMode } | { type: 'angle'; angle: CameraAngle } | { type: 'floor' } | { type: 'reset' } | { type: 'zoom'; amount: number };
export const INITIAL_VIEW: ViewState = { mode: 'floor', angle: 'perspective', reset: 0, zoomAction: 0, zoomFactor: 1 };
export function viewReducer(state: ViewState, action: ViewAction): ViewState {
  switch (action.type) {
    case 'mode': return { ...state, mode: action.mode, angle: action.mode === 'floor' ? state.angle : 'perspective', reset: state.reset + 1 };
    case 'angle': return { ...state, angle: action.angle, mode: action.angle === 'plan' ? 'floor' : state.mode, reset: state.reset + 1 };
    case 'floor': return { ...state, mode: 'floor', reset: state.reset + 1 };
    case 'reset': return { ...state, reset: state.reset + 1 };
    case 'zoom': return Number.isFinite(action.amount) && action.amount > 0 ? { ...state, zoomAction: state.zoomAction + 1, zoomFactor: action.amount } : state;
  }
}

export function settingsKey(frame: Frame): string {
  const s = frame.settings;
  return JSON.stringify([frame.session_id, frame.environment_id, frame.localization_mode, frame.floor_id, s.scenario,
    s.noise_std, s.nlos_bias, s.dropout, [...s.disabled_anchors].sort(), [...(s.disabled_anchor_keys ?? [])].sort(), s.walls_enabled,
    s.route, s.speed, s.seed]);
}

export function recordFrame(history: Sample[], previous: Frame | null, frame: Frame): Sample[] {
  const sample: Sample = {
    t: frame.timestamp, baseline: frame.evaluation.baseline_error, proposed: frame.evaluation.proposed_error,
    truth: frame.evaluation.ground_truth, basePosition: frame.baseline.position, proposedPosition: frame.tracking.position,
    truthElevation: frame.evaluation.ground_truth_elevation ?? null, proposedElevation: frame.elevation_estimate ?? null,
    environment_id: frame.environment_id, floor_id: frame.floor_id, frame_index: frame.frame_index,
    baselineUpdated: frame.baseline.position !== null, proposedUpdated: frame.tracking.updated,
    baselineRuntime: frame.baseline.runtime_ms, proposedRuntime: frame.robust.runtime_ms + frame.tracking.runtime_ms,
    covered95: frame.evaluation.covered_95,
  };
  return appendSample(previous && settingsKey(previous) === settingsKey(frame) ? history : [], sample);
}

function quantile(sorted: number[], fraction: number): number | null {
  if (!sorted.length) return null;
  const index = (sorted.length - 1) * fraction, lower = Math.floor(index), weight = index - lower;
  return sorted[lower] * (1 - weight) + sorted[Math.ceil(index)] * weight;
}

export function windowMetrics(samples: Sample[], method: 'baseline' | 'proposed') {
  const errors = samples.flatMap(sample => sample[method] == null ? [] : [sample[method]!]).sort((a, b) => a - b);
  const runtimes = samples.flatMap(s => s[`${method}Runtime`] == null ? [] : [s[`${method}Runtime`]!]);
  const updates = samples.flatMap(s => s[`${method}Updated`] == null ? [] : [s[`${method}Updated`]!]);
  const coverage = method === 'proposed' ? samples.flatMap(s => s.covered95 == null ? [] : [s.covered95]) : [];
  return {
    count: samples.length, valid: errors.length,
    rmse: errors.length ? Math.sqrt(errors.reduce((sum, error) => sum + error ** 2, 0) / errors.length) : null,
    median: quantile(errors, 0.5), p95: quantile(errors, 0.95),
    validRate: samples.length ? errors.length / samples.length : null,
    updateRate: updates.length ? updates.filter(Boolean).length / updates.length : null,
    latency: runtimes.length ? runtimes.reduce((sum, value) => sum + value, 0) / runtimes.length : null,
    coverage: coverage.length ? coverage.filter(Boolean).length / coverage.length : null,
    coverageCount: coverage.length,
  };
}

/** Never connect across a missing estimate or another floor. */
export function trajectorySegments(samples: Sample[], key: 'truth' | 'basePosition' | 'proposedPosition', floorId: string) {
  const segments: Point[][] = [[]];
  for (const sample of samples) {
    const position = sample.floor_id === floorId ? sample[key] : undefined;
    if (position) segments[segments.length - 1].push(position);
    else if (segments[segments.length - 1].length) segments.push([]);
  }
  return segments.filter(segment => segment.length > 1);
}

/**
 * Multi-floor trails carry their own elevation; a missing height breaks the line, not a storey.
 * `lift` receives each scene point with the sample it came from, so a trail can apply the
 * exploded group of a storey without losing per-sample identity along the segment.
 */
export function elevatedTrajectorySegments(
  samples: Sample[], key: 'truth' | 'proposedPosition', elevationKey: 'truthElevation' | 'proposedElevation',
  lift: (point: [number, number, number], sample: Sample) => [number, number, number] = point => point,
): [number, number, number][][] {
  const segments: [number, number, number][][] = [[]];
  for (const sample of samples) {
    const position = sample[key], elevation = sample[elevationKey];
    if (position && elevation != null) segments[segments.length - 1].push(lift(toScene(position, elevation), sample));
    else if (segments[segments.length - 1].length) segments.push([]);
  }
  return segments.filter(segment => segment.length > 1);
}

export function ellipsePoints(frame: Frame, elevation = 0.16): [number, number, number][] {
  const ellipse = frame.tracking.ellipse, center = frame.tracking.position;
  if (!ellipse || !center) return [];
  return Array.from({ length: 81 }, (_, index) => {
    const theta = index / 80 * Math.PI * 2;
    const x = ellipse.major * Math.cos(theta), y = ellipse.minor * Math.sin(theta);
    return toScene([center[0] + x * Math.cos(ellipse.angle) - y * Math.sin(ellipse.angle), center[1] + x * Math.sin(ellipse.angle) + y * Math.cos(ellipse.angle)], elevation);
  });
}
