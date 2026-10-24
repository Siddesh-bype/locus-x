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
  reception: '#8fdad0', triage: '#eadfd5', emergency: '#e8d9d0', consultation: '#dce6e7',
  imaging: '#d7e2ed', laboratory: '#dbe3e6', pharmacy: '#dee6d8', support: '#e5e4df',
  storage: '#e5e4df', ward: '#dce5ec', patient: '#e1e9ed', isolation: '#e9e0d3',
  station: '#d6e7e2', treatment: '#dce5e5', icu: '#d8e5e3', theatre: '#d7e4e7',
  recovery: '#dce8e5', staff: '#f0c9a0',
  classroom: '#9ec9f2', lab: '#9fe0b6', office: '#f5d38a', library: '#c3a9ee', seminar: '#f4a99a', canteen: '#f8be85',
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
    } else if (room.kind === 'classroom') {
      // Academic Classroom: Raised teacher's dais, blackboard/whiteboard, lecture podium & student desks
      const daisWidth = Math.min(6.0, w - 2.0);
      add([x, backY], [daisWidth, 0.18, 1.6], '#8d6e63'); // Wooden dais platform
      add([x, backY + (north ? 0.72 : -0.72)], [daisWidth * 0.85, 1.25, 0.08], '#1b4332', 1.0); // Green blackboard
      add([x, backY + (north ? 0.76 : -0.76)], [daisWidth * 0.88, 0.06, 0.12], '#d7ccc8', 0.95); // Chalk tray
      add([x - 1.5, backY], [0.85, 0.9, 0.6], '#a1887f', 0.18); // Teacher podium desk

      // Student bench desks with backrests
      const rows = Math.max(1, Math.floor((h - 3.4) / 2.0));
      const cols = Math.max(2, Math.floor((w - 2.4) / 2.8));
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const dx = x + (c - (cols - 1) / 2) * 2.8;
          const dy = y + (r - (rows - 1) / 2) * 2.0 + (north ? -0.5 : 0.5);
          add([dx, dy], [2.0, 0.72, 0.55], '#3b82c4'); // Desk frame
          add([dx, dy], [2.08, 0.06, 0.6], '#93c5fd', 0.72); // Writing tabletop
          add([dx, dy + (north ? 0.55 : -0.55)], [2.0, 0.42, 0.32], '#1e40af'); // Bench seat
          add([dx, dy + (north ? 0.75 : -0.75)], [2.0, 0.45, 0.08], '#1d4ed8', 0.42); // Bench backrest
        }
      }
    } else if (room.kind === 'lab') {
      // Engineering & Computer Labs: Workbenches with PC desktop monitors, towers, or electronics test equipment
      const isElectronics = Boolean(room.id?.includes('203') || room.id?.includes('206')) || room.name.toLowerCase().includes('embedded') || room.name.toLowerCase().includes('electronics');
      const rows = Math.max(1, Math.floor((h - 3.0) / 2.4));
      const cols = Math.max(2, Math.floor((w - 2.0) / 2.6));

      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const dx = x + (c - (cols - 1) / 2) * 2.6;
          const dy = y + (r - (rows - 1) / 2) * 2.2 + (north ? -0.4 : 0.4);
          // Lab heavy bench
          add([dx, dy], [2.2, 0.78, 0.85], '#15803d');
          add([dx, dy], [2.26, 0.06, 0.9], '#86efac', 0.78);

          if (isElectronics) {
            // Oscilloscope & multimeter test rigs
            add([dx - 0.5, dy], [0.55, 0.35, 0.35], '#0284c7', 0.84);
            add([dx + 0.5, dy], [0.45, 0.3, 0.3], '#f59e0b', 0.84);
          } else {
            // Dual PC desktop workstations (monitor + CPU)
            for (const offset of [-0.55, 0.55]) {
              add([dx + offset, dy], [0.5, 0.38, 0.08], '#0f172a', 0.84); // Monitor frame
              add([dx + offset, dy + 0.01], [0.46, 0.32, 0.02], '#e0f2fe', 0.87); // Glowing display
              add([dx + offset, dy - 0.22], [0.38, 0.02, 0.16], '#334155', 0.84); // Keyboard
              add([dx + offset, dy], [0.22, 0.45, 0.45], '#1e293b', 0.1); // CPU tower
            }
          }
        }
      }
      add([x, backY], [Math.min(4.5, w - 2.0), 1.1, 0.35], '#14532d'); // Instructor bench
    } else if (room.kind === 'seminar') {
      // Seminar Hall: Presentation dais, large screen, cushioned audience chairs
      add([x, backY], [Math.min(7.0, w - 2.0), 0.3, 2.2], '#7f1d1d'); // Stage platform
      add([x, backY + (north ? 0.9 : -0.9)], [Math.min(5.5, w - 2.0), 1.8, 0.08], '#f8fafc', 0.9); // Projector screen
      add([x - 2.0, backY], [0.75, 1.1, 0.55], '#b91c1c', 0.3); // Lectern podium

      const rows = Math.max(1, Math.floor((h - 4.0) / 1.8));
      const cols = Math.max(3, Math.floor((w - 2.4) / 1.8));
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const dx = x + (c - (cols - 1) / 2) * 1.8;
          const dy = y + (r - (rows - 1) / 2) * 1.8 + (north ? -0.8 : 0.8);
          add([dx, dy], [0.65, 0.45, 0.65], '#991b1b'); // Cushioned seat
          add([dx, dy + (north ? 0.35 : -0.35)], [0.65, 0.5, 0.1], '#7f1d1d', 0.45); // Backrest
        }
      }
    } else if (room.kind === 'library') {
      // Lohiya Central Library: Double-sided book stacks, reading study tables & lamps
      const aisles = Math.max(2, Math.floor((w - 4.0) / 2.8));
      for (let c = 0; c < aisles; c++) {
        const ax = x - w / 2 + 2.4 + c * 2.8;
        // Bookcase frame
        add([ax, y], [0.75, 2.2, h * 0.72], '#581c87');
        // Colored book spines on shelves
        for (const by of [-h * 0.25, 0, h * 0.25]) {
          add([ax, y + by], [0.78, 0.28, 1.4], by < 0 ? '#3b82f6' : by === 0 ? '#ef4444' : '#10b981', 0.4);
          add([ax, y + by], [0.78, 0.28, 1.4], by < 0 ? '#f59e0b' : '#8b5cf6', 1.2);
        }
      }
      // Study tables on the side
      add([x + w * 0.35, y], [1.4, 0.75, Math.min(8.0, h - 2.0)], '#a855f7');
      add([x + w * 0.35, y], [1.46, 0.06, Math.min(8.0, h - 2.0) + 0.1], '#f3e8ff', 0.75);
    } else if (room.kind === 'canteen') {
      // Canteen: Dining tables with stools & food service counter
      const cols = Math.max(2, Math.floor((w - 3.0) / 3.2));
      for (let c = 0; c < cols; c++) {
        for (const dy of [-h * 0.22, h * 0.22]) {
          const cx = x - w / 2 + 2.4 + c * 3.2;
          add([cx, y + dy], [2.2, 0.75, 1.1], '#ea580c');
          add([cx, y + dy], [2.28, 0.06, 1.18], '#fed7aa', 0.75);
          // Stools around table
          for (const sx of [-0.75, 0.75]) {
            for (const sy of [-0.8, 0.8]) {
              add([cx + sx, y + dy + sy], [0.38, 0.45, 0.38], '#c2410c');
            }
          }
        }
      }
      // Service Counter
      add([x, backY], [Math.min(8.0, w - 2.0), 0.95, 1.2], '#9a3412');
      add([x, backY], [Math.min(8.0, w - 2.0) + 0.15, 0.08, 1.3], '#ffedd5', 0.95);
    } else if (room.kind === 'office') {
      // Director / HOD Office: Executive mahogany L-desk, leather chairs & credenza
      add([sideX, backY], [2.8, 0.78, 1.2], '#78350f'); // Main desk
      add([sideX + 1.0, backY + (north ? -0.8 : 0.8)], [1.1, 0.74, 1.4], '#92400e'); // L-extension
      add([sideX, backY + (north ? 0.75 : -0.75)], [0.65, 1.1, 0.65], '#1c1917'); // Executive high-back chair
      // Visitor chairs
      add([sideX - 0.75, backY + (north ? -1.0 : 1.0)], [0.55, 0.8, 0.55], '#451a03');
      add([sideX + 0.75, backY + (north ? -1.0 : 1.0)], [0.55, 0.8, 0.55], '#451a03');
      // Credenza / trophy display shelf
      add([x + w * 0.3, backY], [2.2, 1.4, 0.5], '#b45309');
    } else if (room.kind === 'support') {
      // Server Room / Infrastructure: 19-inch equipment racks with status lights
      const rackCols = Math.max(1, Math.floor((w - 2.0) / 1.4));
      for (let c = 0; c < rackCols; c++) {
        const rx = x - w / 2 + 1.5 + c * 1.4;
        add([rx, y], [0.85, 2.2, 0.95], '#0f172a');
        add([rx, y + 0.48], [0.75, 0.08, 0.04], '#10b981', 1.8); // Green status LED bar
        add([rx, y + 0.48], [0.75, 0.08, 0.04], '#38bdf8', 1.2); // Cyan status LED bar
      }
    } else {
      for (const side of [-1, 1]) {
        add([x + side * w * 0.3, y], [Math.min(1.1, w * 0.2), 1.1, h * 0.56], '#becac5');
        add([x + side * w * 0.3, y], [Math.min(1.18, w * 0.22), 0.08, h * 0.56 + 0.08], '#edf0e8', 1.1);
      }
    }
  }
  return items;
}
