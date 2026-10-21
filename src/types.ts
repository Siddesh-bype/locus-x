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
export interface Building {
  schema_version: '3.0'; version: string; id: string; name: string; description: string;
  width: number; height: number; default_floor_id: string; floors: Floor[];
  connections: { id: string; kind: 'stairs' | 'elevator'; stops: { floor_id: string; access_id: string }[] }[];
  validation: { units: 'metres'; source: 'fictional-procedural'; geometry_revision: string; notes: string };
}
export type EnvironmentDefinition = Environment | Building;
export interface EnvironmentCatalog {
  environments: EnvironmentDefinition[];
  presets: Record<Scenario, Partial<Settings>>;
  coordinate_system: string;
}
export interface Settings {
  scenario: Scenario; noise_std: number; nlos_bias: number; dropout: number;
  disabled_anchors: string[]; walls_enabled: boolean; route: string; speed: number; seed: number;
}
export interface Estimate {
  position: Point | null; status: string; reason: string; residuals: Record<string, number>;
  reliability: Record<string, number>; covariance: number[][] | null; runtime_ms: number;
  condition_number: number | null;
}
export interface AIPrediction {
  anchor_id: string; p_los: number; p_nlos: number; weight: number;
}
export interface AIMetadata {
  status: 'active' | 'fallback' | 'unavailable' | string; reason: string;
  model_version: string; feature_version: string; ambiguous: boolean;
  n_predicted_los: number; n_predicted_nlos: number;
  predictions: AIPrediction[]; runtime_ms: number;
}
export interface Tracking {
  position: Point | null; velocity: Point | null; covariance: number[][] | null;
  ellipse: { major: number; minor: number; angle: number; level: number } | null;
  status: string; reason: string; updated: boolean; outage_seconds: number; runtime_ms: number;
}
export interface Frame {
  environment_id: string; environment_version: string; environment_sha256: string;
  floor_id: string; floor_elevation: number; localization_mode: '2d_on_known_floor';
  session_id: string; revision: number; frame_index: number; timestamp: number; settings: Settings; anchors: Anchor[];
  measurements: { anchor_id: string; anchor_position: Point; timestamp: number; observed_range: number }[];
  simulator_labels: { anchor_id: string; available: boolean; kind: 'los' | 'nlos' | 'disabled' | 'outage' | 'packet_loss'; intersected_walls: string[]; true_range: number; injected_bias: number }[];
  baseline: Estimate; robust: Estimate;
  tracking: Tracking;
  ai?: Estimate | null; ai_tracking?: Tracking | null; ai_metadata?: AIMetadata | null;
  evaluation: { ground_truth: Point; baseline_error: number | null; proposed_error: number | null; covered_95: boolean | null; ai_error?: number | null; ai_covered_95?: boolean | null; has_nlos: boolean; has_dropout: boolean };
  events: { timestamp: number; message: string; level: string }[];
}
export interface Sample {
  t: number; baseline: number | null; proposed: number | null; ai?: number | null;
  truth?: Point; basePosition?: Point | null; proposedPosition?: Point | null; aiPosition?: Point | null;
  floor_id?: string; environment_id?: string; frame_index?: number;
  baselineUpdated?: boolean; proposedUpdated?: boolean; aiUpdated?: boolean;
  baselineRuntime?: number; proposedRuntime?: number; aiRuntime?: number;
  covered95?: boolean | null; aiCovered95?: boolean | null;
}
export type Method = 'baseline' | 'proposed' | 'ai';
export interface BenchmarkRow {
  scenario: Scenario; anchors: number; seeds: string; method: Method; frames: number; valid_frames: number;
  rmse: number | null; median: number | null; p95: number | null; valid_rate: number; update_rate: number;
  nlos_rmse: number | null; dropout_rmse: number | null; latency_ms: number; coverage_95: number | null;
}
export interface BenchmarkResult {
  config: { scenarios: Scenario[]; anchor_counts: number[]; seeds: number[]; steps: number; dt: number; route: string; environment_id?: string; floor_id?: string };
  result_version?: string; environment_version?: string; floor_id?: string;
  environment_id: string; environment_sha256: string; rows: BenchmarkRow[];
  traces: Record<string, Sample[]>; distributions: Record<string, { baseline: number[]; proposed: number[]; ai?: number[] }>;
  elapsed_seconds: number; notes: string;
}
export interface BenchmarkJob { id: string; status: string; progress: number; result: BenchmarkResult | null; error: string | null }
