// @vitest-environment jsdom
// covers: console-logic
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

const deletes = vi.hoisted(() => ({ decision: vi.fn(), generative: vi.fn() }));
vi.mock("@/client", () => ({
  default: {
    evalRuns: { evalRunsDestroy: deletes.generative },
    nativeEvaluations: { nativeEvaluationsDestroy: deletes.decision },
  },
}));

import { useDeleteEvalRunMutation } from "./use-evaluations";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("dispatches each format to its generated endpoint and invalidates lists, facets and detail", async () => {
  const client = new QueryClient();
  for (const key of [
    ["eval-runs", "project"],
    ["eval-runs", "all", "project"],
    ["eval-runs", "datasets", "project"],
    ["decision-comparison", "native"],
    ["eval-run", "chat"],
  ])
    client.setQueryData(key, {});
  const { result } = renderHook(() => useDeleteEvalRunMutation("project"), {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await act(() => result.current.mutateAsync({ id: "native", kind: "decision" }));
  await act(() => result.current.mutateAsync({ id: "chat", kind: "generative" }));
  expect(deletes.decision).toHaveBeenCalledExactlyOnceWith({ id: "native" });
  expect(deletes.generative).toHaveBeenCalledExactlyOnceWith({ id: "chat" });
  expect(client.getQueryData(["decision-comparison", "native"])).toBeUndefined();
  expect(client.getQueryData(["eval-run", "chat"])).toBeUndefined();
  expect(client.getQueryState(["eval-runs", "project"])?.isInvalidated).toBe(true);
  expect(client.getQueryState(["eval-runs", "datasets", "project"])?.isInvalidated).toBe(true);
});
