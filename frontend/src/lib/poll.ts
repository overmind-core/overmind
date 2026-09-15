/** Stops the poll loop rather than hammering a failing endpoint; any later
 *  successful fetch (a refocus refetch, say) resets the count and resumes it. */
const POLL_FAILURE_LIMIT = 3;

export function backoffPolling(
  query: { state: { fetchFailureCount: number } },
  interval: number | false | (() => number | false)
): number | false {
  if (query.state.fetchFailureCount >= POLL_FAILURE_LIMIT) return false;
  return typeof interval === "function" ? interval() : interval;
}
