import { useMemo } from 'react';
import * as THREE from 'three';
import { toScene } from '../lib';
import type { Building } from '../types';
import { Label } from './Primitives';

export interface DisplayFloor { floor: { id: string; access_cores: { id: string; center: [number, number]; size: [number, number] }[] }; elevation: number }

const COLORS = { stairs: '#1fb5a6', elevator: '#f2a33a' } as const;

/** Translucent stair/lift shafts through every visible floor, with a ring on each floor they serve. */
export default function Connectors({ connections, visible }: { connections: Building['connections']; visible: DisplayFloor[] }) {
  const items = useMemo(() => connections.flatMap(connection => {
    const stops = connection.stops.flatMap(stop => {
      const display = visible.find(f => f.floor.id === stop.floor_id);
      const core = display?.floor.access_cores.find(c => c.id === stop.access_id);
      return display && core ? [{ core, elevation: display.elevation }] : [];
    });
    if (stops.length < 2) return [];
    const bottom = stops[0].elevation, top = stops[stops.length - 1].elevation + 3.2;
    return [{ id: connection.id, kind: connection.kind, stops, bottom, top, color: COLORS[connection.kind as keyof typeof COLORS] ?? '#1fb5a6' }];
  }), [connections, visible]);
  return <>{items.map(item => {
    const { core } = item.stops[0];
    return <group key={item.id}>
      <mesh position={toScene(core.center, (item.bottom + item.top) / 2)}>
        <boxGeometry args={[core.size[0] - 0.5, item.top - item.bottom, core.size[1] - 0.5]} />
        <meshStandardMaterial color={item.color} transparent opacity={0.22} depthWrite={false} emissive={item.color} emissiveIntensity={0.25} />
      </mesh>
      {item.stops.map(stop => <mesh key={stop.elevation} position={toScene(stop.core.center, stop.elevation + 0.12)} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[Math.min(stop.core.size[0], stop.core.size[1]) * 0.28, Math.min(stop.core.size[0], stop.core.size[1]) * 0.4, 32]} />
        <meshBasicMaterial color={item.color} side={THREE.DoubleSide} />
      </mesh>)}
      <Label text={item.kind === 'stairs' ? 'STAIRS' : 'LIFT'} position={toScene(core.center, item.top + 0.8)} scale={3} color={item.color} />
    </group>;
  })}</>;
}
