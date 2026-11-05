/** A fetch exhausts its configured retries before entering error state.
 * Refocus or an explicit refetch can recover it and resume polling. */
export function backoffPolling(
  query: { state: { status: string } },
  interval: number | false | (() => number | false)
): number | false {
  if (query.state.status === "error") return false;
  return typeof interval === "function" ? interval() : interval;
}
