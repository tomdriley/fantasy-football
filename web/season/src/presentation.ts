import type { Advice, Evaluation, Job, ManagerDecisions, Snapshot } from './types.ts';

export function adviceUsable(advice: Advice, elapsedMs = 0): boolean {
  if (advice.mode !== 'current' || !advice.freshness.usable
    || advice.freshness.state !== 'recent' || !advice.snapshot || advice.snapshot.status !== 'complete') return false;
  if (advice.latest_attempt && !['complete', 'succeeded'].includes(advice.latest_attempt.status)) return false;
  const now = advice.now_ms + Math.max(0, elapsedMs);
  const captured = advice.snapshot.as_of_ms;
  if (advice.freshness.valid_until_ms !== null && now >= advice.freshness.valid_until_ms) return false;
  if (now - captured >= advice.freshness.threshold_seconds * 1000) return false;
  return !advice.lineup.some(slot => slot.kickoff_ms !== null
    && slot.kickoff_ms > captured && slot.kickoff_ms <= now)
    && !(advice.next_deadline && advice.next_deadline.at_ms > captured && advice.next_deadline.at_ms <= now);
}

export function displayedActions(advice: Advice, elapsedMs = 0, updating = false) {
  return !updating && adviceUsable(advice, elapsedMs) ? advice.actions : [];
}

export function failedUpdateAfterAdvice(advice: Advice, jobs: Job[]): boolean {
  const latest = jobs.filter(job => job.kind === 'collect')
    .sort((a, b) => b.created_at_ms - a.created_at_ms)[0];
  return Boolean(latest && latest.status === 'failed'
    && (!advice.snapshot || latest.created_at_ms >= advice.snapshot.as_of_ms));
}

export function confirmedAdvice(
  advice: Advice, elapsedMs: number, dataRevision: number, requiredRevision: number, jobs: Job[],
): boolean {
  return dataRevision >= requiredRevision && !failedUpdateAfterAdvice(advice, jobs)
    && adviceUsable(advice, elapsedMs);
}

export const points = (value: number | null | undefined) => value !== null && value !== undefined && Number.isFinite(value)
  ? value.toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '—';
export const dateTime = (value: number | null | undefined, detailed = false) => value !== null && value !== undefined && Number.isFinite(value)
  ? new Date(value).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    ...(detailed ? { weekday: 'short', timeZoneName: 'short' } as const : {}),
  })
  : 'Time unavailable';
export const signedPoints = (value: number) => `${value > 0 ? '+' : ''}${points(value)}`;

export function availabilityChecks(injuries: Advice['injuries'], now: number) {
  const groups = new Map<string, {
    checkAt: number | null; kickoff: number | null;
    state: 'upcoming' | 'now' | 'locked' | 'unknown';
    players: Advice['injuries'];
  }>();
  for (const player of injuries) {
    const kickoff = player.kickoff_ms;
    // Earlier v1 responses omitted the explicit check time.
    const checkAt = player.check_at_ms ?? (kickoff === null ? null : kickoff - 90 * 60_000);
    const state = kickoff === null || checkAt === null ? 'unknown'
      : now >= kickoff ? 'locked' : now >= checkAt ? 'now' : 'upcoming';
    const key = `${checkAt}:${kickoff}`;
    const group = groups.get(key) ?? { checkAt, kickoff, state, players: [] };
    group.players.push(player);
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => (a.checkAt ?? Infinity) - (b.checkAt ?? Infinity));
}

export function displayedDecisions(advice: Advice, elapsedMs = 0, updating = false): ManagerDecisions | null {
  if (updating || !advice.decisions || !adviceUsable(advice, elapsedMs)) return null;
  const now = advice.now_ms + Math.max(0, elapsedMs);
  const due = (advice.next_review_at_ms !== null && advice.next_review_at_ms !== undefined
    && advice.next_review_at_ms <= now)
    || availabilityChecks(advice.injuries, now).some(check => check.state === 'now');
  if (!due || advice.decisions.lineup.action !== 'hold') return advice.decisions;
  return {
    ...advice.decisions,
    lineup: {
      ...advice.decisions.lineup, action: 'check',
      title: 'Keep your starters; check their status now',
      instruction: 'Keep these starters in. Update advice and check Sleeper now; replace anyone ruled out before kickoff.',
    },
  };
}

export function ageLabel(value: number | null, now: number) {
  if (value === null) return 'Update time unavailable';
  const minutes = Math.max(0, Math.floor((now - value) / 60_000));
  if (minutes === 0) return 'Updated just now';
  if (minutes < 60) return `Updated ${minutes} min ago`;
  if (minutes < 1440) return `Updated ${Math.floor(minutes / 60)} hr ago`;
  return `Updated ${dateTime(value)}`;
}

export function safeEvaluations(items: Evaluation[], id: string) {
  return items.filter(item => item.capture_id === id && item.report?.snapshot_id === id);
}

export function historyItems(snapshots: Snapshot[], jobs: Job[]) {
  return [
    ...snapshots.map(snapshot => {
      const job = jobs.find(item => item.kind === 'collect' && (item.result?.snapshot_id ?? item.snapshot_id) === snapshot.id);
      const failed = job?.status === 'failed' || ['incomplete', 'invalid'].includes(snapshot.status);
      return {
        key: snapshot.id,
        snapshotId: snapshot.id,
        time: snapshot.finished_at_ms ?? snapshot.started_at_ms,
        week: snapshot.week,
        status: failed ? 'Update failed' : snapshot.status === 'complete' ? 'Advice saved' : 'Update unfinished',
        error: failed ? job?.error || 'No new usable advice was obtained.' : null,
      };
    }),
    ...jobs.filter(job => job.kind === 'collect' && (!job.snapshot_id
      || !snapshots.some(snapshot => snapshot.id === job.snapshot_id))).map(job => ({
      key: job.id,
      snapshotId: job.result?.snapshot_id ?? job.snapshot_id,
      time: job.finished_at_ms ?? job.started_at_ms ?? job.created_at_ms,
      week: null,
      status: job.status === 'failed' ? 'Update failed'
        : job.status === 'succeeded' ? 'Advice saved' : 'Updating advice',
      error: job.status === 'failed' ? job.error || 'No new usable advice was obtained.' : null,
    })),
  ].sort((a, b) => b.time - a.time);
}
