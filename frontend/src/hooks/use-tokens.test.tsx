// @vitest-environment jsdom
// covers: console-logic
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useTokensList } from "./use-tokens";

const list = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({ default: { auth: { authApiKeysList: list } } }));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
it("finds project keys after the first account page", async () => {
  list
    .mockResolvedValueOnce({
      next: "page2",
      results: [{ id: "other", project: "other", scope: {} }],
    })
    .mockResolvedValueOnce({
      next: null,
      results: [{ id: "target", project: "project", scope: {} }],
    });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useTokensList("project"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.tokens.map((token) => token.id)).toEqual(["target"]);
  expect(list).toHaveBeenCalledTimes(2);
  client.clear();
});
it("keeps one entry per key when a key shifts between pages", async () => {
  const key = { id: "target", project: "project", scope: {} };
  list
    .mockResolvedValueOnce({ next: "page2", results: [key] })
    .mockResolvedValueOnce({ next: null, results: [key] });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { result } = renderHook(() => useTokensList("project"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.data?.tokens.map((token) => token.id)).toEqual(["target"]);
  client.clear();
});
