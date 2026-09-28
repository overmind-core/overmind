// @vitest-environment jsdom
import type { CellContext, ColumnDef } from "@tanstack/react-table";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FinetuningJobList, FinetuningJobRun } from "@/openapi";

const mocks = vi.hoisted(() => ({
  datasets: [{ id: "ds-1", name: "Billing traces" }] as { id: string; name: string }[],
  models: ["meta/llama-3.1-8b"] as string[],
  peeked: vi.fn(),
  runsParams: vi.fn(),
}));

// Radix Select needs a layout engine jsdom lacks.
vi.mock("@/components/ui/select", () => ({
  Select: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <div data-value={value}>{children}</div>
  ),
  SelectTrigger: () => null,
  SelectValue: () => null,
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
  // `ListToolbar` wraps itself in a provider, so the mock has to carry one too.
  TooltipProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

// The table isn't under test: render only the toolbar and the peek cell.
vi.mock("@/components/ui/data-table", () => ({
  DataTable: ({
    columns,
    data,
    toolbar,
  }: {
    columns: ColumnDef<FinetuningJobRun>[];
    data?: { count: number; results: FinetuningJobRun[] };
    toolbar: React.ReactNode;
  }) => {
    const peek = columns.find((c) => c.id === "peek");
    const cell = peek?.cell as (ctx: CellContext<FinetuningJobRun, unknown>) => React.ReactNode;
    return (
      <div>
        {toolbar}
        {(data?.results ?? []).map((run) => (
          <div key={run.runId}>
            {cell({ row: { original: run } } as CellContext<FinetuningJobRun, unknown>)}
          </div>
        ))}
      </div>
    );
  },
}));

vi.mock("@tanstack/react-router", () => ({ Link: () => null, useNavigate: () => vi.fn() }));

// The @lobehub icon ESM subpaths behind the provider logo don't resolve under
// vitest's node resolver.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  getProviderIcon: () => undefined,
  ModelProviderChip: () => null,
  ProviderLogo: () => null,
}));

// `useDebouncedValue` is deliberately NOT mocked: the mount guard below holds
// only because the real hook seeds with the incoming value.

const job = (id: string, over: Partial<FinetuningJobList> = {}) =>
  ({
    baseModel: "meta/llama-3.1-8b",
    costUsd: null,
    createdAt: new Date("2026-01-01T00:00:00Z"),
    dataset: "ds-1",
    groupId: "grp-solo",
    id,
    name: "meta/llama-3.1-8b · Billing traces",
    progress: null,
    status: "succeeded",
    ...over,
  }) as unknown as FinetuningJobList;

const runs: FinetuningJobRun[] = [
  { groupId: "grp-solo", jobs: [job("job-solo")], runId: "grp-solo" },
  {
    groupId: "grp-pair",
    jobs: [
      job("job-a", { groupId: "grp-pair" }),
      job("job-b", { baseModel: "qwen/qwen3-8b", groupId: "grp-pair" }),
    ],
    runId: "grp-pair",
  },
];

vi.mock("@/hooks/use-finetuning", () => ({
  useFinetuningRunBaseModelsQuery: () => ({ data: mocks.models }),
  useFinetuningRunDatasetsQuery: () => ({ data: mocks.datasets }),
  useFinetuningRunsQuery: (params: unknown) => {
    mocks.runsParams(params);
    return { data: { count: 2, results: runs }, isLoading: false, refetch: vi.fn() };
  },
}));

import { JobsHistory } from "./jobs-history";

afterEach(() => {
  cleanup();
  mocks.datasets = [{ id: "ds-1", name: "Billing traces" }];
  mocks.models = ["meta/llama-3.1-8b"];
});

type Filters = Parameters<typeof JobsHistory>[0]["filters"];

const renderHistory = (
  filters: Partial<Filters> = {},
  props: Partial<Parameters<typeof JobsHistory>[0]> = {}
) =>
  render(
    <JobsHistory
      filters={{
        ft_dataset: "all",
        ft_model: "all",
        ft_search: "",
        ft_status: "all",
        ...filters,
      }}
      onMonitorRun={vi.fn()}
      onPeekJob={mocks.peeked}
      onSearchChange={vi.fn()}
      page={1}
      pageSize={25}
      projectId="proj-1"
      {...props}
    />
  );

const optionValues = () =>
  [...document.querySelectorAll("[data-value]")].map((el) => el.getAttribute("data-value"));

describe("JobsHistory filters", () => {
  it("sends the URL's filters and page to the runs query", () => {
    renderHistory(
      {
        ft_dataset: "ds-1",
        ft_model: "meta/llama-3.1-8b",
        ft_search: "nightly",
        ft_status: "failed",
      },
      { page: 3, pageSize: 10 }
    );

    expect(mocks.runsParams).toHaveBeenLastCalledWith({
      baseModel: "meta/llama-3.1-8b",
      dataset: "ds-1",
      page: 3,
      pageSize: 10,
      projectId: "proj-1",
      search: "nightly",
      status: "failed",
    });
  });

  it("drops an 'all' filter instead of sending it as a value", () => {
    renderHistory();

    expect(mocks.runsParams).toHaveBeenLastCalledWith(
      expect.objectContaining({ baseModel: undefined, dataset: undefined, status: undefined })
    );
  });

  it("offers every job status the API can return, in lifecycle order", () => {
    renderHistory();

    // Every select's options, in render order: status, then dataset, then model.
    expect(optionValues()).toEqual([
      "all",
      "queued",
      "preparing",
      "running",
      "deploying",
      "succeeded",
      "failed",
      "cancelled",
      "all",
      "ds-1",
      "all",
      "meta/llama-3.1-8b",
    ]);
  });

  it("keeps a dataset the facet doesn't carry selectable", () => {
    mocks.datasets = [];

    renderHistory({ ft_dataset: "ds-gone" });

    expect(document.querySelector("[data-value='ds-gone']")?.textContent).toBe("Unknown dataset");
  });

  it("keeps a base model the facet doesn't carry selectable", () => {
    mocks.models = [];

    renderHistory({ ft_model: "mistral/retired-7b" });

    expect(document.querySelector("[data-value='mistral/retired-7b']")?.textContent).toBe(
      "Retired 7B"
    );
  });
});

describe("JobsHistory deep link", () => {
  it("doesn't rewrite a deep-linked page + search on mount", () => {
    const onSearchChange = vi.fn();

    renderHistory({ ft_search: "nightly" }, { onSearchChange, page: 3 });

    expect(onSearchChange).not.toHaveBeenCalled();
  });
});

describe("JobsHistory run rows", () => {
  it("offers Peek for the one-job run only", () => {
    renderHistory();

    const peeks = screen.getAllByRole("button", { name: /quick peek/i });
    expect(peeks).toHaveLength(1);

    peeks[0].click();
    expect(mocks.peeked).toHaveBeenCalledWith("job-solo");
  });
});
