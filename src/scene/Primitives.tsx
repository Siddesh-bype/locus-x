import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';

export type Vec3 = [number, number, number];
export interface Solid { position: Vec3; size: Vec3; color: string; rotation?: number }

/** Repeated architectural solids share one draw call and one geometry/material. */
export function Solids({ items, shadows = false }: { items: Solid[]; shadows?: boolean }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  useLayoutEffect(() => {
    if (!ref.current) return;
    const object = new THREE.Object3D(), color = new THREE.Color();
    items.forEach((item, index) => {
      object.position.set(...item.position); object.scale.set(...item.size);
      object.rotation.set(0, item.rotation ?? 0, 0); object.updateMatrix();
      ref.current!.setMatrixAt(index, object.matrix);
      ref.current!.setColorAt(index, color.set(item.color));
    });
    ref.current.instanceMatrix.needsUpdate = true;
    if (ref.current.instanceColor) ref.current.instanceColor.needsUpdate = true;
    ref.current.computeBoundingSphere();
  }, [items]);
  if (!items.length) return null;
  return <instancedMesh ref={ref} args={[undefined, undefined, items.length]} castShadow={shadows} receiveShadow>
    <boxGeometry args={[1, 1, 1]} /><meshStandardMaterial roughness={0.82} metalness={0.02} />
  </instancedMesh>;
}

export function Label({ text, position, color = '#3e5666', scale = 4, opacity = 1 }: { text: string; position: Vec3; color?: string; scale?: number; opacity?: number }) {
  const texture = useMemo(() => {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d')!;
    context.font = '500 48px "IBM Plex Sans", sans-serif';
    canvas.width = Math.ceil(context.measureText(text).width) + 32; canvas.height = 76;
    context.font = '500 48px "IBM Plex Sans", sans-serif';
    context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillStyle = color;
    context.fillText(text, canvas.width / 2, canvas.height / 2);
    const map = new THREE.CanvasTexture(canvas); map.colorSpace = THREE.SRGBColorSpace;
    return map;
  }, [text, color]);
  useEffect(() => () => texture.dispose(), [texture]);
  return <sprite position={position} scale={[scale, scale * texture.image.height / texture.image.width, 1]}>
    <spriteMaterial map={texture} transparent opacity={opacity} depthWrite={false} />
  </sprite>;
}

export function Polyline({ points, color, opacity = 1, dashed = false }: { points: Vec3[]; color: string; opacity?: number; dashed?: boolean }) {
  const object = useMemo(() => {
    const geometry = new THREE.BufferGeometry().setFromPoints(points.map(p => new THREE.Vector3(...p)));
    const material = dashed ? new THREE.LineDashedMaterial({ color, dashSize: 0.3, gapSize: 0.2, transparent: true, opacity }) : new THREE.LineBasicMaterial({ color, transparent: true, opacity });
    const line = new THREE.Line(geometry, material); line.computeLineDistances();
    return line;
  }, [points, color, opacity, dashed]);
  useEffect(() => () => { object.geometry.dispose(); object.material.dispose(); }, [object]);
  return <primitive object={object} />;
}
