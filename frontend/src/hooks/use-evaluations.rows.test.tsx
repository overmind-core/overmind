// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const listRows = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({
  default: { evalRunRows: { evalRunRowsList: listRows } },
}));

import { useEvalRunRowsQuery } from "./use-evaluations";

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
  expect(listRows).toHaveBeenNthCalledWith(2, {
    ordering: "row_index",
    page: 2,
    pageSize: 1000,
    run: "run",
  });
  expect(listRows).toHaveBeenCalledTimes(2);
  client.clear();
});
