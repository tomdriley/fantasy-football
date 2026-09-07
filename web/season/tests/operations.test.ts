import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ApiClient } from '../src/api.ts';
import { Operations, PENDING_KEY, TRACKING_KEY, operationBlocked, restorePending } from '../src/operations.ts';
import type { Job } from '../src/types.ts';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status, headers: { 'Content-Type': 'application/json' },
});
const memoryStorage = () => {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value); },
    removeItem: (key: string) => { values.delete(key); },
    values,
  };
};
type Outcome = number | 'transport' | 'unreadable' | 'missing-job' | 'wrong-kind' | 'hold';
async function setup() {
  const storage = memoryStorage();
  const server = {
    outcomes: [] as Outcome[], posts: [] as { path: string; body: string; key: string }[],
    jobs: new Map<string, { request: { path: string; body: string; key: string }; job: Job }>(),
    locked: false, failLookup: false, hideJobs: false, release: () => {}, keys: 0,
  };
  const fetcher = (async (input: string | URL | Request, options: RequestInit = {}) => {
    const path = String(input);
    if (options.method === 'POST') {
      const headers = new Headers(options.headers);
      const post = { path, body: String(options.body), key: headers.get('Idempotency-Key')! };
      server.posts.push(post);
      if (server.locked && headers.get('Authorization') !== 'Bearer fixture-token') {
        return json({ error: { code: 'unauthorized', message: 'Token required' } }, 401);
      }
      const outcome = server.outcomes.shift();
      if (typeof outcome === 'number' && outcome >= 400 && outcome < 500 && outcome !== 408) {
        return json({ error: { message: 'Request rejected' } }, outcome);
      }
      const prior = server.jobs.get(post.key);
      if (prior) assert.deepEqual(post, prior.request, 'same endpoint, exact body and idempotency key');
      else server.jobs.set(post.key, {
        request: post,
        job: { id: `job-${server.jobs.size + 1}`, kind: path === '/api/v1/collections' ? 'collect' : 'evaluate',
          status: 'queued', snapshot_id: null, created_at_ms: 5000 },
      });
      if (outcome === 'transport') throw new TypeError('Connection lost after acceptance');
      if (outcome === 'unreadable') return new Response('{', { status: 202 });
      if (outcome === 'missing-job') return json({}, 202);
      if (outcome === 'wrong-kind') return json({ job: { ...server.jobs.get(post.key)!.job, kind: 'evaluate' } }, 202);
      if (outcome === 'hold') await new Promise<void>(resolve => { server.release = resolve; });
      if (typeof outcome === 'number') return json({ error: { message: 'Acceptance unknown' } }, outcome);
      return json({ job: server.jobs.get(post.key)!.job, poll_url: 'https://untrusted.invalid/ignored' }, 202);
    }
    if (server.locked && new Headers(options.headers).get('Authorization') !== 'Bearer fixture-token') {
      return json({ error: { message: 'Token required' } }, 401);
    }
    if (path === '/api/v1/status') return json({
      mode: 'local-demo', league: { name: 'League', team_name: 'Team', season: '2026' },
      storage: { unique_payloads: 0, raw_bytes: 0, compressed_bytes: 0 }, jobs: { queued: 0, running: 0 },
      worker: { running: true }, server_time_ms: 10_000,
    });
    if (path.startsWith('/api/v1/jobs')) {
      if (server.failLookup) throw new TypeError('Offline');
      if (path === '/api/v1/jobs?limit=20') {
        return json({ items: server.hideJobs ? [] : [...server.jobs.values()].map(record => record.job) });
      }
      const job = [...server.jobs.values()].find(record => path === `/api/v1/jobs/${record.job.id}`)?.job;
      return job ? json({ job }) : json({ error: { message: 'Not found' } }, 404);
    }
    throw new Error(`Unexpected path ${path}`);
  }) as typeof fetch;
  const api = new ApiClient('http://localhost:8787', fetcher);
  const uuid = () => `00000000-0000-4000-8000-${String(++server.keys).padStart(12, '0')}`;
  const operations = new Operations(api, storage, uuid);
  await operations.connect();
  assert.equal(operationBlocked(operations.getSnapshot()), false);
  return { server, api, storage, operations, uuid, fetcher };
}

test('lost collection response blocks new requests and retries the exact original once', async () => {
  const { server, operations, storage } = await setup();
  server.outcomes = ['transport'];
  await operations.startUpdate();
  assert.equal(operationBlocked(operations.getSnapshot()), true);
  assert.equal(operations.getSnapshot().pending?.uncertain, true);
  assert.ok(storage.getItem(PENDING_KEY));
  operations.dismissError();
  await operations.startResearch('different-snapshot', 9);
  await operations.startUpdate();
  assert.equal(server.posts.length, 1);
  server.failLookup = true;
  await operations.poll();
  assert.ok(operations.getSnapshot().pending);
  server.failLookup = false;
  await operations.poll();
  assert.ok(operations.getSnapshot().pending, 'finding a similar job does not prove request identity');
  await operations.retry();
  assert.equal(server.keys, 1);
  assert.equal(server.jobs.size, 1);
  assert.equal(server.posts[0].body, '{"refresh":true,"minimum_pickup_gain":null}');
  assert.deepEqual(server.posts[0], server.posts[1]);
  assert.equal(operations.getSnapshot().pending, null);
  assert.equal(storage.getItem(PENDING_KEY), null);
  assert.equal(storage.getItem(TRACKING_KEY), 'job-1');
});

test('collection transitions require a subsequent advice response, normal polls do not', async () => {
  const { server, operations } = await setup();
  const before = operations.getSnapshot().adviceRevision;
  await operations.startUpdate();
  const accepted = operations.getSnapshot().adviceRevision;
  assert.ok(accepted > before);
  const record = [...server.jobs.values()][0];
  record.job = { ...record.job, status: 'failed', error: 'Update failed' };
  await operations.poll();
  const completed = operations.getSnapshot().adviceRevision;
  assert.ok(completed > accepted);
  operations.refresh();
  assert.equal(operations.getSnapshot().adviceRevision, completed);
  await operations.poll();
  assert.equal(operations.getSnapshot().adviceRevision, completed);
});

test('network, timeout status, server, truncated success, and missing acknowledgments stay uncertain', async t => {
  for (const outcome of [408, 500, 502, 503, 'transport', 'unreadable', 'missing-job', 'wrong-kind'] as Outcome[]) {
    await t.test(String(outcome), async () => {
      const { server, operations } = await setup();
      server.outcomes = [outcome];
      await operations.startUpdate();
      assert.ok(operations.getSnapshot().pending);
      await operations.retry();
      assert.equal(operations.getSnapshot().pending, null);
      assert.equal(server.jobs.size, 1);
      assert.deepEqual(server.posts[0], server.posts[1]);
    });
  }
});

test('initial definite rejection permits a new intentional operation and new key', async t => {
  for (const status of [400, 403, 404, 409, 422, 429]) {
    await t.test(String(status), async () => {
      const { operations, server } = await setup();
      server.outcomes = [status];
      await operations.startUpdate();
      assert.equal(server.jobs.size, 0);
      assert.equal(operations.getSnapshot().pending, null);
      assert.equal(operationBlocked(operations.getSnapshot()), false);
      await operations.startUpdate();
      assert.equal(server.keys, 2);
      assert.notEqual(server.posts[0].key, server.posts[1].key);
    });
  }
});

test('later rejection never erases prior acceptance uncertainty', async () => {
  const { operations, server } = await setup();
  server.outcomes = ['transport', 422];
  await operations.startUpdate();
  await operations.retry();
  assert.ok(operations.getSnapshot().pending);
  await operations.retry();
  assert.equal(server.keys, 1);
  assert.equal(server.jobs.size, 1);
  assert.deepEqual(server.posts, [server.posts[0], server.posts[0], server.posts[0]]);
});

test('research retry survives auth changes and never adopts later form inputs', async () => {
  const { operations, server, storage } = await setup();
  server.outcomes = [503];
  await operations.startResearch('snapshot-a', 2);
  server.locked = true;
  await operations.retry();
  assert.equal(operations.getSnapshot().authRequired, true);
  assert.ok(operations.getSnapshot().pending);
  await operations.connect('fixture-token');
  assert.equal(operations.getSnapshot().authRequired, false);
  assert.ok(operations.getSnapshot().pending);
  await operations.startResearch('snapshot-b', 9);
  await operations.retry();
  assert.equal(server.keys, 1);
  assert.equal(server.jobs.size, 1);
  assert.equal(server.posts[0].path, '/api/v1/snapshots/snapshot-a/evaluations');
  assert.equal(server.posts[0].body, '{"minimum_pickup_gain":2}');
  assert.deepEqual(server.posts, [server.posts[0], server.posts[0], server.posts[0]]);
  assert.equal(JSON.stringify([...storage.values]).includes('fixture-token'), false);
});

test('initial auth challenge and explicit disconnect preserve the pending request', async () => {
  const { operations, server } = await setup();
  server.locked = true;
  await operations.startUpdate();
  assert.equal(server.jobs.size, 0);
  const pending = operations.getSnapshot().pending;
  operations.disconnect();
  assert.deepEqual(operations.getSnapshot().pending, pending);
  await operations.connect('fixture-token');
  await operations.retry();
  assert.equal(server.jobs.size, 1);
  assert.equal(server.keys, 1);
  assert.deepEqual(server.posts[0], server.posts[1]);
});

test('repeated retry clicks never overlap requests', async () => {
  const { operations, server } = await setup();
  server.outcomes = ['transport', 'hold'];
  await operations.startUpdate();
  const running = operations.retry();
  await operations.retry();
  assert.equal(server.posts.length, 2);
  assert.equal(operations.getSnapshot().submitting, true);
  server.release();
  await running;
  assert.equal(operations.getSnapshot().pending, null);
});

test('page reload recovers only non-secret metadata and replays the same request', async () => {
  const { operations, server, storage, fetcher, uuid } = await setup();
  server.outcomes = ['transport'];
  await operations.startUpdate();
  const restored = new Operations(new ApiClient('http://localhost:8787', fetcher), storage, uuid);
  await restored.connect();
  assert.equal(restored.getSnapshot().pending?.uncertain, true);
  await restored.retry();
  assert.equal(server.keys, 1);
  assert.equal(server.jobs.size, 1);
  assert.deepEqual(server.posts[0], server.posts[1]);
});

test('durable job tracking survives reload, follows jobs outside list limit and failed captures', async () => {
  const { operations, server, storage, fetcher, uuid } = await setup();
  await operations.startUpdate();
  const restored = new Operations(new ApiClient('http://localhost:8787', fetcher), storage, uuid);
  server.hideJobs = true;
  await restored.connect();
  assert.equal(restored.getSnapshot().tracking, 'job-1');
  assert.equal(operationBlocked(restored.getSnapshot()), true);
  const revision = restored.getSnapshot().revision;
  const record = [...server.jobs.values()][0];
  Object.assign(record.job, { status: 'failed', snapshot_id: 'failed-capture', error: 'Missing inputs' });
  await restored.poll();
  assert.equal(restored.getSnapshot().tracking, null);
  assert.equal(restored.getSnapshot().jobs[0].snapshot_id, 'failed-capture');
  assert.equal(restored.getSnapshot().revision, revision + 1);
  assert.equal(operationBlocked(restored.getSnapshot()), false);
  assert.equal(storage.getItem(TRACKING_KEY), null);
});

test('storage restrictions do not interrupt mutation recovery', async () => {
  const { api, server, uuid } = await setup();
  const denied = {
    getItem: () => { throw new Error('Denied'); },
    setItem: () => { throw new Error('Denied'); },
    removeItem: () => { throw new Error('Denied'); },
  };
  const operations = new Operations(api, denied, uuid);
  assert.match(operations.getSnapshot().recoveryWarning!, /Keep this tab open until your request is confirmed/);
  await operations.connect();
  server.outcomes = ['transport'];
  await operations.startUpdate();
  assert.ok(operations.getSnapshot().pending);
  assert.match(operations.getSnapshot().recoveryWarning!, /Reload-safe recovery is unavailable/);
  await operations.retry();
  assert.equal(server.jobs.size, 1);
  assert.deepEqual(server.posts[0], server.posts[1]);
});

test('write failure warns without losing in-memory retries and clears when storage recovers', async () => {
  const { api, server, storage, uuid } = await setup();
  let writeDenied = true;
  const operations = new Operations(api, {
    ...storage,
    setItem(key, value) {
      if (writeDenied) throw new Error('Storage quota exceeded');
      storage.setItem(key, value);
    },
  }, uuid);
  await operations.connect();
  assert.equal(operations.getSnapshot().recoveryWarning, null);
  server.outcomes = ['transport'];
  await operations.startUpdate();
  const pending = operations.getSnapshot().pending;
  assert.ok(pending);
  assert.equal(storage.getItem(PENDING_KEY), null);
  assert.match(operations.getSnapshot().recoveryWarning!, /Keep this tab open until your request is confirmed/);
  operations.dismissError();
  assert.ok(operations.getSnapshot().recoveryWarning, 'dismissing a request error must not hide the storage warning');
  writeDenied = false;
  await operations.poll();
  assert.equal(operations.getSnapshot().recoveryWarning, null);
  assert.deepEqual(operations.getSnapshot().pending, pending);
  assert.equal(JSON.parse(storage.getItem(PENDING_KEY)!).key, pending.key);
  await operations.retry();
  assert.deepEqual(server.posts[0], server.posts[1]);
  assert.equal(server.jobs.size, 1);
});

test('unavailable sessionStorage access has a visible recovery warning', async () => {
  const { api, uuid } = await setup();
  const operations = new Operations(api, undefined, uuid);
  assert.match(operations.getSnapshot().recoveryWarning!, /Reload-safe recovery is unavailable/);
  await operations.connect();
  assert.match(operations.getSnapshot().recoveryWarning!, /Keep this tab open/);
});

test('restored metadata rejects unsupported endpoints, fields, keys and non-finite floors', () => {
  const valid = {
    kind: 'collect', path: '/api/v1/collections', body: '{"refresh":true,"minimum_pickup_gain":null}',
    key: '00000000-0000-4000-8000-000000000001', uncertain: false,
  };
  assert.equal(restorePending(JSON.stringify(valid))?.uncertain, true);
  for (const change of [
    { path: 'https://evil.invalid/api/v1/collections' }, { key: 'not-a-uuid' },
    { body: '{"refresh":false,"minimum_pickup_gain":null}' }, { kind: 'delete' },
    { body: '{"refresh":true,"minimum_pickup_gain":null,"token":"secret"}' },
    { kind: 'evaluate', path: '/api/v1/snapshots/s/evaluations', body: '{"minimum_pickup_gain":-2}' },
    { kind: 'evaluate', path: '/api/v1/snapshots/s/evaluations', body: '{"minimum_pickup_gain":1e999}' },
  ]) assert.equal(restorePending(JSON.stringify({ ...valid, ...change })), null);
  assert.equal(restorePending('not json'), null);
});
