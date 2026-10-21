import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { ViewState } from '../workspace';

interface Bounds { width: number; length: number; bottom: number; top: number }
interface Target { position: THREE.Vector3; center: THREE.Vector3; zoom: number }

export default function CameraRig({ bounds, view }: { bounds: Bounds; view: ViewState }) {
  const { camera, gl, size, invalidate } = useThree();
  const controls = useRef<OrbitControls | null>(null);
  const target = useRef<Target | null>(null);
  const appliedZoomAction = useRef(view.zoomAction);
  const currentZoomAction = useRef(view.zoomAction);
  currentZoomAction.current = view.zoomAction;
  useEffect(() => {
    const orbit = new OrbitControls(camera, gl.domElement);
    orbit.enableDamping = true; orbit.dampingFactor = 0.12;
    orbit.maxPolarAngle = Math.PI / 2.02; orbit.minZoom = 1.5; orbit.maxZoom = 120;
    const onChange = () => invalidate();
    orbit.addEventListener('change', onChange);
    const onStart = () => { target.current = null; };
    orbit.addEventListener('start', onStart);
    controls.current = orbit;
    return () => { orbit.removeEventListener('change', onChange); orbit.removeEventListener('start', onStart); orbit.dispose(); };
  }, [camera, gl, invalidate]);

  useEffect(() => {
    if (!(camera instanceof THREE.OrthographicCamera)) return;
    const center = new THREE.Vector3(bounds.width / 2, (bounds.top + bounds.bottom) / 2, -bounds.length / 2);
    const position = center.clone().add(view.angle === 'plan' ? new THREE.Vector3(0, 80, 0.01) : new THREE.Vector3(43, 47, 57));
    const probe = camera.clone(); probe.position.copy(position); probe.lookAt(center); probe.updateMatrixWorld();
    const corners = [];
    for (const x of [-4, bounds.width + 4]) for (const y of [bounds.bottom - 0.5, bounds.top + 1]) for (const z of [-bounds.length - 3, 4]) corners.push(new THREE.Vector3(x, y, z).applyMatrix4(probe.matrixWorldInverse));
    const extentX = Math.max(...corners.map(p => p.x)) - Math.min(...corners.map(p => p.x));
    const extentY = Math.max(...corners.map(p => p.y)) - Math.min(...corners.map(p => p.y));
    const zoom = Math.min(size.width / extentX, size.height / extentY) * 0.93;
    const next = { position, center, zoom };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      camera.position.copy(position); camera.zoom = zoom; camera.updateProjectionMatrix();
      controls.current?.target.copy(center); controls.current?.update(); target.current = null;
    } else target.current = next;
    if (controls.current) controls.current.enableRotate = view.angle !== 'plan';
    appliedZoomAction.current = currentZoomAction.current;
    invalidate();
  }, [camera, bounds.width, bounds.length, bounds.bottom, bounds.top, size.width, size.height, view.mode, view.angle, view.reset, invalidate]);

  useEffect(() => {
    if (!(camera instanceof THREE.OrthographicCamera) || appliedZoomAction.current === view.zoomAction) return;
    // Each button action multiplies the *actual* orbit-controlled zoom. There is no
    // separate absolute zoom state that can drift after mouse-wheel interaction.
    const zoom = Math.max(1.5, Math.min(120, (target.current?.zoom ?? camera.zoom) * view.zoomFactor));
    if (target.current) target.current.zoom = zoom;
    else { camera.zoom = zoom; camera.updateProjectionMatrix(); }
    appliedZoomAction.current = view.zoomAction;
    invalidate();
  }, [camera, view.zoomAction, view.zoomFactor, invalidate]);

  useFrame((_, delta) => {
    const next = target.current, orbit = controls.current;
    if (next && orbit && camera instanceof THREE.OrthographicCamera) {
      const alpha = 1 - Math.exp(-Math.min(delta, 0.1) * 11);
      camera.position.lerp(next.position, alpha); orbit.target.lerp(next.center, alpha);
      camera.zoom += (next.zoom - camera.zoom) * alpha; camera.updateProjectionMatrix();
      if (camera.position.distanceTo(next.position) < 0.01 && Math.abs(camera.zoom - next.zoom) < 0.01) target.current = null;
      invalidate();
    }
    if (orbit?.update()) invalidate();
  });
  return null;
}
