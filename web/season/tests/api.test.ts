import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiClient, ApiError, advicePath, snapshotPath } from '../src/api.ts';

test('API requests stay same-origin, reject redirects, and never place token in URLs', async () => {
  const requests: { path: string; init: RequestInit }[] = [];
  const api = new ApiClient('http://localhost:8787', (async (path, init) => {
    requests.push({ path: String(path), init: init! });
    return new Response('{}');
  }) as typeof fetch);
  api.setToken('fixture-secret');
  await api.request('/api/v1/status');
  assert.equal(new Headers(requests[0].init.headers).get('Authorization'), 'Bearer fixture-secret');
  assert.equal(requests[0].path, '/api/v1/status');
  assert.equal(requests[0].init.redirect, 'error');
  assert.equal(requests[0].init.cache, 'no-store');
  for (const path of ['https://evil.invalid/api/v1/status', '//evil.invalid/api/v1/status', '/api/old', '/outside', '/api/v1/status#secret']) {
    await assert.rejects(api.request(path), /outside this API/);
  }
  assert.equal(requests.length, 1);
  api.clearToken();
  await api.request('/api/v1/status');
  assert.equal(new Headers(requests[1].init.headers).has('Authorization'), false);
  assert.equal(JSON.stringify(api).includes('fixture-secret'), false);
});

test('a stale unauthorized response cannot clear a newly entered token', async () => {
  let release: (response: Response) => void = () => {};
  let unauthorized = 0;
  const api = new ApiClient('http://localhost:8787', (() => new Promise<Response>(resolve => { release = resolve; })) as typeof fetch);
  api.onUnauthorized = () => { unauthorized += 1; };
  api.setToken('old');
  const request = api.request('/api/v1/status');
  api.setToken('new');
  release(new Response('{"error":{"message":"Expired"}}', { status: 401 }));
  await assert.rejects(request, /Expired/);
  assert.equal(unauthorized, 0);
});

test('timeout aborts the request and leaves acceptance uncertain', async () => {
  const api = new ApiClient('http://localhost:8787', ((_path, init) => new Promise<Response>((_resolve, reject) => {
    init!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  })) as typeof fetch, 5);
  await assert.rejects(api.request('/api/v1/collections', { body: '{}', key: 'key' }), error => {
    assert.ok(error instanceof ApiError);
    assert.equal(error.status, undefined);
    assert.match(error.message, /timed out/);
    return true;
  });
});

test('current advice is separate from encoded historical and research identifiers', () => {
  assert.equal(advicePath(), '/api/v1/advice');
  assert.equal(advicePath('old/report?x=1'), '/api/v1/advice/old%2Freport%3Fx%3D1');
  assert.equal(snapshotPath('old/report?x=1'), '/api/v1/snapshots/old%2Freport%3Fx%3D1');
});

test('default fetch preserves the browser global receiver', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async function (this: unknown) {
    assert.equal(this, globalThis, 'browser fetch must not receive an ApiClient as its receiver');
    return new Response('{}');
  };
  try {
    await new ApiClient('http://localhost:8787').request('/api/v1/status');
  } finally { globalThis.fetch = original; }
});
