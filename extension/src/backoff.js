export const BACKOFF_MIN_MS = 1000;
export const BACKOFF_MAX_MS = 30000;

export function nextBackoffDelay(attempt, rand = Math.random) {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** attempt);
  return base * (0.8 + 0.4 * rand());
}
