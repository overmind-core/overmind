// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ deployedModelsList: vi.fn() }));

vi.mock("@/client", () => ({
  default: { deployedModels: { deployedModelsList: mocks.deployedModelsList } },
}));

import { deployedModelsPollInterval, useDeployedModelsQuery } from "./use-inference";

let queryClient: QueryClient;

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
);

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  mocks.deployedModelsList.mockReset();
});

const renderList = async (params: Parameters<typeof useDeployedModelsQuery>[0]) => {
  const { result } = renderHook(() => useDeployedModelsQuery(params), { wrapper });
  await waitFor(() => {
    expect(result.current.isSuccess).toBe(true);
  });
  return result;
};

describe("useDeployedModelsQuery", () => {
  it("sends the project scope and the page as request params", async () => {
    mocks.deployedModelsList.mockResolvedValue({ count: 0, results: [] });

    await renderList({ page: 3, pageSize: 10, projectId: "proj-1" });

    expect(mocks.deployedModelsList).toHaveBeenCalledWith({
      capability: undefined,
      page: 3,
      pageSize: 10,
      project: "proj-1",
    });
  });

  it("sends the capability scope as a request param", async () => {
    mocks.deployedModelsList.mockResolvedValue({ count: 0, results: [] });

    await renderList({ capability: "capability-1", pageSize: 100, projectId: "proj-1" });

    expect(mocks.deployedModelsList).toHaveBeenCalledWith({
      capability: "capability-1",
      page: undefined,
      pageSize: 100,
      project: "proj-1",
    });
  });

  it("returns the page untouched so count keeps describing results", async () => {
    // `project` deliberately disagrees with the scope: re-filtering the response
    // client-side would drop this row while leaving `count` at 42.
    const page = { count: 42, results: [{ id: "m-1", project: "proj-other", status: "ready" }] };
    mocks.deployedModelsList.mockResolvedValue(page);

    const result = await renderList({ page: 1, pageSize: 25, projectId: "proj-1" });

    expect(result.current.data).toEqual(page);
  });
});

describe("deployedModelsPollInterval", () => {
  it("stops once every model on the page has settled", () => {
    expect(
      deployedModelsPollInterval([{ status: "ready" }, { status: "failed" }, { status: "deleted" }])
    ).toBe(false);
    expect(deployedModelsPollInterval([])).toBe(false);
  });

  it("polls while any model is mid-lifecycle, and before the first response", () => {
    expect(deployedModelsPollInterval([{ status: "ready" }, { status: "warming" }])).toBe(10_000);
    // An unknown status counts as unsettled — a new backend state must not
    // silently freeze the poll.
    expect(deployedModelsPollInterval([{ status: "rehydrating" }])).toBe(10_000);
    expect(deployedModelsPollInterval(undefined)).toBe(10_000);
  });
});
