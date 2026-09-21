// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExperimentSnapshot } from "@/components/finetuning/job-snapshot";
import type { DeployedModelsQueryParams } from "@/hooks/use-inference";
import type { FinetuningJobList } from "@/openapi";

const CAPABILITY_ID = "00000000-0000-4000-8000-000000000010";
const PROJECT_ID = "00000000-0000-4000-8000-000000000001";
const SERVING_ID = "ft:job-a:9f3c";

/** This run's deployment is the oldest of 30, so no default-sized project page
 *  can contain it. */
const DEPLOYED = [
  ...Array.from({ length: 29 }, (_, i) => ({
    capabilityId: "other-capability",
    id: `dm-${i}`,
    modelId: `ft:other-${i}`,
  })),
  { capabilityId: CAPABILITY_ID, id: "dm-target", modelId: SERVING_ID },
];

/** Mirrors the endpoint: scope server-side, then cut one page. */
const serverPage = ({ capability, pageSize }: DeployedModelsQueryParams) => {
  const scoped = capability ? DEPLOYED.filter((m) => m.capabilityId === capability) : DEPLOYED;
  return { count: scoped.length, results: scoped.slice(0, pageSize ?? 25) };
};

const mocks = vi.hoisted(() => ({ deployedParams: vi.fn() }));

vi.mock("@/hooks/use-inference", () => ({
  useDeployedModelsQuery: (params: DeployedModelsQueryParams) => {
    mocks.deployedParams(params);
    return { data: serverPage(params) };
  },
}));

vi.mock("@/hooks/use-finetuning", () => ({
  useCancelFinetuningJobMutation: () => ({ isPending: false, mutate: vi.fn() }),
  useFinetuningJobQuery: () => ({ data: job, error: null }),
  useFinetuningRunJobsQuery: () => ({ data: [job], isLoading: false }),
  useProjectDatasetsQuery: () => ({ data: { count: 0, results: [] } }),
  useRetryFinetuningJobMutation: () => ({ isPending: false, mutate: vi.fn() }),
}));

vi.mock("@/client", () => ({
  default: { finetuningJobs: { finetuningJobsLossCurvesRetrieve: vi.fn().mockResolvedValue({}) } },
}));

vi.mock("@/components/finetuning/judge-eval-table", () => ({
  classMetricsSourceLabel: () => "",
  JudgeEvalTableRow: ({
    deployedUuidByServingId,
    row,
    snapshots,
  }: {
    deployedUuidByServingId: Map<string, string>;
    row: { model_id: string };
    snapshots: ExperimentSnapshot[];
  }) => (
    <tr>
      <td
        data-deployed-uuid={deployedUuidByServingId.get(row.model_id) ?? ""}
        data-experiments={snapshots.length}
        data-testid="row"
      />
    </tr>
  ),
  latestClassMetricsOf: () => null,
}));

// The production action fetches the capability; it has its own tests.
vi.mock("@/components/finetuning/model-live-action", () => ({
  ModelLiveAction: () => null,
}));

// The @lobehub icon ESM subpaths behind the provider logo don't resolve under
// vitest's node resolver.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  ModelProviderChip: () => null,
}));

vi.mock("@/components/finetuning/finetuning-charts", () => ({
  ClassMetricsTable: () => null,
  ClassSeriesChart: () => null,
  ConfusionMatrixGrid: () => null,
  MetricSeriesChart: () => null,
  MultiSeriesChart: () => null,
}));

// Radix Tooltip needs its provider.
vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipContent: () => null,
  TooltipProvider: ({ children }: { children: ReactNode }) => <>{children}</>,
  TooltipTrigger: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import { TrainingMonitorPanel } from "./training-monitor";

const job = {
  baseModel: "meta/llama-3.1-8b",
  capability: CAPABILITY_ID,
  createdAt: new Date("2026-01-01T00:00:00Z"),
  dataset: "ds-1",
  groupId: "grp-1",
  id: "job-a",
  name: "meta/llama-3.1-8b · Billing traces",
  progress: {
    judge_evals: [
      {
        aggregate_score: 0.8,
        created_at: "2026-01-01T00:00:00Z",
        id: "ev-1",
        kind: "final",
        model_id: SERVING_ID,
        status: "succeeded",
      },
    ],
  },
  status: "succeeded",
} as unknown as FinetuningJobList;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("renders a shared incumbent once across a training group", () => {
  const groupJobs = ["job-a", "job-b"].map((id) => ({
    ...job,
    evalDataset: "data",
    evalIncumbentBefore: true,
    evalModelAfter: true,
    evalModelBefore: true,
    evalSet: "set",
    id,
    progress: {
      judge_evals: [
        {
          eval_run_id: "shared-run",
          id: `baseline-${id}`,
          kind: "baseline",
          model_id: "incumbent",
          status: "running",
        },
      ],
    },
  }));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={groupJobs}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  const rows = screen.getAllByTestId("row");
  expect(rows).toHaveLength(5);
  expect(rows.filter((row) => row.getAttribute("data-experiments") === "2")).toHaveLength(1);
});

describe("TrainingMonitorPanel deployed-model lookup", () => {
  it("resolves the run's deployed model from outside a default project page", () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <TrainingMonitorPanel
          focusJobId="job-a"
          groupJobs={[job]}
          onFocusJob={vi.fn()}
          projectId={PROJECT_ID}
        />
      </QueryClientProvider>
    );

    expect(screen.getByTestId("row").getAttribute("data-deployed-uuid")).toBe("dm-target");
    expect(mocks.deployedParams).toHaveBeenCalledWith({
      capability: CAPABILITY_ID,
      pageSize: 100,
      projectId: PROJECT_ID,
    });
  });
});
