export class ApiError extends Error {
  status: number | undefined;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export class ApiClient {
  #token = '';
  readonly origin: string;
  readonly fetcher: typeof fetch;
  readonly timeoutMs: number;
  onUnauthorized: () => void = () => {};

  constructor(origin: string, fetcher: typeof fetch = (input, init) => globalThis.fetch(input, init), timeoutMs = 20_000) {
    this.origin = origin;
    this.fetcher = fetcher;
    this.timeoutMs = timeoutMs;
  }

  setToken(token: string) { this.#token = token; }
  clearToken() { this.#token = ''; }

  async request<T>(path: string, options: {
    body?: string; key?: string; signal?: AbortSignal;
  } = {}): Promise<T> {
    const url = new URL(path, this.origin);
    if (url.origin !== this.origin || !url.pathname.startsWith('/api/v1/') || url.hash) {
      throw new ApiError('Refusing to send credentials outside this API.');
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const signal = options.signal
      ? AbortSignal.any([controller.signal, options.signal]) : controller.signal;
    const token = this.#token;
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (options.body !== undefined) headers['Content-Type'] = 'application/json';
    if (options.key) headers['Idempotency-Key'] = options.key;
    try {
      const response = await this.fetcher(url.pathname + url.search, {
        method: options.body === undefined ? 'GET' : 'POST',
        headers, body: options.body, signal,
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
      });
      const payload = await response.json().catch(() => null);
      if (response.status === 401 && token === this.#token) {
        this.clearToken();
        this.onUnauthorized();
      }
      if (!response.ok) {
        throw new ApiError(payload?.error?.message || `Request failed (${response.status}).`, response.status);
      }
      if (!payload || typeof payload !== 'object') {
        throw new ApiError(options.body === undefined ? 'The service returned an unreadable response.'
          : 'The response could not be read. A submitted update may still be running.');
      }
      return payload as T;
    } catch (error) {
      if (controller.signal.aborted) {
        throw new ApiError(options.body === undefined ? 'The service took too long to respond. Check your connection and try again.'
          : 'The request timed out. A submitted update may still be running.');
      }
      if (error instanceof TypeError) {
        throw new ApiError('Cannot reach the service. Check your connection and try again.');
      }
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
}

export const snapshotPath = (id: string) => `/api/v1/snapshots/${encodeURIComponent(id)}`;
export const advicePath = (id?: string) => id
  ? `/api/v1/advice/${encodeURIComponent(id)}` : '/api/v1/advice';
export const errorText = (error: unknown) => error instanceof Error ? error.message : 'An unexpected error occurred.';
