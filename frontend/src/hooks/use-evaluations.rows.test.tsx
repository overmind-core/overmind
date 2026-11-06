// @vitest-environment jsdom
// covers: console-logic
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const listRows = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({
  default: { evalRunRows: { evalRunRowsList: listRows } },
}));

import { evalRowsPollingInterval, useEvalRunRowsQuery } from "./use-evaluations";

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("loads every server page so local filtering and pagination retain later rows", async () => {
  listRows
    .mockResolvedValueOnce({ count: 2, next: "next-page", results: [{ id: 1, score: 1 }] })
    .mockResolvedValueOnce({ count: 2, next: null, results: [{ id: 2, score: 0 }] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useEvalRunRowsQuery("run"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.results).toEqual([
    { id: 1, score: 1 },
    { id: 2, score: 0 },
  ]);
  expect(listRows).toHaveBeenNthCalledWith(
    2,
    {
      ordering: "row_index",
      page: 2,
      pageSize: 1000,
      run: "run",
    },
    { signal: expect.any(AbortSignal) }
  );
  expect(listRows).toHaveBeenCalledTimes(2);
  client.clear();
});

it("reduces refresh frequency as full result sets grow and stops on errors", () => {
  const small = evalRowsPollingInterval(
    { state: { data: { count: 10 }, status: "success" } },
    true
  );
  const large = evalRowsPollingInterval(
    { state: { data: { count: 10_000 }, status: "success" } },
    true
  );
  expect(large).toBeGreaterThan(small as number);
  expect(
    evalRowsPollingInterval({ state: { data: { count: 10_000 }, status: "error" } }, true)
  ).toBe(false);
});
