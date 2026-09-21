import type { UseQueryOptions } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  deployments: vi.fn(),
  list: vi.fn(),
  retrieve: vi.fn(),
  runsList: vi.fn(),
}));

let captured: UseQueryOptions | undefined;

vi.mock("@tanstack/react-query", () => ({
  useMutation: () => ({}),
  useQuery: (options: UseQueryOptions) => {
    captured = options;
    return {};
  },
  useQueryClient: () => ({}),
}));

vi.mock("@/client", () => ({
  default: {
    deployedModels: { deployedModelsList: mocks.deployments },
    finetuningJobs: {
      finetuningJobsList: mocks.list,
      finetuningJobsRetrieve: mocks.retrieve,
      finetuningJobsRunsList: mocks.runsList,
    },
  },
}));

import {
  useFinetuningRunJobsQuery,
  useFinetuningRunsQuery,
  useTrainingBenchmarksQuery,
} from "./use-finetuning";

const optionsOf = (call: () => unknown): UseQueryOptions => {
  captured = undefined;
  call();
  if (!captured) throw new Error("hook did not call useQuery");
  return captured;
};

/** `queryFn` widens to include `skipToken`; these hooks always set a function. */
const fetchWith = (options: UseQueryOptions) => {
  const queryFn = options.queryFn;
  if (typeof queryFn !== "function") throw new Error("queryFn is not a function");
  return queryFn({} as never);
};

beforeEach(() => {
  mocks.deployments.mockReset();
  mocks.list.mockReset();
  mocks.retrieve.mockReset();
  mocks.runsList.mockReset();
});

describe("useTrainingBenchmarksQuery", () => {
  it("loads every page of ready trained project models and excludes eval infrastructure", async () => {
    const first = { finetuningJobId: "job-first", modelId: "ft-first", status: "ready" };
    const last = { finetuningJobId: "job-last", modelId: "ft-last", status: "ready" };
    mocks.deployments
      .mockResolvedValueOnce({
        next: "/api/deployed-models/?page=2",
        results: [
          first,
          { finetuningJobId: null, modelId: "base", status: "ready" },
          { finetuningJobId: "job-failed", modelId: "failed", status: "failed" },
        ],
      })
      .mockResolvedValueOnce({ next: null, results: [last] });
    const options = optionsOf(() => useTrainingBenchmarksQuery("project"));
    expect(await fetchWith(options)).toEqual([first, last]);
    expect(options.queryKey).toEqual(["training-benchmarks", "project"]);
    expect(mocks.deployments).toHaveBeenNthCalledWith(1, {
      page: 1,
      pageSize: 100,
      project: "project",
      status: "ready",
    });
    expect(mocks.deployments).toHaveBeenNthCalledWith(2, {
      page: 2,
      pageSize: 100,
      project: "project",
      status: "ready",
    });
  });
});

describe("useFinetuningRunsQuery", () => {
  it("keys every param and sends none of the empty ones", async () => {
    const options = optionsOf(() =>
      useFinetuningRunsQuery({ page: 2, pageSize: 25, projectId: "proj-1", search: "   " })
    );

    expect(options.queryKey).toEqual(["finetuning-runs", "proj-1", 2, 25, null, null, null, null]);

    await fetchWith(options);
    expect(mocks.runsList).toHaveBeenCalledWith({
      baseModel: undefined,
      dataset: undefined,
      page: 2,
      pageSize: 25,
      project: "proj-1",
      search: undefined,
      status: undefined,
    });
  });

  it("carries the filters into both the key and the request", async () => {
    const options = optionsOf(() =>
      useFinetuningRunsQuery({
        baseModel: "meta/llama-3.1-8b",
        dataset: "ds-1",
        page: 1,
        pageSize: 25,
        projectId: "proj-1",
        search: " nightly ",
        status: "failed",
      })
    );

    expect(options.queryKey).toEqual([
      "finetuning-runs",
      "proj-1",
      1,
      25,
      "nightly",
      "failed",
      "ds-1",
      "meta/llama-3.1-8b",
    ]);

    await fetchWith(options);
    expect(mocks.runsList).toHaveBeenCalledWith(
      expect.objectContaining({
        baseModel: "meta/llama-3.1-8b",
        dataset: "ds-1",
        search: "nightly",
        status: "failed",
      })
    );
  });
});

describe("useFinetuningRunJobsQuery", () => {
  it("asks for the run's jobs by group, without paging the project's history", async () => {
    mocks.list.mockResolvedValue({ results: [{ id: "job-a" }, { id: "job-b" }] });

    const options = optionsOf(() => useFinetuningRunJobsQuery("grp-1"));

    expect(options.queryKey).toEqual(["finetuning-run-jobs", "grp-1"]);
    expect(await fetchWith(options)).toEqual([{ id: "job-a" }, { id: "job-b" }]);
    expect(mocks.list).toHaveBeenCalledWith({ groupId: "grp-1", ordering: "-created_at" });
    expect(mocks.retrieve).not.toHaveBeenCalled();
  });

  it("falls back to the lone job when the run key is a job id", async () => {
    mocks.list.mockResolvedValue({ results: [] });
    mocks.retrieve.mockResolvedValue({ id: "job-legacy" });

    const options = optionsOf(() => useFinetuningRunJobsQuery("job-legacy"));

    expect(await fetchWith(options)).toEqual([{ id: "job-legacy" }]);
    expect(mocks.retrieve).toHaveBeenCalledWith({ id: "job-legacy" });
  });
});
