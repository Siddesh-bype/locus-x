export type Point = [number, number];
export type Scenario = 'clean' | 'noise' | 'high_noise' | 'nlos' | 'packet_loss' | 'anchor_failure' | 'chaos';
export interface Anchor { id: string; position: Point }
export interface Environment {
  id: string; name: string; width: number; height: number; description: string;
  anchors: Anchor[];
  walls: { id: string; a: Point; b: Point; height: number }[];
  rooms: { name: string; center: Point; size: Point; kind: string }[];
  routes: Record<string, { position: Point; dwell: number }[]>;
}
export interface Region { id: string; name: string; center: Point; size: Point }
export interface AccessCore extends Region { kind: 'stairs' | 'elevator'; entry: Point }
export interface Floor extends Environment {
  short_name: string; elevation: number; color: string;
  rooms: (Environment['rooms'][number] & { id?: string })[];
  corridors: Region[];
  doors: { id: string; a: Point; b: Point; height: number; connects: [string, string] }[];
  access_cores: AccessCore[];
  movement_regions: string[];
  candidate_anchors: Anchor[];
  anchor_layout_id: string;
  route_names: Record<string, string>;
}
export interface TransitionRoute {
  id: string; name: string; loop: boolean;
  legs: { floor_id: string; waypoints: { position: Point; dwell: number }[] }[];
  transitions: { id: string; kind: 'stairs' | 'elevator'; connection_id: string; from_floor_id: string; to_floor_id: string; travel_seconds: number; dwell_before: number; dwell_after: number }[];
}
export interface Building {
  schema_version: '3.0'; version: string; id: string; name: string; description: string;
  width: number; height: number; default_floor_id: string; floors: Floor[];
  connections: { id: string; kind: 'stairs' | 'elevator'; stops: { floor_id: string; access_id: string }[] }[];
  transition_routes?: TransitionRoute[];
  validation: { units: 'metres'; source: 'fictional-procedural'; geometry_revision: string; notes: string };
}
export type LocalizationMode = '2d_on_known_floor' | 'multifloor_route_assisted' | 'multifloor_inferred';
export type FloorSource = 'configured' | 'route_assisted' | 'inferred';
export type TransitionPhase = 'entering' | 'traversing' | 'exiting' | 'reacquiring';
export interface FloorState {
  floor_id: string | null; source: FloorSource; confidence: number | null; ambiguous: boolean;
}
export interface TransitionState {
  phase: TransitionPhase; transition_id: string | null; connection_id: string | null;
  from_floor_id: string | null; to_floor_id: string | null; progress: number | null;
}
export type EnvironmentDefinition = Environment | Building;
export interface EnvironmentCatalog {
  environments: EnvironmentDefinition[];
  presets: Record<Scenario, Partial<Settings>>;
  coordinate_system: string;
}
export interface Settings {
  scenario: Scenario; noise_std: number; nlos_bias: number; dropout: number;
  disabled_anchors: string[]; disabled_anchor_keys?: string[]; walls_enabled: boolean;
  route: string; speed: number; seed: number;
}
export interface Estimate {
  position: Point | null; status: string; reason: string; residuals: Record<string, number>;
  reliability: Record<string, number>; covariance: number[][] | null; runtime_ms: number;
  condition_number: number | null;
}
export interface Frame {
  environment_id: string; environment_version: string; environment_sha256: string;
  floor_id: string | null; floor_elevation: number | null; localization_mode: LocalizationMode;
  session_id: string; revision: number; frame_index: number; timestamp: number; settings: Settings; anchors: Anchor[];
  measurements: { anchor_id: string; anchor_position: Point; timestamp: number; observed_range: number }[];
  simulator_labels: { anchor_id: string; available: boolean; kind: 'los' | 'nlos' | 'disabled' | 'outage' | 'packet_loss'; intersected_walls: string[]; true_range: number; injected_bias: number }[];
  baseline: Estimate; robust: Estimate;
  tracking: {
    position: Point | null; velocity: Point | null; covariance: number[][] | null;
    ellipse: { major: number; minor: number; angle: number; level: number } | null;
    status: string; reason: string; updated: boolean; outage_seconds: number; runtime_ms: number;
  };
  evaluation: { ground_truth: Point; baseline_error: number | null; proposed_error: number | null; covered_95: boolean | null; has_nlos: boolean; has_dropout: boolean; ground_truth_elevation?: number | null };
  events: { timestamp: number; message: string; level: string }[];
  floor_state?: FloorState | null; transition?: TransitionState | null;
  elevation_estimate?: number | null; floor_probabilities?: Record<string, number> | null;
}
export interface Sample {
  t: number; baseline: number | null; proposed: number | null;
  truth?: Point; basePosition?: Point | null; proposedPosition?: Point | null;
  truthElevation?: number | null; proposedElevation?: number | null;
  floor_id?: string | null; environment_id?: string; frame_index?: number;
  baselineUpdated?: boolean; proposedUpdated?: boolean;
  baselineRuntime?: number; proposedRuntime?: number; covered95?: boolean | null;
}
export interface BenchmarkRow {
  scenario: Scenario; anchors: number; seeds: string; method: 'baseline' | 'proposed'; frames: number; valid_frames: number;
  rmse: number | null; median: number | null; p95: number | null; valid_rate: number; update_rate: number;
  nlos_rmse: number | null; dropout_rmse: number | null; latency_ms: number; coverage_95: number | null;
}
export interface BenchmarkResult {
  config: { scenarios: Scenario[]; anchor_counts: number[]; seeds: number[]; steps: number; dt: number; route: string; environment_id?: string; floor_id?: string };
  result_version?: string; environment_version?: string; floor_id?: string;
  environment_id: string; environment_sha256: string; rows: BenchmarkRow[];
  traces: Record<string, Sample[]>; distributions: Record<string, { baseline: number[]; proposed: number[] }>;
  elapsed_seconds: number; notes: string;
}
export interface BenchmarkJob { id: string; status: string; progress: number; result: BenchmarkResult | null; error: string | null }
