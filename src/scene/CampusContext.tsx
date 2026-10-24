import { useEffect, useMemo, useState } from 'react';
import * as THREE from 'three';
import type { ThreeEvent } from '@react-three/fiber';
import { toScene } from '../lib';
import {
  CAMPUS_BLOCKS,
  CAMPUS_GROUND,
  CAMPUS_ORIGIN,
  CAMPUS_TREES,
  type CampusBlock,
  type Polygon,
} from './campusLayout';
import { solid } from './architectureGeometry';
import { Label, Polyline, Solids, type Solid } from './Primitives';

const BASE = -0.3;
const PLINTH_HEIGHT = 0.45;
const TERRACOTTA = '#9c4126';
const CONCRETE_BEIGE = '#eeebe2';
const PLINTH_COLOR = '#525b62';
const WINDOW_GLASS = '#1e293b';
const CHAJJA_COLOR = '#cfc7b8';
const CANTEEN_ROOF = '#e07a38';

const centroid = (polygon: Polygon): [number, number] => [
  polygon.reduce((s, p) => s + p[0], 0) / polygon.length,
  polygon.reduce((s, p) => s + p[1], 0) / polygon.length,
];

/** Flat ribbon between two campus points with kerb borders. */
function walkway(a: [number, number], b: [number, number]): Solid[] {
  const mid: [number, number] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
  const rot = Math.atan2(b[1] - a[1], b[0] - a[0]);
  return [
    // Main paved walkway slab
    solid(mid, [len, 0.06, 2.2], '#d8d3c5', BASE + 0.05, rot),
    // Walkway kerb borders on left and right
    solid(mid, [len, 0.08, 0.18], '#7c766b', BASE + 0.06, rot),
  ];
}

interface BuildingProps {
  block: CampusBlock;
  hot: boolean;
  onClick?: () => void;
  onHover?: (on: boolean) => void;
}

/**
 * Detailed institutional building model reflecting VIT Pune's concrete-framed architecture:
 * - Raised granite plinth base
 * - Institutional warm off-white/beige masonry
 * - Cantilevered sunshades (chajjas) and terracotta-accent floor slab bands
 * - Recessed window ribbons and architectural mullions
 * - Parapet walls, elevator headhouse machine room & rooftop overhead water tanks
 */
function CampusBuilding({ block, hot, onClick, onHover }: BuildingProps) {
  const { polygon, height, stories, style } = block;
  const isCanteen = style === 'canteen';

  // Base 2D Shape from polygon
  const shape = useMemo(
    () => new THREE.Shape(polygon.map(([x, y]) => new THREE.Vector2(x, y))),
    [polygon]
  );

  // Plinth geometry (granite stone base)
  const plinthGeo = useMemo(
    () => new THREE.ExtrudeGeometry(shape, { depth: PLINTH_HEIGHT, bevelEnabled: false }),
    [shape]
  );

  // Main building body geometry
  const bodyHeight = height - PLINTH_HEIGHT;
  const bodyGeo = useMemo(
    () => new THREE.ExtrudeGeometry(shape, { depth: bodyHeight, bevelEnabled: false }),
    [shape, bodyHeight]
  );

  useEffect(() => {
    return () => {
      plinthGeo.dispose();
      bodyGeo.dispose();
    };
  }, [plinthGeo, bodyGeo]);

  const [cx, cy] = useMemo(() => centroid(polygon), [polygon]);
  const storyHeight = bodyHeight / Math.max(1, stories);

  // Architectural facade elements (chajjas, floor cornices, window ribbons)
  const facadeSolids = useMemo(() => {
    const items: Solid[] = [];
    const n = polygon.length;

    for (let i = 0; i < n; i++) {
      const p1 = polygon[i];
      const p2 = polygon[(i + 1) % n];
      const edgeLen = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
      if (edgeLen < 1.2) continue; // Skip tiny segments

      const edgeMid: [number, number] = [(p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2];
      const edgeAngle = Math.atan2(p2[1] - p1[1], p2[0] - p1[0]);

      // Floor slab bands and sunshades (chajjas) for each story
      for (let s = 1; s <= stories; s++) {
        const slabY = BASE + PLINTH_HEIGHT + s * storyHeight;

        // Terracotta red accent band / floor cornice
        items.push(
          solid(
            edgeMid,
            [edgeLen + 0.1, 0.32, 0.35],
            isCanteen ? CANTEEN_ROOF : TERRACOTTA,
            slabY - 0.28,
            edgeAngle
          )
        );

        // Window ribbon + weather shed (chajja) on each floor level
        if (edgeLen > 4.0) {
          const windowHeight = storyHeight * 0.45;
          const windowBase = slabY - storyHeight + 0.9;

          // Dark tinted glass ribbon
          items.push(
            solid(edgeMid, [edgeLen - 0.6, windowHeight, 0.18], WINDOW_GLASS, windowBase, edgeAngle)
          );

          // Cantilevered horizontal chajja (sunshade) protruding over the windows
          items.push(
            solid(
              edgeMid,
              [edgeLen - 0.4, 0.12, 0.55],
              CHAJJA_COLOR,
              windowBase + windowHeight + 0.05,
              edgeAngle
            )
          );

          // Vertical concrete mullions / louvers along the windows
          const mullionCount = Math.max(2, Math.floor(edgeLen / 3.0));
          const step = (edgeLen - 1.0) / mullionCount;
          for (let m = 0; m <= mullionCount; m++) {
            const frac = (m * step - (edgeLen - 1.0) / 2) / edgeLen;
            const mx = edgeMid[0] + frac * (p2[0] - p1[0]);
            const my = edgeMid[1] + frac * (p2[1] - p1[1]);
            items.push(
              solid([mx, my], [0.18, windowHeight + 0.15, 0.25], CONCRETE_BEIGE, windowBase, edgeAngle)
            );
          }
        }
      }

      // Roof parapet wall
      const roofY = BASE + height;
      items.push(solid(edgeMid, [edgeLen + 0.08, 0.85, 0.22], CONCRETE_BEIGE, roofY, edgeAngle));
    }

    // Rooftop elevator machine room (lift headhouse) & overhead water tanks
    if (!isCanteen && height >= 8.0) {
      // Lift machine room headhouse
      items.push(solid([cx, cy], [5.5, 2.6, 4.8], '#8c9aa4', BASE + height));
      items.push(solid([cx, cy + 2.45], [1.1, 2.0, 0.08], '#334155', BASE + height)); // Steel door

      // Twin overhead water tanks (Sintex industrial water storage)
      items.push(solid([cx - 4.5, cy], [1.8, 1.8, 1.8], '#1e3a8a', BASE + height + 0.4));
      items.push(solid([cx - 4.5, cy], [2.2, 0.4, 2.2], '#525b62', BASE + height)); // Concrete tank plinth
      items.push(solid([cx - 4.5, cy + 2.2], [1.8, 1.8, 1.8], '#1e3a8a', BASE + height + 0.4));
      items.push(solid([cx - 4.5, cy + 2.2], [2.2, 0.4, 2.2], '#525b62', BASE + height));
    }

    return items;
  }, [polygon, height, stories, isCanteen, storyHeight, cx, cy]);

  const click = (event: ThreeEvent<MouseEvent>) => {
    if (onClick && event.delta < 4) {
      event.stopPropagation();
      onClick();
    }
  };

  const wallColor = isCanteen ? '#f5eee6' : CONCRETE_BEIGE;

  return (
    <group>
      {/* Plinth Base */}
      <mesh
        geometry={plinthGeo}
        position={[0, BASE, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        castShadow
        receiveShadow
      >
        <meshStandardMaterial color={PLINTH_COLOR} roughness={0.9} />
      </mesh>

      {/* Main Building Body */}
      <mesh
        geometry={bodyGeo}
        position={[0, BASE + PLINTH_HEIGHT, 0]}
        rotation={[-Math.PI / 2, 0, 0]}
        castShadow
        receiveShadow
        onClick={onClick ? click : undefined}
        onPointerOver={onHover ? (e) => { e.stopPropagation(); onHover(true); } : undefined}
        onPointerOut={onHover ? () => onHover(false) : undefined}
      >
        <meshStandardMaterial
          color={wallColor}
          roughness={0.78}
          emissive={hot ? TERRACOTTA : '#000000'}
          emissiveIntensity={hot ? 0.35 : 0}
        />
      </mesh>

      {/* Architectural Facade, Chajjas, Windows and Roof Utilities */}
      <Solids items={facadeSolids} />
    </group>
  );
}

/** Low-poly architectural campus trees with wood trunk and layered leafy crown */
function CampusTree({ x, y, radius, height }: { x: number; y: number; radius: number; height: number }) {
  const trunkHeight = height * 0.42;
  const crownHeight = height * 0.65;
  const scenePos = toScene([x, y], BASE);

  return (
    <group position={scenePos}>
      {/* Wood Trunk */}
      <mesh position={[0, trunkHeight / 2, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[radius * 0.16, radius * 0.22, trunkHeight, 7]} />
        <meshStandardMaterial color="#543d2b" roughness={0.9} />
      </mesh>
      {/* Leafy Foliage (Layer 1) */}
      <mesh position={[0, trunkHeight + crownHeight * 0.4, 0]} castShadow>
        <dodecahedronGeometry args={[radius, 1]} />
        <meshStandardMaterial color="#386641" roughness={0.75} flatShading />
      </mesh>
      {/* Leafy Foliage (Layer 2 Highlight) */}
      <mesh position={[0, trunkHeight + crownHeight * 0.75, 0]} castShadow>
        <dodecahedronGeometry args={[radius * 0.72, 1]} />
        <meshStandardMaterial color="#588157" roughness={0.72} flatShading />
      </mesh>
    </group>
  );
}

/**
 * VIT Pune Campus 3D Scene Context:
 * Renders real-world academic building architecture, main entrance portico,
 * paved inter-building pathways, sports ground with pitch & track, and mature trees.
 */
export default function CampusContext({
  environmentId,
  width,
  selectEnvironment,
}: {
  environmentId: string;
  width: number;
  selectEnvironment: (id: string) => void;
}) {
  const [ox, oy] = CAMPUS_ORIGIN[environmentId] ?? [0, 0];
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    document.body.style.cursor = hover ? 'pointer' : '';
    return () => {
      document.body.style.cursor = '';
    };
  }, [hover]);

  const blocks = useMemo(
    () =>
      CAMPUS_BLOCKS.filter(
        (b) => b.hideFor !== environmentId && (!b.onlyFor || b.onlyFor === environmentId)
      ),
    [environmentId]
  );

  const entrance: [number, number] = useMemo(() => [ox + width / 2, oy], [ox, width, oy]);

  // Inter-building paved walkway corridors
  const walkways = useMemo(
    () => blocks.flatMap((b) => walkway(centroid(b.polygon), entrance)),
    [blocks, entrance]
  );

  // Building 1 / Main Academic Block Entrance Portico solids
  const entrancePortico = useMemo((): Solid[] => {
    const px = entrance[0];
    const py = entrance[1];
    return [
      // Concrete stairs / entrance podium
      solid([px, py + 1.2], [9.0, 0.35, 3.2], '#d5d0c3', BASE + 0.05),
      solid([px, py + 1.8], [8.0, 0.55, 2.2], '#78716c', BASE + 0.05),
      // 4 Grand white structural columns
      solid([px - 3.2, py + 0.8], [0.65, 4.4, 0.65], '#ffffff', BASE + 0.35),
      solid([px - 1.1, py + 0.8], [0.65, 4.4, 0.65], '#ffffff', BASE + 0.35),
      solid([px + 1.1, py + 0.8], [0.65, 4.4, 0.65], '#ffffff', BASE + 0.35),
      solid([px + 3.2, py + 0.8], [0.65, 4.4, 0.65], '#ffffff', BASE + 0.35),
      // Terracotta trimmed entrance canopy slab
      solid([px, py + 0.8], [9.4, 0.42, 3.8], TERRACOTTA, BASE + 4.7),
      solid([px, py + 0.8], [9.0, 0.15, 3.4], '#f8fafc', BASE + 5.1),
    ];
  }, [entrance]);

  // Sports ground center pitch
  const sportsFieldDetails = useMemo((): Solid[] => {
    const groundPoly = CAMPUS_GROUND[1].polygon;
    const [gcx, gcy] = centroid(groundPoly);
    return [
      // Central turf / cricket pitch
      solid([gcx, gcy], [4.5, 0.05, 14.0], '#c2b083', BASE + 0.05),
      // Running track inner curb
      solid([gcx, gcy], [22.0, 0.04, 34.0], '#854d0e', BASE + 0.03),
    ];
  }, []);

  return (
    <group position={[-ox, 0, oy]}>
      {/* Base Campus Ground Planes */}
      {CAMPUS_GROUND.map((g, i) => {
        const shape = new THREE.Shape(g.polygon.map(([x, y]) => new THREE.Vector2(x, y)));
        const geo = new THREE.ExtrudeGeometry(shape, {
          depth: 0.04 + i * 0.02,
          bevelEnabled: false,
        });
        return (
          <mesh
            key={g.name}
            geometry={geo}
            position={[0, BASE, 0]}
            rotation={[-Math.PI / 2, 0, 0]}
            receiveShadow
          >
            <meshStandardMaterial color={g.color} roughness={0.92} />
          </mesh>
        );
      })}

      {/* VIT Sports Ground White Boundary Track */}
      <Polyline
        points={[...CAMPUS_GROUND[1].polygon, CAMPUS_GROUND[1].polygon[0]].map((p) =>
          toScene(p as [number, number], BASE + 0.12)
        )}
        color="#ffffff"
        opacity={0.95}
      />
      <Solids items={sportsFieldDetails} />

      {/* Inter-Building Paved Walkways */}
      <Solids items={walkways} />

      {/* Main Entrance Portico */}
      <Solids items={entrancePortico} />

      {/* Architectural Campus Buildings */}
      {blocks.map((b) => (
        <CampusBuilding
          key={b.name}
          block={b}
          hot={hover === b.name}
          onClick={b.hideFor ? () => selectEnvironment(b.hideFor!) : undefined}
          onHover={b.hideFor ? (on) => setHover(on ? b.name : null) : undefined}
        />
      ))}

      {/* Campus Mature Trees */}
      {CAMPUS_TREES.map(([tx, ty, tr, th], i) => (
        <CampusTree key={i} x={tx} y={ty} radius={tr} height={th} />
      ))}

      {/* Building Labels and Navigation Tags */}
      {blocks.map((b) => {
        const [bcx, bcy] = centroid(b.polygon);
        const isClickable = Boolean(b.hideFor);
        const labelText = b.buildingNumber
          ? `${b.buildingNumber} · ${b.name.split(' (')[0]}`
          : b.name;
        return (
          <group key={b.name}>
            <Label
              text={isClickable ? `${labelText}  ➔  OPEN` : labelText}
              position={toScene([bcx, bcy], b.height + 1.2)}
              scale={isClickable ? 7.5 : 6.0}
              color={hover === b.name ? '#9c4126' : '#1e293b'}
            />
          </group>
        );
      })}

      {/* Ground & Entrance Labels */}
      <Label
        text="VIT SPORTS GROUND"
        position={toScene(centroid(CAMPUS_GROUND[1].polygon), 0.35)}
        scale={6.5}
        color="#ffffff"
      />
      <Label
        text="VISHWAKARMA INSTITUTE OF TECHNOLOGY · MAIN ENTRANCE"
        position={toScene([entrance[0], entrance[1] - 1.5], 5.8)}
        scale={4.8}
        color="#9c4126"
      />
    </group>
  );
}
