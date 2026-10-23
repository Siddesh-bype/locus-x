import { Canvas } from '@react-three/fiber';
import { Component, useEffect, useMemo, type ReactNode } from 'react';
import * as THREE from 'three';
import { toScene } from './lib';
import { Architecture } from './scene/Architecture';
import CameraRig from './scene/CameraRig';
import { Anchors, ExperimentOverlays, MultifloorOverlays } from './scene/Overlays';
import { Label, Polyline } from './scene/Primitives';
import type { EnvironmentDefinition, Frame, Sample } from './types';
import { floorsOf, isBuilding, isMultifloor, sceneFloors, type SceneLayers, type ViewMode, type ViewState } from './workspace';

class SceneBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() { return this.state.failed ? <div className="scene-error" role="status"><strong>3D rendering is unavailable</strong><span>Enable WebGL to inspect the building. Experiment controls, measurements and results remain available.</span></div> : this.props.children; }
}

function GroundGrid({ width, length, elevation }: { width: number; length: number; elevation: number }) {
  const grid = useMemo(() => {
    const size = Math.max(width, length) + 24;
    const object = new THREE.GridHelper(size, Math.ceil(size / 2), '#d6dfdd', '#dce3e0');
    object.position.set(width / 2, elevation - 0.36, -length / 2);
    return object;
  }, [width, length, elevation]);
  useEffect(() => () => { grid.geometry.dispose(); (grid.material as THREE.Material).dispose(); }, [grid]);
  return <primitive object={grid} />;
}

export interface SceneProps {
  environment: EnvironmentDefinition; frame: Frame; history: Sample[];
  layers: SceneLayers; view: ViewState; cutaway: boolean;
  selectFloor: (id: string) => void;
}

function SceneContent({ environment, frame, history, layers, view, cutaway, selectFloor }: SceneProps) {
  const floors = useMemo(() => floorsOf(environment), [environment]);
  const multifloor = isMultifloor(frame);
  const activeFloorId = frame.floor_id ?? (isBuilding(environment) ? environment.default_floor_id : floors[0]?.id ?? '');
  // An ambiguous storey has nothing to isolate; the whole building keeps the target in view.
  const effectiveMode: ViewMode = multifloor && frame.floor_id === null ? 'building' : view.mode;
  const visible = useMemo(() => sceneFloors(environment, activeFloorId, effectiveMode), [environment, activeFloorId, effectiveMode]);
  const bounds = useMemo(() => ({
    width: environment.width, length: environment.height,
    bottom: Math.min(...visible.map(f => f.elevation)),
    top: Math.max(...visible.map(f => f.elevation + Math.max(3.2, ...f.floor.walls.map(w => w.height)))),
  }), [environment, visible]);
  const blockedKey = layers.signals && frame.settings.walls_enabled ? frame.simulator_labels.filter(l => l.kind === 'nlos').flatMap(l => l.intersected_walls).sort().join('|') : '';
  const blocked = useMemo(() => new Set(blockedKey.split('|')), [blockedKey]);
  const noBlocked = useMemo(() => new Set<string>(), []);
  return <>
    <color attach="background" args={['#eef1ee']} />
    <ambientLight intensity={1.15} />
    <hemisphereLight args={['#e8f3ff', '#bbbcae', 1.1]} />
    <directionalLight position={[5, 55, -8]} intensity={2.6} castShadow shadow-mapSize={[1024, 1024]} shadow-camera-left={-55} shadow-camera-right={55} shadow-camera-top={55} shadow-camera-bottom={-55} shadow-camera-far={140} shadow-bias={-0.001} />
    <CameraRig bounds={bounds} view={view} />
    <GroundGrid width={environment.width} length={environment.height} elevation={bounds.bottom} />
    {visible.map(({ floor, elevation, active }) => {
      const index = floors.findIndex(f => f.id === floor.id);
      const offset = multifloor ? floors.slice(0, index).reduce((count, item) => count + item.anchors.length, 0) : 0;
      const next = floors[index + 1];
      return <group key={floor.id} position={[0, elevation, 0]} onClick={event => { if (!multifloor && effectiveMode !== 'floor' && event.delta < 4) { event.stopPropagation(); selectFloor(floor.id); } }}>
        <Architecture floor={floor} layers={layers} mode={effectiveMode} active={active} cutaway={cutaway} rise={next ? next.elevation - floor.elevation : 0} blockedWalls={active ? blocked : noBlocked} propagation={frame.settings.walls_enabled} />
        <Anchors floor={floor} frame={multifloor || active ? frame : null} layers={layers} offset={offset} />
        {active && !multifloor && <ExperimentOverlays floor={floor} frame={frame} history={history} layers={layers} />}
      </group>;
    })}
    {multifloor && <MultifloorOverlays environment={environment} frame={frame} history={history} layers={layers} />}
    {effectiveMode === 'exploded' && layers.access && isBuilding(environment) && environment.connections.map(connection => {
      const points = connection.stops.flatMap(stop => {
        const display = visible.find(f => f.floor.id === stop.floor_id);
        const core = display?.floor.access_cores.find(c => c.id === stop.access_id);
        return display && core ? [toScene(core.center, display.elevation)] : [];
      });
      return <Polyline key={connection.id} points={points} color="#9badb5" opacity={0.7} dashed />;
    })}
    <Label text="N / +Y" position={toScene([environment.width + 2, environment.height + 2], bounds.bottom)} scale={2.8} color="#899c9b" />
    <Polyline points={[toScene([environment.width + 2, environment.height - 2], bounds.bottom), toScene([environment.width + 2, environment.height + 1], bounds.bottom)]} color="#9aabab" />
  </>;
}

export default function Scene(props: SceneProps) {
  return <SceneBoundary><Canvas orthographic frameloop="demand" shadows dpr={[1, 1.5]} camera={{ position: [64, 47, 43], zoom: 8, near: 0.1, far: 300 }} gl={{ antialias: true }} fallback={<div className="scene-error">WebGL is unavailable. Measurements and comparison results remain accessible.</div>}>
    <SceneContent {...props} />
  </Canvas></SceneBoundary>;
}
