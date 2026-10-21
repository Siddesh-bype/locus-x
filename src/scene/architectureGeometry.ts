import { toScene } from '../lib';
import type { Floor, Point } from '../types';
import type { Solid } from './Primitives';

export function solid(at: Point, size: [number, number, number], color: string, base = 0, rotation = 0): Solid {
  return { position: toScene(at, base + size[1] / 2), size, color, rotation };
}

type Rect = [number, number, number, number];
/** Cut actual openings for access cores out of a floor plate. */
export function slabRectangles(floor: Floor): Rect[] {
  let rectangles: Rect[] = [[0, 0, floor.width, floor.height]];
  for (const core of floor.access_cores) {
    const [x, y] = core.center, [w, h] = core.size;
    const cut: Rect = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
    rectangles = rectangles.flatMap(rect => {
      const [x0, y0, x1, y1] = rect;
      const left = Math.max(x0, cut[0]), bottom = Math.max(y0, cut[1]);
      const right = Math.min(x1, cut[2]), top = Math.min(y1, cut[3]);
      if (right <= left || top <= bottom) return [rect];
      return [[x0, y0, left, y1], [right, y0, x1, y1], [left, y0, right, bottom], [left, top, right, y1]]
        .filter(([a, b, c, d]) => c > a && d > b) as Rect[];
    });
  }
  return rectangles;
}

export const ROOM_COLORS: Record<string, string> = {
  reception: '#d8e4df', triage: '#eadfd5', emergency: '#e8d9d0', consultation: '#dce6e7',
  imaging: '#d7e2ed', laboratory: '#dbe3e6', pharmacy: '#dee6d8', support: '#e5e4df',
  storage: '#e5e4df', ward: '#dce5ec', patient: '#e1e9ed', isolation: '#e9e0d3',
  station: '#d6e7e2', treatment: '#dce5e5', icu: '#d8e5e3', theatre: '#d7e4e7',
  recovery: '#dce8e5', staff: '#e9e4dc',
};

export function roomEquipment(floor: Floor): Solid[] {
  const items: Solid[] = [];
  const add = (at: Point, size: [number, number, number], color: string, base = 0) => items.push(solid(at, size, color, base));
  const bed = (x: number, y: number, monitor: boolean) => {
    add([x, y], [1.3, 0.42, 2.2], '#a9b7bd', 0.15);
    add([x, y], [1.22, 0.18, 2.08], '#f8faf9', 0.57);
    add([x, y - 0.25], [1.24, 0.07, 1.35], '#9bbdbf', 0.75);
    add([x, y + 0.7], [1.03, 0.12, 0.44], '#ffffff', 0.75);
    add([x, y + 1.04], [1.35, 0.7, 0.08], '#cad5d5', 0.15);
    if (monitor) {
      add([x + 0.95, y + 0.7], [0.48, 0.88, 0.5], '#c2ced0');
      add([x + 0.95, y + 0.7], [0.45, 0.35, 0.13], '#536e7c', 0.9);
    }
  };
  for (const room of floor.rooms) {
    const [x, y] = room.center, [w, h] = room.size;
    const sideX = x - w * 0.28;
    const north = y > floor.height / 2;
    const backY = y + (north ? 1 : -1) * (h / 2 - 1.1);
    if (['ward', 'icu', 'recovery', 'emergency'].includes(room.kind)) {
      const rowOffset = Math.min(2.4, h / 2 - 1.35);
      for (const offsetX of [-w * 0.27, w * 0.27]) for (const offsetY of [-rowOffset, rowOffset]) bed(x + offsetX, y + offsetY, room.kind === 'icu');
    } else if (['patient', 'isolation', 'triage', 'treatment'].includes(room.kind)) {
      bed(sideX, y, room.kind === 'isolation');
      add([x + w * 0.3, backY], [1.1, 1.15, 0.65], '#c1cdca');
    } else if (room.kind === 'theatre') {
      add([sideX, y], [1.0, 0.75, 2.2], '#b3c4c9');
      add([sideX, y], [1.05, 0.15, 2.3], '#92b4b8', 0.75);
      add([x + w * 0.3, y + 1], [0.85, 1.1, 0.7], '#c8d5d7');
      add([sideX, y + 2], [0.08, 2, 0.08], '#839ca5');
      add([sideX, y + 1.7], [1.1, 0.12, 1.1], '#f9fafa', 2);
    } else if (room.kind === 'reception' || room.kind === 'station') {
      add([x, backY], [Math.min(6, w - 2), 0.9, 1.1], '#b3c4bb');
      add([x, backY], [Math.min(6, w - 2) + 0.12, 0.09, 1.25], '#f4f5ed', 0.9);
      for (const side of [-1, 1]) {
        add([x + side * w * 0.34, y], [0.85, 0.45, 2.5], '#98afb4');
        add([x + side * w * 0.34, y], [0.15, 0.55, 2.5], '#a8bec2', 0.45);
      }
    } else if (room.kind === 'consultation' || room.kind === 'staff') {
      add([sideX, backY], [Math.min(2.5, w * 0.42), 0.8, 1.1], '#d3d4c9');
      add([sideX, backY + (north ? -1 : 1) * 1.1], [0.55, 0.5, 0.55], '#889fae');
      if (room.kind === 'consultation') bed(x + w * 0.27, y, false);
    } else if (room.kind === 'imaging') {
      add([sideX, y - 0.8], [1.15, 0.7, 3.2], '#b5c6ce');
      add([sideX, y - 0.8], [1.08, 0.12, 3.2], '#fafbf9', 0.7);
      add([x + w * 0.28, backY], [2.1, 0.9, 1.2], '#bdcdd1');
    } else {
      for (const side of [-1, 1]) {
        add([x + side * w * 0.3, y], [Math.min(1.1, w * 0.2), 1.1, h * 0.56], '#becac5');
        add([x + side * w * 0.3, y], [Math.min(1.18, w * 0.22), 0.08, h * 0.56 + 0.08], '#edf0e8', 1.1);
      }
    }
  }
  return items;
}
