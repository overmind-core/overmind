// @vitest-environment jsdom

import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useCreateDatasetMutation, useCreateDatasetSplitMutation } from "./use-datasets";

const mocks = vi.hoisted(() => ({ create: vi.fn(), split: vi.fn() }));
vi.mock("@/client", () => ({
  default: { datasets: { datasetsCreate: mocks.create, datasetsSplitCreate: mocks.split } },
}));

let queryClient: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

beforeEach(() => {
  vi.resetAllMocks();
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  mocks.create.mockResolvedValue({ id: "dataset" });
  mocks.split.mockResolvedValue({ eval: { id: "eval" }, train: { id: "train" } });
});
afterEach(() => {
  cleanup();
  queryClient.clear();
});

it.each([
  undefined,
  null,
  "capability-id",
])("preserves capability choice %s when creating", async (capabilityId) => {
  const { result } = renderHook(useCreateDatasetMutation, { wrapper });
  await act(async () => {
    await result.current.mutateAsync({
      capabilityId,
      intent: "eval",
      name: "Test",
      projectId: "project",
      source: { uploads: ["upload"] },
    });
  });
  expect(mocks.create.mock.calls[0][0].datasetCreateRequest).toEqual({
    capability: capabilityId,
    intent: "eval",
    name: "Test",
    project: "project",
    source: { uploads: ["upload"] },
  });
});

it.each([
  undefined,
  null,
  "capability-id",
])("preserves capability choice %s when splitting", async (capabilityId) => {
  const { result } = renderHook(useCreateDatasetSplitMutation, { wrapper });
  await act(async () => {
    await result.current.mutateAsync({
      capabilityId,
      evalPercent: 30,
      name: "Test",
      position: "tail",
      projectId: "project",
      source: { uploads: ["upload"] },
    });
  });
  expect(mocks.split.mock.calls[0][0].datasetSplitCreateRequest.capability).toBe(capabilityId);
});
