import { memo, useMemo } from 'react';
import * as THREE from 'three';
import { toScene } from '../lib';
import type { Floor } from '../types';
import type { SceneLayers, ViewMode } from '../workspace';
import { Label, Polyline, Solids, type Solid } from './Primitives';
import { ROOM_COLORS, roomEquipment, slabRectangles, solid } from './architectureGeometry';

interface Props {
  floor: Floor; layers: SceneLayers; mode: ViewMode; cutaway: boolean; active: boolean;
  rise: number; blockedWalls: Set<string>; propagation: boolean;
}

export const Architecture = memo(function Architecture({ floor, layers, mode, cutaway, active, rise, blockedWalls, propagation }: Props) {
  // Each storey reads in its own colour: the floor accent is mixed into slabs and exterior walls.
  const tint = useMemo(() => (base: string, amount: number) => '#' + new THREE.Color(base).lerp(new THREE.Color(floor.color), amount).getHexString(), [floor.color]);
  const plates = useMemo(() => slabRectangles(floor).map(([x0, y0, x1, y1]) => solid([(x0 + x1) / 2, (y0 + y1) / 2], [x1 - x0, 0.28, y1 - y0], tint('#e4e8e5', 0.45), -0.3)), [floor, tint]);
  const rooms = useMemo(() => floor.rooms.map(room => solid(room.center, [room.size[0] - 0.18, 0.045, room.size[1] - 0.18], ROOM_COLORS[room.kind] ?? '#e2e7e3')), [floor]);
  const corridors = useMemo(() => floor.corridors.map(region => solid(region.center, [region.size[0], 0.04, region.size[1]], '#f6f3e9', 0.005)), [floor]);
  const equipment = useMemo(() => roomEquipment(floor), [floor]);
  const walls = useMemo(() => floor.walls.map(wall => {
    const height = cutaway ? Math.min(wall.height, wall.id.startsWith('ext') ? 0.62 : 1.05) : wall.height;
    const color = active && propagation && blockedWalls.has(wall.id) ? '#c4977e' : wall.id.startsWith('ext') ? tint('#b8c7ca', 0.55) : tint('#f4f5f0', 0.12);
    return solid([(wall.a[0] + wall.b[0]) / 2, (wall.a[1] + wall.b[1]) / 2], [Math.hypot(wall.b[0] - wall.a[0], wall.b[1] - wall.a[1]), height, 0.18], color, 0, Math.atan2(wall.b[1] - wall.a[1], wall.b[0] - wall.a[0]));
  }), [floor, cutaway, active, propagation, blockedWalls, tint]);

  // Structural concrete columns and architectural exterior cornices / sunshade chajjas
  const columns = useMemo(() => {
    const items: Solid[] = [];
    for (const wall of floor.walls) {
      if (!wall.id.startsWith('ext')) continue;
      const len = Math.hypot(wall.b[0] - wall.a[0], wall.b[1] - wall.a[1]);
      const angle = Math.atan2(wall.b[1] - wall.a[1], wall.b[0] - wall.a[0]);
      const count = Math.max(1, Math.floor(len / 6.0));
      const step = len / count;
      const colHeight = cutaway ? 0.95 : wall.height;
      for (let i = 0; i <= count; i++) {
        const t = i * step;
        const cx = wall.a[0] + (t / len) * (wall.b[0] - wall.a[0]);
        const cy = wall.a[1] + (t / len) * (wall.b[1] - wall.a[1]);
        // Structural concrete pilaster / column
        items.push(solid([cx, cy], [0.38, colHeight, 0.38], tint('#cbd5e1', 0.35), 0, angle));
      }
      // Terracotta accent band / chajja on the upper exterior wall facade
      if (!cutaway) {
        items.push(solid([(wall.a[0] + wall.b[0]) / 2, (wall.a[1] + wall.b[1]) / 2], [len + 0.12, 0.18, 0.42], '#9c4126', wall.height - 0.18, angle));
      }
    }
    return items;
  }, [floor.walls, cutaway, tint]);

  const doors = useMemo(() => floor.doors.flatMap(door => {
    const angle = Math.atan2(door.b[1] - door.a[1], door.b[0] - door.a[0]);
    const height = cutaway ? 0.85 : door.height;
    return [
      solid(door.a, [0.12, height, 0.26], '#6f8c96'), solid(door.b, [0.12, height, 0.26], '#6f8c96'),
      solid([(door.a[0] + door.b[0]) / 2, (door.a[1] + door.b[1]) / 2], [Math.hypot(door.b[0] - door.a[0], door.b[1] - door.a[1]), 0.025, 0.4], '#9db9b6', 0.05, angle),
      ...(!cutaway ? [solid([(door.a[0] + door.b[0]) / 2, (door.a[1] + door.b[1]) / 2], [Math.hypot(door.b[0] - door.a[0], door.b[1] - door.a[1]), 0.12, 0.26], '#6f8c96', height, angle)] : []),
    ];
  }), [floor, cutaway]);
  const cores = useMemo(() => floor.access_cores.flatMap(core => {
    const [x, y] = core.center, [w, h] = core.size;
    const items: Solid[] = [];
    if (core.kind === 'stairs') {
      items.push(solid([x, y - h / 2 + 0.65], [w - 0.35, 0.12, 1.3], '#c8d3d4'));
      if (rise > 0) {
        const count = 12, tread = (h - 1.5) / count, step = rise / (count * 2);
        for (let i = 0; i < count; i++) {
          items.push(solid([x - w / 4, y - h / 2 + 1.3 + (i + 0.5) * tread], [w / 2 - 0.4, 0.13, tread], '#d7dfdc', (i + 1) * step));
          items.push(solid([x + w / 4, y + h / 2 - 0.2 - (i + 0.5) * tread], [w / 2 - 0.4, 0.13, tread], '#d7dfdc', rise / 2 + (i + 1) * step));
        }
        items.push(solid([x, y + h / 2 - 0.5], [w - 0.35, 0.15, 1], '#bfcdd0', rise / 2));
      }
    } else {
      items.push(solid(core.center, [w - 0.35, 0.12, h - 0.35], '#8ca3ad'));
      items.push(solid([x, y + h / 2 - 0.25], [w - 0.3, cutaway ? 1.05 : 2.8, 0.2], '#a7bbc2'));
      items.push(solid([x - w / 2 + 0.2, y], [0.18, cutaway ? 1.05 : 2.8, h - 0.3], '#b1c3c8'));
      items.push(solid([x + w / 2 - 0.2, y], [0.18, cutaway ? 1.05 : 2.8, h - 0.3], '#b1c3c8'));
    }
    return items;
  }), [floor, rise, cutaway]);
  const perimeter = useMemo(() => [[0, 0], [floor.width, 0], [floor.width, floor.height], [0, floor.height], [0, 0]].map(p => toScene(p as [number, number], -0.13)), [floor]);
  return <group>
    <Solids items={plates} />
    <Polyline points={perimeter} color={floor.color} opacity={active ? 0.95 : 0.55} />
    {layers.rooms && <><Solids items={rooms} /><Solids items={equipment} shadows />
      {floor.rooms.filter(r => r.kind === 'imaging').map(room => <mesh key={room.name} position={toScene([room.center[0] - room.size[0] * 0.28, room.center[1] + 0.3], 1.25)} castShadow>
        <torusGeometry args={[0.92, 0.28, 10, 24]} /><meshStandardMaterial color="#f5f8f7" roughness={0.8} />
      </mesh>)}
    </>}
    {layers.corridors && <Solids items={corridors} />}
    {layers.walls && <><Solids items={walls} shadows /><Solids items={doors} /><Solids items={columns} shadows /></>}
    {layers.access && <Solids items={cores} shadows />}
    {layers.labels && <>
      {floor.rooms.map(room => <Label key={room.id ?? room.name} text={room.name} position={toScene([room.center[0], room.center[1] + (room.center[1] > floor.height / 2 ? 2.8 : -2.8)], 1.3)} scale={Math.min(room.size[0] * 0.88, room.name.length * 0.3)} color="#4c6671" />)}
      {layers.corridors && floor.corridors.map(region => <Label key={region.id} text={region.name.toUpperCase()} position={toScene([region.center[0], region.center[1] + (region.id === 'main-corridor' ? 0.7 : 0)], 0.1)} scale={Math.min(region.size[0] * 0.8, 6)} color="#9a9e90" />)}
      {layers.access && floor.access_cores.map(core => <Label key={core.id} text={core.kind === 'stairs' ? 'STAIR' : 'LIFT'} position={toScene([core.center[0], core.center[1]], 1.8)} scale={2} color="#587680" />)}
    </>}
    {mode !== 'floor' && <Label text={`${floor.short_name}  /  ${floor.name.toUpperCase()}`} position={toScene([floor.width / 2, -2.4], 0)} color={floor.color} scale={8} />}
  </group>;
});
