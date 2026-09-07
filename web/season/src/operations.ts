import { ApiClient, ApiError, errorText, snapshotPath } from './api.ts';
import type { Job, Status } from './types.ts';

export const PENDING_KEY = 'ffopt.pending.v1';
export const TRACKING_KEY = 'ffopt.job.v1';
const RECOVERY_WARNING = 'Reload-safe recovery is unavailable. Keep this tab open until your request is confirmed.';
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem' | 'removeItem'>;
export type PendingOperation = {
  kind: Job['kind']; path: string; body: string; key: string; uncertain: boolean;
};
export type OperationState = {
  pending: PendingOperation | null;
  tracking: string | null;
  submitting: boolean;
  connecting: boolean;
  authRequired: boolean;
  status: Status | null;
  jobs: Job[];
  error: string | null;
  pollingError: string | null;
  recoveryWarning: string | null;
  revision: number;
  adviceRevision: number;
};
export const activeJob = (job: Job) => job.status === 'queued' || job.status === 'running';
export const operationBlocked = (state: OperationState) => Boolean(
  state.pending || state.tracking || state.submitting || state.connecting
  || state.authRequired || !state.status || state.jobs.some(activeJob),
);
const validJob = (value: unknown): value is Job => {
  if (!value || typeof value !== 'object') return false;
  const job = value as Job;
  return typeof job.id === 'string' && job.id.length > 0
    && ['collect', 'evaluate'].includes(job.kind)
    && ['queued', 'running', 'succeeded', 'failed'].includes(job.status);
};

// Restored metadata can replay only our two supported operations, never an arbitrary URL/body.
export function restorePending(raw: string | null): PendingOperation | null {
  try {
    const value = JSON.parse(raw || 'null');
    if (!value || typeof value.key !== 'string'
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.key)
      || typeof value.body !== 'string' || typeof value.path !== 'string') return null;
    const body = JSON.parse(value.body);
    if (!body || typeof body !== 'object' || Array.isArray(body)) return null;
    if (value.kind === 'collect') {
      if (value.path !== '/api/v1/collections' || body.refresh !== true
        || body.minimum_pickup_gain !== null
        || Object.keys(body).length !== 2) return null;
    } else if (value.kind === 'evaluate') {
      if (!/^\/api\/v1\/snapshots\/[^/?#]+\/evaluations$/.test(value.path)
        || Object.keys(body).length !== 1 || !('minimum_pickup_gain' in body)
        || !(body.minimum_pickup_gain === null || (typeof body.minimum_pickup_gain === 'number'
          && Number.isFinite(body.minimum_pickup_gain) && body.minimum_pickup_gain >= 0))) return null;
    } else return null;
    return { kind: value.kind, path: value.path, body: value.body, key: value.key, uncertain: true };
  } catch { return null; }
}

export class Operations {
  readonly api: ApiClient;
  readonly storage: Storage | undefined;
  readonly uuid: () => string;
  #listeners = new Set<() => void>();
  #polling = false;
  #state: OperationState;

  constructor(api: ApiClient, storage?: Storage, uuid: () => string = () => crypto.randomUUID()) {
    this.api = api;
    this.storage = storage;
    this.uuid = uuid;
    let pending: PendingOperation | null = null;
    let tracking: string | null = null;
    let recoveryWarning = storage ? null : RECOVERY_WARNING;
    try {
      pending = restorePending(storage?.getItem(PENDING_KEY) ?? null);
      const savedJob = storage?.getItem(TRACKING_KEY);
      if (savedJob && /^[a-zA-Z0-9_-]{1,200}$/.test(savedJob)) tracking = savedJob;
    } catch { recoveryWarning = RECOVERY_WARNING; }
    this.#state = {
      pending, tracking, submitting: false, connecting: false, authRequired: false,
      status: null, jobs: [], error: null, pollingError: null, recoveryWarning, revision: 0, adviceRevision: 0,
    };
    api.onUnauthorized = () => this.update({ authRequired: true, status: null });
  }

  getSnapshot = () => this.#state;
  subscribe = (listener: () => void) => {
    this.#listeners.add(listener);
    return () => { this.#listeners.delete(listener); };
  };
  private update(patch: Partial<OperationState>) {
    this.#state = { ...this.#state, ...patch };
    this.#listeners.forEach(listener => listener());
  }
  private persist() {
    if (!this.storage) {
      this.update({ recoveryWarning: RECOVERY_WARNING });
      return;
    }
    try {
      if (this.#state.pending) this.storage.setItem(PENDING_KEY, JSON.stringify(this.#state.pending));
      else this.storage.removeItem(PENDING_KEY);
      if (this.#state.tracking) this.storage.setItem(TRACKING_KEY, this.#state.tracking);
      else this.storage.removeItem(TRACKING_KEY);
      // Clear an earlier read-access warning only once saved recovery can be read.
      this.storage.getItem(PENDING_KEY);
      this.storage.getItem(TRACKING_KEY);
      if (this.#state.recoveryWarning) this.update({ recoveryWarning: null });
    } catch { this.update({ recoveryWarning: RECOVERY_WARNING }); }
  }
  dismissError = () => this.update({ error: null });
  refresh = () => this.update({ revision: this.#state.revision + 1 });

  async connect(token?: string): Promise<boolean> {
    if (this.#state.connecting) return false;
    if (token !== undefined) this.api.setToken(token.trim());
    this.update({ connecting: true, error: null });
    try {
      const status = await this.api.request<Status>('/api/v1/status');
      const revision = this.#state.revision + 1;
      this.update({ status, authRequired: false, revision, adviceRevision: revision });
      await this.poll();
      return true;
    } catch (error) {
      this.update({ error: errorText(error), status: null });
      return false;
    } finally { this.update({ connecting: false }); }
  }

  disconnect = () => {
    this.api.clearToken();
    this.update({ authRequired: true, status: null });
  };

  startUpdate = () => this.start('collect', '/api/v1/collections', { refresh: true, minimum_pickup_gain: null });
  startResearch = (id: string, floor: number | null) => {
    if (!id || (floor !== null && (!Number.isFinite(floor) || floor < 0))) return Promise.resolve();
    return this.start('evaluate', `${snapshotPath(id)}/evaluations`, { minimum_pickup_gain: floor });
  };
  private start(kind: Job['kind'], path: string, body: object) {
    if (operationBlocked(this.#state)) return Promise.resolve();
    const pending = { kind, path, body: JSON.stringify(body), key: this.uuid(), uncertain: false };
    this.update({ pending });
    this.persist();
    return this.submit();
  }
  retry = () => this.submit();

  private async submit() {
    const operation = this.#state.pending;
    if (!operation || this.#state.submitting || this.#state.authRequired || this.#state.connecting) return;
    this.update({ submitting: true, error: null });
    try {
      const response = await this.api.request<{ job: Job }>(operation.path, {
        body: operation.body, key: operation.key,
      });
      if (!validJob(response.job) || response.job.kind !== operation.kind) {
        throw new ApiError('The service did not confirm the update. Retry to recover the original request.');
      }
      const job = response.job;
      const revision = this.#state.revision + 1;
      this.update({
        pending: null,
        tracking: activeJob(job) ? job.id : null,
        jobs: [job, ...this.#state.jobs.filter(item => item.id !== job.id)],
        revision,
        adviceRevision: operation.kind === 'collect' ? revision : this.#state.adviceRevision,
        error: job.status === 'failed' ? job.error || 'The update failed. No new usable advice was obtained.' : null,
      });
    } catch (error) {
      const status = error instanceof ApiError ? error.status : undefined;
      const definite = status !== undefined && status >= 400 && status < 500 && status !== 408 && status !== 401;
      // A rejection on a later retry cannot disprove acceptance of the original request.
      this.update({
        pending: definite && !operation.uncertain ? null : {
          ...operation, uncertain: operation.uncertain || status !== 401,
        },
        error: errorText(error),
      });
    } finally {
      this.update({ submitting: false });
      this.persist();
    }
  }

  async poll() {
    if (this.#polling || this.#state.authRequired) return;
    this.#polling = true;
    try {
      const response = await this.api.request<{ items: Job[] }>('/api/v1/jobs?limit=20');
      const jobs = response.items.filter(validJob);
      const knownIds = new Set(this.#state.jobs.filter(activeJob).map(job => job.id));
      if (this.#state.tracking) knownIds.add(this.#state.tracking);
      for (const id of knownIds) {
        if (!jobs.some(job => job.id === id)) {
          const result = await this.api.request<{ job: Job }>(`/api/v1/jobs/${encodeURIComponent(id)}`);
          if (!validJob(result.job) || result.job.id !== id) throw new ApiError('The update status could not be confirmed.');
          jobs.push(result.job);
        }
      }
      const finished = jobs.some(job => !activeJob(job)
        && (job.id === this.#state.tracking || this.#state.jobs.some(previous => previous.id === job.id && activeJob(previous))));
      const collectionChanged = jobs.some(job => job.kind === 'collect'
        && !this.#state.jobs.some(previous => previous.id === job.id && previous.status === job.status));
      const revision = this.#state.revision + (finished || collectionChanged ? 1 : 0);
      const tracking = this.#state.tracking && jobs.some(job => job.id === this.#state.tracking && activeJob(job))
        ? this.#state.tracking : null;
      this.update({
        jobs, tracking, pollingError: null,
        revision,
        adviceRevision: collectionChanged ? revision : this.#state.adviceRevision,
      });
      this.persist();
    } catch (error) {
      this.update({ pollingError: errorText(error) });
    } finally { this.#polling = false; }
  }
}
