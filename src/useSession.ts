import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, request } from './lib';
import { floorForFrame, floorsOf, isBuilding, recordFrame } from './workspace';
import type { EnvironmentCatalog, EnvironmentDefinition, Frame, Sample, Settings } from './types';

export type SettingsUpdate = Partial<Settings> | ((current: Settings) => Partial<Settings>);
export type UpdateSettings = (settings: SettingsUpdate) => Promise<boolean>;
const STORAGE_KEY = 'locus-x-v3-session';

export function useSession() {
  const [environments, setEnvironments] = useState<EnvironmentDefinition[]>([]);
  const [frame, setFrame] = useState<Frame | null>(null);
  const [history, setHistory] = useState<Sample[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const latest = useRef<Frame | null>(null);
  const catalog = useRef<EnvironmentDefinition[]>([]);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const alive = useRef(true);

  const accept = useCallback((next: Frame) => {
    if (!alive.current) return;
    const definition = catalog.current.find(env => env.id === next.environment_id);
    if (!definition) throw new Error('The session environment is unavailable. Reconnect to reload the catalog.');
    floorForFrame(definition, next);
    const previous = latest.current;
    latest.current = next; setFrame(next); setError(null);
    sessionStorage.setItem(STORAGE_KEY, next.session_id);
    setHistory(current => recordFrame(current, previous, next));
  }, []);

  const enqueue = useCallback((operation: () => Promise<Frame>) => {
    const action = async () => {
      if (!alive.current) return false;
      setBusy(true);
      try {
        accept(await operation());
        return true;
      } catch (cause) {
        if (!alive.current) return false;
        setRunning(false);
        const message = cause instanceof Error ? cause.message : 'Connection interrupted';
        if (cause instanceof ApiError && cause.status === 409 && latest.current) {
          try { accept(await request<Frame>(`/sessions/${latest.current.session_id}`)); } catch { /* Keep the original error visible. */ }
        }
        setError(message);
        return false;
      } finally { if (alive.current) setBusy(false); }
    };
    const result = queue.current.then(action, action);
    queue.current = result;
    return result;
  }, [accept]);

  const connect = useCallback(() => {
    setRunning(false);
    return enqueue(async () => {
      const response = await request<EnvironmentCatalog>('/environments');
      if (!response.environments.length) throw new Error('No validated environments are available.');
      catalog.current = response.environments;
      if (alive.current) setEnvironments(response.environments);
      const stored = sessionStorage.getItem(STORAGE_KEY);
      if (stored) {
        try { return await request<Frame>(`/sessions/${stored}`); }
        catch (cause) { if (!(cause instanceof ApiError) || cause.status !== 404) throw cause; }
      }
      const initial = response.environments.find(env => env.id === 'hospital_v3') ?? response.environments[0];
      return request<Frame>('/sessions', { method: 'POST', body: JSON.stringify({ environment_id: initial.id }) });
    });
  }, [enqueue]);

  useEffect(() => { alive.current = true; void connect(); return () => { alive.current = false; }; }, [connect]);

  const mutate = useCallback((operation: 'advance' | 'controls' | 'reset' | 'floor', values: Record<string, unknown> | ((settings: Settings) => Partial<Settings>) = {}) => enqueue(async () => {
    const current = latest.current;
    if (!current) throw new Error('Connect to the localization engine first.');
    return request<Frame>(`/sessions/${current.session_id}/${operation}`, {
      method: operation === 'controls' ? 'PATCH' : 'POST',
      body: JSON.stringify({ expected_revision: current.revision, ...(typeof values === 'function' ? values(current.settings) : values) }),
    });
  }), [enqueue]);

  const selectEnvironment = useCallback((id: string) => {
    setRunning(false);
    return enqueue(async () => {
      const definition = catalog.current.find(env => env.id === id);
      if (!definition) throw new Error('Unknown environment.');
      const current = latest.current;
      if (current?.environment_id === id) return current;
      const floor = isBuilding(definition) ? definition.floors.find(f => f.id === definition.default_floor_id)! : floorsOf(definition)[0];
      const settings = current ? { ...current.settings, disabled_anchors: [], route: current.settings.route in floor.routes ? current.settings.route : Object.keys(floor.routes)[0] } : undefined;
      const next = await request<Frame>('/sessions', { method: 'POST', body: JSON.stringify({ environment_id: id, floor_id: floor.id, settings }) });
      // Release only the old ephemeral session after the replacement succeeds.
      if (current) await request<void>(`/sessions/${current.session_id}`, { method: 'DELETE' }).catch(() => undefined);
      return next;
    });
  }, [enqueue]);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      await mutate('advance', { dt: 0.2 });
      if (!cancelled) timer = setTimeout(tick, 150);
    };
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [running, mutate]);

  const environment = useMemo(() => environments.find(env => env.id === frame?.environment_id) ?? null, [environments, frame?.environment_id]);
  const floor = useMemo(() => environment && frame ? floorForFrame(environment, frame) : null, [environment, frame]);

  return {
    environments, environment, floor, frame, history, running, setRunning, error, busy, connect, selectEnvironment,
    selectFloor: (floor_id: string) => { setRunning(false); return mutate('floor', { floor_id }); },
    update: (settings: SettingsUpdate) => mutate('controls', settings),
    reset: () => { setRunning(false); return mutate('reset'); },
    step: () => mutate('advance', { dt: 0.2 }),
  };
}
