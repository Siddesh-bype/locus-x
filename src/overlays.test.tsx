import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import buildingJson from '../configs/environments/hospital_v3.json';
import type { Building, Frame, Sample } from './types';
import { DEFAULT_LAYERS } from './workspace';

// Root-level overlays render R3F primitives; stub the three.js objects while keeping the
// geometry props so the test can assert exactly where a polyline is drawn.
vi.mock('./scene/Primitives', () => ({
  Polyline: ({ points, color, opacity }: { points: [number, number, number][]; color: string; opacity?: number }) =>
    <polyline data-color={color} data-opacity={opacity} data-points={points.map(point => point.join(',')).join(';')} />,
  Label: () => null,
  Solids: () => null,
}));

import { MultifloorOverlays } from './scene/Overlays';

const building = buildingJson as unknown as Building;

function frame(overrides: Partial<Frame> = {}): Frame {
  const estimate = { position: [5, 14] as [number, number], status: 'valid', reason: 'accepted', residuals: {}, reliability: {}, covariance: [[1, 0, 0], [0, 1, 0], [0, 0, 1]], runtime_ms: 2, condition_number: 2 };
  return {
    session_id: 'overlay-test', environment_id: building.id, environment_version: '3.0.0', environment_sha256: 'test',
    floor_id: 'first', floor_elevation: 4.2, localization_mode: 'multifloor_inferred',
    revision: 0, timestamp: 0, frame_index: 0,
    settings: { scenario: 'noise', noise_std: 0.4, nlos_bias: 0, dropout: 0, disabled_anchors: [], walls_enabled: true, route: 'ascent_stairs', speed: 1.2, seed: 12 },
    anchors: [], measurements: [], simulator_labels: [],
    baseline: { ...estimate, position: null, status: 'not_applicable', reason: 'no 2D baseline' },
    robust: estimate,
    tracking: { position: [7, 14], velocity: [0, 0], covariance: [[1, 0], [0, 1]], ellipse: { major: 2, minor: 1, angle: 0, level: 0.95 }, status: 'tracking', reason: 'accepted', updated: true, outage_seconds: 0, runtime_ms: 1 },
    floor_state: { floor_id: 'first', source: 'inferred', confidence: 0.91, ambiguous: false },
    elevation_estimate: 5.35, floor_probabilities: { ground: 0.01, first: 0.98, second: 0.01 },
    evaluation: { ground_truth: [4, 14], baseline_error: null, proposed_error: 3, covered_95: true, has_nlos: false, has_dropout: false, ground_truth_elevation: 5.4 },
    events: [], ...overrides,
  };
}

function render(current: Frame, mode: 'floor' | 'building' | 'exploded' = 'building', history: Sample[] = []) {
  return renderToStaticMarkup(<MultifloorOverlays environment={building} frame={current} history={history} layers={DEFAULT_LAYERS} mode={mode} />);
}

function polylines(markup: string) {
  return [...markup.matchAll(/<polyline\b[^>]*><\/polyline>/g)].map(([tag]) => ({
    color: tag.match(/data-color="([^"]*)"/)?.[1],
    points: (tag.match(/data-points="([^"]*)"/)?.[1] ?? '').split(';').filter(Boolean)
      .map(point => point.split(',').map(Number) as [number, number, number]),
  }));
}

/** Marker groups are the only <group> elements with a scene position and no children wrappers. */
function markerPositions(markup: string) {
  return [...markup.matchAll(/<group position="([^"]+)">/g)].map(([, value]) => value.split(',').map(Number) as [number, number, number]);
}

describe('multi-floor overlay honesty', () => {
  it('draws no ellipse or estimate marker when the vertical estimate is withheld', () => {
    const current = frame({
      floor_id: null, floor_elevation: null, elevation_estimate: null,
      floor_state: { floor_id: null, source: 'inferred', confidence: null, ambiguous: true },
      transition: { phase: 'traversing', transition_id: null, connection_id: null, from_floor_id: null, to_floor_id: null, progress: null },
    });
    const markup = render(current);
    // Truth still renders; the proposed marker and the uncertainty ellipse do not exist.
    expect(markup).toContain('boxGeometry');
    expect(markup).not.toContain('sphereGeometry');
    expect(markup).not.toContain('#087f83');
    expect(polylines(markup).some(line => line.points.length === 81)).toBe(false);
    expect(markerPositions(markup)).toHaveLength(1);
  });

  it('lifts markers and trails by the exploded spacing of their floor group', () => {
    const history: Sample[] = [
      { t: 0, baseline: null, proposed: 0.2, truth: [4, 14], proposedPosition: [7, 14], truthElevation: 5.4, proposedElevation: 5.35 },
      { t: 1, baseline: null, proposed: 0.2, truth: [4, 14], proposedPosition: [7, 14], truthElevation: 5.4, proposedElevation: 5.35 },
    ];
    const markup = render(frame(), 'exploded', history);
    const [truthMarker, estimateMarker] = markerPositions(markup);
    // First floor is separated by EXPLODE_GAP (5.5 m); physical z is preserved on top of it.
    expect(truthMarker[1]).toBeCloseTo(5.4 + 5.5 + 0.16);
    expect(estimateMarker[1]).toBeCloseTo(5.35 + 5.5 + 0.16);
    const proposedTrail = polylines(markup).find(line => line.color === '#087f83' && line.points.length === 2);
    expect(proposedTrail).toBeDefined();
    expect(proposedTrail!.points.every(([, y]) => Math.abs(y - (5.35 + 5.5)) < 1e-9)).toBe(true);
    const firstFloorLeg = polylines(markup).find(line => line.color === '#799eaa' && Math.abs(line.points[0][1] - (4.2 + 5.5 + 0.09)) < 1e-9);
    expect(firstFloorLeg).toBeDefined();
  });

  it('does not close a leg of a route the configuration declares as one-way', () => {
    const markup = render(frame());
    const routeLines = polylines(markup).filter(line => line.color === '#799eaa');
    const groundLeg = routeLines.find(line => line.points.every(([, y]) => Math.abs(y - 0.09) < 1e-9));
    expect(groundLeg).toBeDefined();
    // ascent_stairs is not a loop: five waypoints stay five points, no return segment.
    expect(groundLeg!.points).toHaveLength(5);
    expect(groundLeg!.points[0]).not.toEqual(groundLeg!.points.at(-1));
  });
});
