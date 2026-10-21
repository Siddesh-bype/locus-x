import type { Point, Sample } from './types';

/** Canonical map: backend X,Y metres -> Three.js X,elevation,-Y metres. */
export function toScene([x, y]: Point, elevation = 0): [number, number, number] { return [x, elevation, -y]; }
export function fromScene([x, , z]: [number, number, number]): Point { return [x, -z]; }
export const fmt = (value: number | null | undefined, digits = 2) => value == null ? '—' : value.toFixed(digits);
export const percent = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(1)}%`;
export const scenarioNames = { clean: 'Clean line of sight', noise: 'Moderate noise', high_noise: 'High noise', nlos: 'Wall obstruction', packet_loss: 'Packet loss', anchor_failure: 'Anchor outage', chaos: 'Combined stress' };

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError'; }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...init?.headers }, signal: init?.signal ?? AbortSignal.timeout(30000) });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === 'string' ? body.detail : Array.isArray(body?.detail) ? body.detail.map((item: { msg: string }) => item.msg).join('; ') : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
export function download(name: string, content: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function samplesCsv(samples: Sample[]) {
  const cell = (value: unknown) => {
    const text = String(value ?? '');
    return /[",\r\n]/.test(text) ? '"' + text.replaceAll('"', '""') + '"' : text;
  };
  const rows = samples.map(s => [s.t, s.baseline, s.proposed, s.ai, s.environment_id, s.floor_id,
    s.frame_index, s.baselineUpdated, s.proposedUpdated, s.aiUpdated, s.baselineRuntime, s.proposedRuntime, s.aiRuntime,
    s.covered95, s.aiCovered95, s.truth?.[0], s.truth?.[1], s.basePosition?.[0], s.basePosition?.[1],
    s.proposedPosition?.[0], s.proposedPosition?.[1], s.aiPosition?.[0], s.aiPosition?.[1]].map(cell).join(','));
  return ['time_s,baseline_error_m,locus_x_error_m,locus_x_ai_error_m,environment_id,floor_id,frame_index,baseline_updated,locus_x_updated,locus_x_ai_updated,baseline_runtime_ms,locus_x_runtime_ms,locus_x_ai_runtime_ms,covered_95,locus_x_ai_covered_95,truth_x_m,truth_y_m,baseline_x_m,baseline_y_m,locus_x_x_m,locus_x_y_m,locus_x_ai_x_m,locus_x_ai_y_m', ...rows].join('\n');
}
export function appendSample(history: Sample[], frame: Sample): Sample[] {
  const previous = history.at(-1);
  if (previous && frame.t < previous.t) return [frame];
  if (previous?.t === frame.t) return [...history.slice(0, -1), frame];
  return [...history, frame].slice(-600);
}
