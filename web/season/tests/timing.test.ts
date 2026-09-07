import assert from 'node:assert/strict';
import { test } from 'node:test';
import { clockAnchor } from '../src/timing.ts';

test('transit time is included even on the initial response', () => {
  const anchor = clockAnchor({ now_ms: 1000 }, 100, 10_100);
  assert.equal(anchor, 100);
  assert.equal(1000 + (10_100 - anchor), 11_000);
});

test('a quicker later response cannot move the effective clock backwards', () => {
  const previous = { now_ms: 1000 };
  const oldAnchor = clockAnchor(previous, 0, 10_000);
  const later = { now_ms: 5000 };
  const newAnchor = clockAnchor(later, 10_900, 11_000, previous, oldAnchor);
  const oldNow = previous.now_ms + 11_000 - oldAnchor;
  const newNow = later.now_ms + 11_000 - newAnchor;
  assert.ok(newNow >= oldNow);
});

test('non-clock payloads use request start without inventing a server timestamp', () => {
  assert.equal(clockAnchor({ items: [] }, 100, 500), 100);
});
