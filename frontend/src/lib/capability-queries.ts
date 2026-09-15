import type { QueryClient } from "@tanstack/react-query";

/** Refetch everything that renders capability state after a rename, a delete,
 * or a scan. Prefix keys cover all scoped variants. */
export function invalidateCapabilityQueries(queryClient: QueryClient) {
  for (const key of ["agent-graph", "capabilities", "capability-detail"]) {
    void queryClient.invalidateQueries({ queryKey: [key] });
  }
}
