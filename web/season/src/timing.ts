function serverTime(value: unknown): number | null {
  if (!value || typeof value !== 'object' || !('now_ms' in value)) return null;
  return typeof value.now_ms === 'number' && Number.isFinite(value.now_ms) ? value.now_ms : null;
}

// Anchor at request start, conservatively counting transit/processing time.
// Revalidation must not move an existing advice clock backwards.
export function clockAnchor(
  data: unknown, startedAt: number, arrivedAt: number,
  previousData: unknown = null, previousAnchor = 0,
): number {
  const next = serverTime(data);
  const previous = serverTime(previousData);
  if (next === null || previous === null) return startedAt;
  const previousNow = previous + Math.max(0, arrivedAt - previousAnchor);
  const nextNow = next + Math.max(0, arrivedAt - startedAt);
  return arrivedAt - Math.max(0, Math.max(previousNow, nextNow) - next);
}
