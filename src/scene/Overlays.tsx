import { useMemo } from 'react';
import * as THREE from 'three';
import { toScene } from '../lib';
import type { EnvironmentDefinition, Floor, Frame, Point, Sample } from '../types';
import { elevatedTrajectorySegments, ellipsePoints, floorsOf, isBuilding, overlayElevation, routePolyline, trajectorySegments, type SceneLayers, type ViewMode } from '../workspace';
import { Label, Polyline, Solids } from './Primitives';
import { solid } from './architectureGeometry';

const COLORS = { truth: '#344859', baseline: '#cd842e', proposed: '#087f83', ai: '#7264b8', fault: '#b75543', anchor: '#466fad' };

function Marker({ point, kind, elevation = 0.16 }: { point: Point; kind: 'truth' | 'baseline' | 'proposed' | 'ai'; elevation?: number }) {
  const color = COLORS[kind];
  return <group position={toScene(point, elevation)}>
    <mesh rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.48, 0.58, 28]} /><meshBasicMaterial color={color} side={THREE.DoubleSide} /></mesh>
    {kind === 'truth' ? <>
      <mesh position={[0, 0.35, 0]} castShadow><boxGeometry args={[0.48, 0.6, 0.78]} /><meshStandardMaterial color="#344859" /></mesh>
      <mesh position={[0, 0.66, 0]}><boxGeometry args={[0.58, 0.08, 0.88]} /><meshStandardMaterial color="#f9f8f1" /></mesh>
      <mesh position={[0, 0.85, -0.3]}><boxGeometry args={[0.52, 0.06, 0.1]} /><meshStandardMaterial color="#344859" /></mesh>
    </> : <mesh position={[0, kind === 'baseline' ? 0.62 : 0.82, 0]}>
      {kind === 'baseline' ? <octahedronGeometry args={[0.34]} /> : <sphereGeometry args={[0.3, 14, 10]} />}
      <meshBasicMaterial color={color} />
    </mesh>}
  </group>;
}

export function Anchors({ floor, frame, layers, offset = 0 }: { floor: Floor; frame: Frame | null; layers: SceneLayers; offset?: number }) {
  const states = useMemo(() => {
    // Multi-floor frames list every anchor in building order; the matching labels are at
    // the same indices, so an offset avoids confusing an ID repeated on another storey.
    const labels = frame ? frame.simulator_labels.slice(offset, offset + floor.anchors.length) : [];
    return floor.anchors.map(anchor => {
      const status = labels.find(label => label.anchor_id === anchor.id);
      const color = !frame ? '#879ca7' : status?.kind === 'disabled' ? '#a8b1b4' : !status?.available ? '#b27440' : COLORS.anchor;
      return { anchor, color };
    });
  }, [floor, frame, offset]);
  const devices = useMemo(() => states.flatMap(({ anchor, color }) => [
    solid(anchor.position, [0.05, 1.35, 0.05], '#839aa5'),
    solid(anchor.position, [0.38, 0.5, 0.28], '#f8faf9', 1.35),
    solid([anchor.position[0], anchor.position[1] - 0.15], [0.22, 0.14, 0.02], color, 1.57),
  ]), [states]);
  return <>
    {layers.anchors && <><Solids items={devices} />{states.map(({ anchor, color }) => <group key={anchor.id}>
      <mesh position={toScene(anchor.position, 0.08)} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.38, 0.46, 24]} /><meshBasicMaterial color={color} /></mesh>
      <Label text={anchor.id} position={toScene(anchor.position, 2.3)} color={color} scale={1.65} />
    </group>)}</>}
    {layers.candidates && floor.candidate_anchors.map(anchor => <group key={anchor.id}>
      <mesh position={toScene(anchor.position, 0.13)} rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.52, 0.57, 4]} /><meshBasicMaterial color="#946e45" /></mesh>
      <Label text={anchor.id} position={toScene([anchor.position[0] + 0.65, anchor.position[1]], 0.45)} color="#946e45" scale={1.5} />
    </group>)}
  </>;
}

export function ExperimentOverlays({ floor, frame, history, layers }: { floor: Floor; frame: Frame; history: Sample[]; layers: SceneLayers }) {
  const route = floor.routes[frame.settings.route];
  const planned = useMemo(() => route ? [...route, route[0]].map(w => toScene(w.position, 0.09)) : [], [route]);
  const ellipse = useMemo(() => ellipsePoints(frame), [frame]);
  const trails = useMemo(() => ({
    truth: trajectorySegments(history, 'truth', floor.id),
    baseline: trajectorySegments(history, 'basePosition', floor.id),
    proposed: trajectorySegments(history, 'proposedPosition', floor.id),
    ai: trajectorySegments(history, 'aiPosition', floor.id),
  }), [history, floor.id]);
  if (frame.floor_id !== floor.id) return null;
  return <>
    {layers.route && planned.length > 1 && <Polyline points={planned} color="#799eaa" opacity={0.75} dashed />}
    {layers.signals && layers.truth && frame.measurements.map(measurement => {
      const nlos = frame.simulator_labels.find(l => l.anchor_id === measurement.anchor_id)?.kind === 'nlos';
      return <Polyline key={measurement.anchor_id} points={[toScene(frame.evaluation.ground_truth, 0.75), toScene(measurement.anchor_position, 1.6)]} color={nlos ? COLORS.fault : COLORS.anchor} opacity={nlos ? 0.8 : 0.35} dashed={nlos} />;
    })}
    {(['truth', 'baseline', 'proposed', 'ai'] as const).map(kind => layers[kind] && trails[kind].map((points, index) => <Polyline key={kind + '-' + index} points={points.map(point => toScene(point, kind === 'truth' ? 0.14 : kind === 'ai' ? 0.23 : 0.2))} color={COLORS[kind]} dashed={kind === 'truth'} />))}
    {layers.uncertainty && ellipse.length > 0 && <Polyline points={ellipse} color={COLORS.proposed} opacity={0.85} dashed />}
    {layers.truth && <Marker point={frame.evaluation.ground_truth} kind="truth" />}
    {layers.baseline && frame.baseline.position && <Marker point={frame.baseline.position} kind="baseline" />}
    {layers.proposed && frame.tracking.position && <Marker point={frame.tracking.position} kind="proposed" />}
    {layers.ai && frame.ai_tracking?.position && <Marker point={frame.ai_tracking.position} kind="ai" />}
  </>;
}

/**
 * Root-level multi-floor overlays: the target is drawn at its estimated elevation, not
 * on a selected floor. Truth uses the simulator's own elevation, so the comparison is
 * vertical as well as horizontal; the 2D baseline does not exist here and is not drawn.
 */
export function MultifloorOverlays({ environment, frame, history, layers, mode }: { environment: EnvironmentDefinition; frame: Frame; history: Sample[]; layers: SceneLayers; mode: ViewMode }) {
  const floors = useMemo(() => floorsOf(environment), [environment]);
  const building = isBuilding(environment) ? environment : null;
  const route = building?.transition_routes?.find(item => item.id === frame.settings.route);
  // Only estimate-derived overlays follow a claimed storey; truth has no storey to consult and
  // always uses the nearest physical plane, so a marker never detaches from its own trail.
  const overlayFloorId = frame.floor_state?.floor_id ?? frame.floor_id;
  const planned = useMemo(() => floors.flatMap(floor => {
    const leg = route?.legs.find(item => item.floor_id === floor.id);
    if (!leg || !route) return [];
    const elevation = overlayElevation(floor.elevation, floors, mode, floor.id) + 0.09;
    return [routePolyline(leg.waypoints.map(waypoint => waypoint.position), route.loop).map(waypoint => toScene(waypoint, elevation))];
  }), [floors, route, mode]);
  const trails = useMemo(() => {
    const lifted = (storey: (sample: Sample) => string | null) =>
      (point: [number, number, number], sample: Sample): [number, number, number] =>
        [point[0], overlayElevation(point[1], floors, mode, storey(sample)), point[2]];
    return {
      truth: elevatedTrajectorySegments(history, 'truth', 'truthElevation', lifted(() => null)),
      // A recorded storey keeps the trail on its marker's group; unknown falls back to the plane.
      proposed: elevatedTrajectorySegments(history, 'proposedPosition', 'proposedElevation', lifted(sample => sample.floor_id ?? null)),
    };
  }, [history, floors, mode]);
  const estimate = frame.elevation_estimate;
  const truthElevation = frame.evaluation.ground_truth_elevation;
  // A withheld estimate has no height to draw at; never fall back to the ground plane.
  const ellipse = useMemo(() => estimate == null ? []
    : ellipsePoints(frame, overlayElevation(estimate, floors, mode, overlayFloorId) + 0.16),
    [frame, estimate, floors, mode, overlayFloorId]);
  // Measurements are the available labels in building order, so align them through the
  // label list rather than assuming every anchor produced a range.
  const signals = useMemo(() => {
    const floorAnchors = floors.flatMap(floor => floor.anchors.map(anchor => ({ anchor, floorId: floor.id, elevation: floor.elevation })));
    const available = frame.simulator_labels.flatMap((label, index) => label.available ? [index] : []);
    return frame.measurements.flatMap((measurement, index) => {
      const target = floorAnchors[available[index]];
      const label = frame.simulator_labels[available[index]];
      return target && label ? [{ measurement, anchor: target, nlos: label.kind === 'nlos' }] : [];
    });
  }, [floors, frame]);
  if (estimate == null && truthElevation == null) return null;
  return <>
    {layers.route && planned.map((points, index) => <Polyline key={'route-' + index} points={points} color="#799eaa" opacity={0.75} dashed />)}
    {layers.signals && layers.truth && truthElevation != null && signals.map(({ measurement, anchor, nlos }) => <Polyline key={measurement.anchor_id + '@' + anchor.floorId} points={[toScene(frame.evaluation.ground_truth, overlayElevation(truthElevation, floors, mode, null) + 0.35), toScene(anchor.anchor.position, overlayElevation(anchor.elevation, floors, mode, anchor.floorId) + 1.6)]} color={nlos ? COLORS.fault : COLORS.anchor} opacity={nlos ? 0.8 : 0.35} dashed={nlos} />)}
    {layers.truth && trails.truth.map((points, index) => <Polyline key={'truth-' + index} points={points} color={COLORS.truth} dashed />)}
    {layers.proposed && trails.proposed.map((points, index) => <Polyline key={'proposed-' + index} points={points} color={COLORS.proposed} />)}
    {layers.uncertainty && ellipse.length > 0 && <Polyline points={ellipse} color={COLORS.proposed} opacity={0.85} dashed />}
    {layers.truth && truthElevation != null && <Marker point={frame.evaluation.ground_truth} kind="truth" elevation={overlayElevation(truthElevation, floors, mode, null) + 0.16} />}
    {layers.proposed && estimate != null && frame.tracking.position && <Marker point={frame.tracking.position} kind="proposed" elevation={overlayElevation(estimate, floors, mode, overlayFloorId) + 0.16} />}
  </>;
}
