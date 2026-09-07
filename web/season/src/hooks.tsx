import { createContext, useContext, useEffect, useState, useSyncExternalStore } from 'react';
import { errorText } from './api.ts';
import { Operations } from './operations.ts';
import { clockAnchor } from './timing.ts';

export const OperationsContext = createContext<Operations | null>(null);
export function useOperations() {
  const operations = useContext(OperationsContext);
  if (!operations) throw new Error('Operations provider is missing.');
  const state = useSyncExternalStore(operations.subscribe, operations.getSnapshot);
  return { operations, state };
}

type Remote<T> = {
  path: string | null; data: T | null; loading: boolean; error: string | null;
  clockAnchorAt: number; revision: number; dataRevision: number;
};
export function useRemote<T>(path: string | null) {
  const { operations, state } = useOperations();
  const [remote, setRemote] = useState<Remote<T>>({
    path: null, data: null, loading: false, error: null, clockAnchorAt: 0, revision: -1, dataRevision: -1,
  });
  const enabled = Boolean(state.status && !state.authRequired);
  useEffect(() => {
    if (!path || !enabled) return;
    let cancelled = false;
    const controller = new AbortController();
    setRemote(previous => ({
      ...previous, path, data: previous.path === path ? previous.data : null,
      dataRevision: previous.path === path ? previous.dataRevision : -1,
      loading: true, error: previous.path === path ? previous.error : null, revision: state.revision,
    }));
    const startedAt = performance.now();
    void operations.api.request<T>(path, { signal: controller.signal }).then(
      data => {
        if (!cancelled) {
          const arrivedAt = performance.now();
          setRemote(previous => ({
            path, data, loading: false, error: null,
            clockAnchorAt: clockAnchor(data, startedAt, arrivedAt,
              previous.path === path ? previous.data : null, previous.clockAnchorAt),
            revision: state.revision, dataRevision: state.revision,
          }));
        }
      },
      error => {
        if (!cancelled) setRemote(previous => ({ ...previous, loading: false, error: errorText(error) }));
      },
    );
    return () => { cancelled = true; controller.abort(); };
  }, [operations, path, enabled, state.revision]);
  if (remote.path !== path || !enabled) return { data: null, loading: enabled && Boolean(path), error: null, clockAnchorAt: 0, dataRevision: -1 };
  return { ...remote, loading: remote.loading || remote.revision !== state.revision };
}

export function useClock() {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => { setNow(performance.now()); timer = setTimeout(tick, 1000); };
    timer = setTimeout(tick, 1000);
    return () => clearTimeout(timer);
  }, []);
  return now;
}
