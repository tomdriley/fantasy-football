import assert from 'node:assert/strict';
import { test } from 'node:test';
import { adviceUsable, availabilityChecks, confirmedAdvice, dateTime, displayedActions, displayedDecisions, historyItems, points, safeEvaluations } from '../src/presentation.ts';
import { clockAnchor } from '../src/timing.ts';
import type { Advice, Evaluation } from '../src/types.ts';

const recent = (): Advice => ({
  mode: 'current', now_ms: 1_000_000,
  league: { name: 'League', team_name: 'Team', season: '2026' },
  snapshot: { id: 'baseline', status: 'complete', week: 2, season: '2026', as_of_ms: 900_000 },
  latest_attempt: { id: 'baseline', status: 'complete', finished_at_ms: 900_000, errors: [] },
  freshness: { state: 'recent', usable: true, age_ms: 100_000, message: 'Recent', reasons: [], threshold_seconds: 1800, valid_until_ms: null },
  next_deadline: { at_ms: 1_100_000, players: [{ player_id: 'p1', name: 'Player' }] },
  summary: { headline: 'Check availability', lineup_change_count: 1, injury_count: 1, missing_slots: [], projected_total: 18 },
  decisions: {
    lineup: { action: 'change', title: 'Change lineup', instruction: 'Use the suggested lineup.',
      reason: 'Current forecasts.', basis: 'forecast_baseline', blocked: false },
    roster: { action: 'hold', title: 'Keep your roster', instruction: 'Do not add or drop.',
      reason: 'Future value is not modeled.', basis: 'conservative_default', blocked: false },
  },
  actions: [{ id: 'a1', kind: 'lineup', priority: 'required', title: 'Suggested change',
    description: 'Check lineup', instructions: ['Make the change in Sleeper'], player_ids: ['p1'], blocked: false }],
  lineup: [{ slot_index: 0, slot: 'WR', label: 'WR', player_id: 'p1', name: 'Player', position: 'WR',
    status: 'Questionable', projected_points: 18, kickoff_ms: 1_100_000, locked_at_capture: false,
    current_player_id: 'p2', current_player_name: 'Other player', change: 'start' }],
  injuries: [{ player_id: 'p1', name: 'Player', status: 'Questionable', kickoff_ms: 1_100_000, note: 'Check official inactives' }],
  pickups: [], warnings: [], sleeper_url: 'https://sleeper.com/', evaluation_id: 'baseline-evaluation', limitations: [],
});

test('only usable current baseline presents live action instructions', () => {
  const advice = recent();
  assert.equal(adviceUsable(advice), true);
  assert.deepEqual(displayedActions(advice), advice.actions);
  assert.deepEqual(displayedActions(advice, 0, true), [], 'updating suppresses live actions');
  for (const state of ['stale', 'unavailable', 'locks_changed', 'rules_changed', 'historical'] as const) {
    const old = { ...advice, freshness: { ...advice.freshness, state, usable: false } };
    assert.equal(adviceUsable(old), false);
    assert.deepEqual(displayedActions(old), []);
  }
  assert.deepEqual(displayedActions({ ...advice, mode: 'historical' }), []);
  assert.deepEqual(displayedActions({ ...advice, snapshot: null }), []);
  assert.deepEqual(displayedActions({ ...advice, snapshot: { ...advice.snapshot!, status: 'incomplete' } }), []);
  assert.deepEqual(displayedActions({ ...advice, latest_attempt: {
    id: 'failed', status: 'failed', finished_at_ms: 1_000_000, errors: ['Offline'],
  } }), []);
});

test('advice ages out or reaches kickoff while the tab stays open', () => {
  const advice = recent();
  assert.equal(adviceUsable(advice, 99_999), true);
  assert.equal(adviceUsable(advice, 100_000), false);
  const noKickoff = { ...advice, next_deadline: null, lineup: advice.lineup.map(slot => ({ ...slot, kickoff_ms: null })) };
  assert.equal(adviceUsable(noKickoff, 1_699_999), true);
  assert.equal(adviceUsable(noKickoff, 1_700_000), false);
  const lockedBeforeCapture = { ...advice, next_deadline: null,
    lineup: advice.lineup.map(slot => ({ ...slot, kickoff_ms: 800_000, locked_at_capture: true })) };
  assert.equal(adviceUsable(lockedBeforeCapture), true);
});

test('questionable status alone does not bench a player or invent an action', () => {
  const advice = recent();
  advice.actions = [];
  assert.equal(adviceUsable(advice), true);
  assert.deepEqual(displayedActions(advice), []);
  assert.equal(advice.lineup[0].player_id, 'p1');
  assert.equal(points(null), '—');
  assert.equal(points(0), '0.0');
});

test('server validity deadline expires source data before recency or kickoff limits', () => {
  const advice = recent();
  advice.freshness.valid_until_ms = advice.now_ms + 20_000;
  assert.equal(adviceUsable(advice, 19_999), true);
  assert.equal(adviceUsable(advice, 20_000), false);
  assert.deepEqual(displayedActions(advice, 20_000), []);
  advice.freshness.valid_until_ms = advice.now_ms;
  assert.equal(adviceUsable(advice), false);
  advice.freshness.valid_until_ms = null;
  assert.equal(adviceUsable(advice), true, 'recency and kickoff checks still apply without an additional validity limit');
});

test('an old successful response cannot revive guidance after a collection transition', () => {
  const advice = recent();
  assert.equal(confirmedAdvice(advice, 0, 2, 2, []), true);
  assert.equal(confirmedAdvice(advice, 0, 2, 3, []), false, 'wait for post-collection advice response');
  assert.equal(confirmedAdvice(advice, 0, 3, 3, []), true);
  assert.equal(confirmedAdvice(advice, 0, 3, 3, [
    { id: 'failed', kind: 'collect', status: 'failed', created_at_ms: 950_000, snapshot_id: null },
  ]), false, 'a failed newer update is not silently replaced by old advice');
});

test('a delayed advice response cannot extend a validity deadline', () => {
  const advice = recent();
  advice.freshness.valid_until_ms = advice.now_ms + 5000;
  const anchor = clockAnchor(advice, 0, 10_000);
  assert.equal(adviceUsable(advice, 10_000 - anchor), false);
});

test('primary deadline formatting includes the local weekday and timezone', () => {
  const time = Date.parse('2026-09-09T20:20:00Z');
  const detailed = dateTime(time, true);
  const parts = new Intl.DateTimeFormat(undefined, { weekday: 'short', timeZoneName: 'short' }).formatToParts(time);
  for (const part of parts.filter(part => ['weekday', 'timeZoneName'].includes(part.type))) {
    assert.ok(detailed.includes(part.value), `detailed deadline includes ${part.type}`);
    assert.ok(!dateTime(time).includes(part.value), 'compact row dates stay compact');
  }
  assert.equal(dateTime(null, true), 'Time unavailable');
});

test('availability groups players into explicit check-ins before kickoff', () => {
  const thursday = Date.parse('2026-09-11T00:35:00Z');
  const sunday = Date.parse('2026-09-13T17:00:00Z');
  const make = (name: string, kickoff: number) => ({
    player_id: name, name, status: 'Questionable', kickoff_ms: kickoff, note: 'Check',
  });
  const groups = availabilityChecks([
    make('Evans', thursday), make('Burden', sunday), make('Higgins', sunday), make('Odunze', sunday),
  ], Date.parse('2026-09-07T20:00:00Z'));
  assert.equal(groups.length, 2);
  assert.equal(groups[0].checkAt, Date.parse('2026-09-10T23:05:00Z'));
  assert.equal(groups[1].checkAt, Date.parse('2026-09-13T15:30:00Z'));
  assert.equal(groups[1].players.length, 3);
  assert.equal(groups[0].state, 'upcoming');
});

test('availability uses check-now and locked states rather than telling users to return too late', () => {
  const kickoff = 10_000_000;
  const player = { player_id: 'p', name: 'Player', status: 'Questionable', kickoff_ms: kickoff,
    check_at_ms: kickoff - 5_400_000, note: 'Check' };
  assert.equal(availabilityChecks([player], player.check_at_ms)[0].state, 'now');
  assert.equal(availabilityChecks([player], kickoff)[0].state, 'locked');
  assert.equal(availabilityChecks([{ ...player, kickoff_ms: null, check_at_ms: null }], 0)[0].state, 'unknown');
});

test('explicit decisions retain roster hold while lineup changes are needed', () => {
  const advice = recent();
  const decisions = displayedDecisions(advice);
  assert.equal(decisions?.lineup.action, 'change');
  assert.equal(decisions?.roster.action, 'hold');
  assert.equal(decisions?.roster.basis, 'conservative_default');
  assert.equal(displayedDecisions(advice, 0, true), null);
  assert.equal(displayedDecisions({ ...advice, mode: 'historical' }), null);
  assert.equal(displayedDecisions({ ...advice, decisions: undefined }), null);
  assert.equal(displayedDecisions(advice, 100_000), null, 'kickoff invalidates the decision');
});

test('a held lineup changes to check-now when the check window opens without mutating saved advice', () => {
  const advice = recent();
  assert.ok(advice.decisions);
  advice.decisions.lineup.action = 'hold';
  advice.injuries[0].check_at_ms = advice.now_ms + 10_000;
  assert.equal(displayedDecisions(advice, 9_999)?.lineup.action, 'hold');
  const due = displayedDecisions(advice, 10_000);
  assert.equal(due?.lineup.action, 'check');
  assert.match(due?.lineup.instruction || '', /Keep these starters in/);
  assert.match(due?.lineup.instruction || '', /replace anyone ruled out/);
  assert.equal(due?.roster.action, 'hold');
  assert.equal(advice.decisions.lineup.action, 'hold');
  advice.decisions.lineup.action = 'repair';
  assert.equal(displayedDecisions(advice, 10_000)?.lineup.action, 'repair');
});

test('an earlier roster review becomes due before the uncertain starter inactive window', () => {
  const advice = recent();
  assert.ok(advice.decisions);
  advice.decisions.lineup.action = 'hold';
  advice.injuries[0].check_at_ms = advice.now_ms + 50_000;
  advice.next_review_at_ms = advice.now_ms + 10_000;
  assert.equal(displayedDecisions(advice, 9_999)?.lineup.action, 'hold');
  assert.equal(displayedDecisions(advice, 10_000)?.lineup.action, 'check');
  assert.equal(availabilityChecks(advice.injuries, advice.now_ms + 10_000)[0].state, 'upcoming');
});

test('research results from an old selection cannot enter a different selected report', () => {
  const report = (capture: string, snapshot: string) => ({ capture_id: capture, report: { snapshot_id: snapshot } }) as Evaluation;
  const valid = report('selected', 'selected');
  assert.deepEqual(safeEvaluations([report('old', 'old'), report('selected', 'wrong'), valid], 'selected'), [valid]);
  assert.equal(recent().evaluation_id, 'baseline-evaluation');
});

test('history shows failed attempts without snapshots and deduplicates saved captures', () => {
  const snapshots = [{
    id: 'snapshot', status: 'complete' as const, week: 2, season: '2026', started_at_ms: 10, finished_at_ms: 20,
  }];
  const jobs = [
    { id: 'saved-job', kind: 'collect' as const, status: 'succeeded' as const, snapshot_id: 'snapshot', created_at_ms: 10 },
    { id: 'failed-job', kind: 'collect' as const, status: 'failed' as const, snapshot_id: null, created_at_ms: 30, error: 'Offline' },
    { id: 'research-job', kind: 'evaluate' as const, status: 'failed' as const, snapshot_id: null, created_at_ms: 40 },
  ];
  const items = historyItems(snapshots, jobs);
  assert.equal(items.length, 2);
  assert.equal(items[0].status, 'Update failed');
  assert.equal(items[0].error, 'Offline');
  assert.equal(items[1].snapshotId, 'snapshot');
  jobs[0] = { ...jobs[0], status: 'failed', error: 'Evaluation failed' } as typeof jobs[0];
  assert.equal(historyItems(snapshots, jobs).find(item => item.snapshotId === 'snapshot')?.status, 'Update failed');
});
