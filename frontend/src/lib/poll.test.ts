// covers: console-logic
import { QueryClient } from "@tanstack/react-query";
import { expect, it } from "vitest";

import { backoffPolling } from "./poll";

it("stops after the retry budget and resumes after a successful explicit refetch", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: 1, retryDelay: 0 } } });
  await client
    .fetchQuery({ queryFn: () => Promise.reject(new Error("offline")), queryKey: ["poll"] })
    .catch(() => {});
  const query = client.getQueryCache().find({ queryKey: ["poll"] })!;
  expect(query.state.fetchFailureCount).toBe(2);
  expect(backoffPolling(query, 5000)).toBe(false);
  await client.fetchQuery({ queryFn: async () => "recovered", queryKey: ["poll"] });
  expect(backoffPolling(query, 5000)).toBe(5000);
  client.clear();
});
