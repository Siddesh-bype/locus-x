import { describe, expect, it } from 'vitest';
import floor from '../configs/environments/hospital_floor_01.json';
import { appendSample, fromScene, samplesCsv, toScene } from './lib';
import type { Point } from './types';

describe('canonical coordinate system', () => {
  it('preserves every backend anchor and route point in the 3D scene', () => {
    const positions = [...floor.anchors.map(a => a.position), ...Object.values(floor.routes).flat().map(w => w.position)];
    for (const point of positions) {
      const scene = toScene(point as Point, 0.4);
      expect(scene).toEqual([point[0], 0.4, -point[1]]);
      expect(fromScene(scene)).toEqual(point);
    }
  });
  it('replaces recomputed frames, clears reset history, and preserves missing estimates', () => {
    const first = { t: 1, baseline: 0.2, proposed: 0.3 };
    const update = { t: 1, baseline: null, proposed: 0.4 };
    expect(appendSample([first], update)).toEqual([update]);
    expect(appendSample([first], { ...first, t: 0 })).toEqual([{ ...first, t: 0 }]);
    expect(samplesCsv([update])).toContain('1,,0.4');
  });
  it('exports actual floor identity, missing fixes and update metadata without corrupting CSV', () => {
    const csv = samplesCsv([{ t: 0.2, baseline: null, proposed: 1.2, ai: 0.9, environment_id: 'hospital, west', floor_id: 'first', proposedUpdated: false, aiUpdated: true, covered95: null, aiCovered95: false }]);
    expect(csv).toContain('environment_id,floor_id');
    expect(csv).toContain('0.2,,1.2,0.9,"hospital, west",first');
    expect(csv).toContain(',true,');
    expect(csv).not.toContain('null');
  });
});
