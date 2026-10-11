// @vitest-environment jsdom
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterContextProvider,
} from "@tanstack/react-router";
import { act, cleanup, render as renderComponent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExperimentSnapshot } from "@/components/finetuning/job-snapshot";
import { buildExperimentSnapshot } from "@/components/finetuning/job-snapshot";
import type { LossCurveData } from "@/hooks/use-finetuning";
import type { DeployedModelsQueryParams } from "@/hooks/use-inference";
import { stageDetailLine, stageLabel, stageStatusLine } from "@/lib/finetuning-progress";
import type { FinetuningJobList } from "@/openapi";

const CAPABILITY_ID = "00000000-0000-4000-8000-000000000010";
const PROJECT_ID = "00000000-0000-4000-8000-000000000001";
const SERVING_ID = "ft:job-a:9f3c";

function render(children: ReactNode) {
  const router = createRouter({ history: createMemoryHistory(), routeTree: createRootRoute() });
  return renderComponent(<RouterContextProvider router={router}>{children}</RouterContextProvider>);
}

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
  ModelLiveAction: ({ children }: { children: ReactNode }) => <>{children}</>,
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

import { StageProgress } from "./stage-progress";
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

it.each([
  ["decision_cross_entropy", "Validation cross entropy"],
  ["decision_supervised", "Validation objective loss"],
])("shows native %s validation progress without chat token accuracy", (objective, lossLabel) => {
  const nativeJobs = ["Pilot", "Full corpus"].map((name, i) => ({
    ...job,
    id: `native-${i}`,
    name,
    progress: {
      diagnostics: {
        completed: 600,
        heartbeat_at: Date.now() / 1000,
        stage: "initial_validation",
        total: 1200,
        unit: "decisions",
      },
      eval_history: [
        {
          brier: 0.4,
          decisions: 1200,
          eval_loss: 1.2,
          hard_label_accuracy: 0.65,
          hard_label_decisions: 1000,
          step: 0,
        },
      ],
      phase: "training",
    },
    status: "running",
    trainingContract: { objective },
  })) as unknown as FinetuningJobList[];
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={nativeJobs}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  expect(screen.getAllByText("Full corpus").length).toBeGreaterThan(0);
  expect(screen.getAllByText(/600 \/ 1,200 decisions/)).toHaveLength(2);
  expect(screen.getAllByText("Validating base model")).toHaveLength(2);
  expect(screen.queryByText("Token accuracy")).toBeNull();
  expect(screen.getAllByText(lossLabel).length).toBeGreaterThan(0);
  expect(screen.getAllByText("1.2000")).toHaveLength(2);
});

it("shows the frozen native plan beside its training job", () => {
  const native = {
    ...job,
    nativeEvaluation: {
      calibration: {},
      calls: {},
      config: {
        suites: { calibration: { cell: "cal", rows: 12 }, final: { cell: "final", rows: 48 } },
      },
      error: "",
      id: "native-plan",
      results: {},
      state: "waiting_for_checkpoint",
    },
    trainingContract: { objective: "decision_cross_entropy" },
  } as unknown as FinetuningJobList;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TrainingMonitorPanel
        focusJobId={native.id}
        groupJobs={[native]}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  expect(screen.getByText("Scheduled after checkpoint verification")).toBeTruthy();
  expect(screen.getByText(/48 decisions/)).toBeTruthy();
  expect(screen.queryByText(/No eval dataset or eval set linked/)).toBeNull();
});

it.each([
  { range: [913, 2184], remaining: "15–37 min training remaining (estimate)" },
  { range: null, remaining: null },
  { range: [-1, 2184], remaining: null },
  { range: [2184, 913], remaining: null },
])("shows elapsed time and only a valid range in minutes: $range", ({ range, remaining }) => {
  const running = {
    ...job,
    progress: { elapsed_seconds: 728, eta_range_seconds: range, eta_seconds: 60 },
    status: "running",
  } as unknown as FinetuningJobList;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={[running]}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  const label = screen.getByText(/elapsed/).textContent;
  expect(label).toContain("12m 08s elapsed");
  if (remaining) expect(label).toContain(remaining);
  else expect(label).not.toContain("remaining");
  expect(label).not.toContain("~1m");
});

it("describes local submission recovery without claiming a provider stage is missing", () => {
  const unresolved = {
    ...job,
    progress: {},
    status: "submission_unknown",
  } as unknown as FinetuningJobList;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={[unresolved]}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  expect(screen.getByText("Reconciling training submission")).toBeTruthy();
  expect(screen.queryByText(/provider stage not reported/)).toBeNull();
});

it("explains model loading and exposes the saved stage history", () => {
  const loading = {
    ...job,
    progress: {
      activity: [
        { message: "Checking base model weights", ts: 1 },
        { message: "Loading base model onto GPU", ts: 2 },
      ],
      diagnostics: {
        heartbeat_at: Date.now() / 1000 - 5,
        last_progress_at: Date.now() / 1000 - 120,
        stage: "loading_model",
        stage_started_at: Date.now() / 1000 - 120,
      },
      phase: "training",
      stage: "loading_model",
    },
    status: "running",
  } as unknown as FinetuningJobList;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={[loading]}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  expect(screen.getByText("Base weights ready · loading onto training GPU")).toBeTruthy();
  expect(screen.getByText("Loading model")).toBeTruthy();
  expect(screen.getByText(/2m 0s in this stage/)).toBeTruthy();
  expect(screen.getByText(/5s since worker heartbeat/)).toBeTruthy();
  expect(screen.getByText(/Loading percentage not reported/)).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "Show activity log" }).length).toBeGreaterThan(0);
});

it("shows the actual startup substage and keeps absent heartbeat explicit", () => {
  const loading = {
    ...job,
    progress: {
      diagnostics: { stage: "configuring_adapters" },
      stage: "configuring_adapters",
    },
    status: "running",
  } as unknown as FinetuningJobList;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={[loading]}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  expect(screen.getByText("Configuring adapters")).toBeTruthy();
  expect(screen.getByText(/Worker heartbeat not reported/)).toBeTruthy();
  expect(screen.queryByText(/Base weights ready/)).toBeNull();
});

it("does not present the last running step as the outcome of a cancelled job", () => {
  const cancelled = {
    ...job,
    progress: {
      activity: [{ kind: "stage", message: "Training started", ts: 1 }],
      phase: "training",
      stage: "training",
    },
    status: "cancelled",
  } as unknown as FinetuningJobList;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <TrainingMonitorPanel
        focusJobId={null}
        groupJobs={[cancelled]}
        onFocusJob={vi.fn()}
        projectId={PROJECT_ID}
      />
    </QueryClientProvider>
  );
  expect(screen.queryByText("Training started")).toBeNull();
});

it("keeps row-level preparation progress when the live metrics response has no preparation field", () => {
  const preparing = {
    ...job,
    progress: {
      preparation: { completed_rows: 250, state: "running", total_rows: 1000 },
    },
    status: "preparing",
  } as unknown as FinetuningJobList;
  const curves = { progress: { diagnostics: {} } } as LossCurveData;
  const snapshot = buildExperimentSnapshot(preparing, curves, "var(--chart-1)");
  expect(stageStatusLine(snapshot.liveProgress)).toContain("250 / 1,000 rows");
});

it("shows measured preparation completion without treating an upload as finished tokenization", () => {
  expect(
    stageStatusLine({
      preparation: { completed_rows: 250, stage: "tokenizing", state: "running", total_rows: 1000 },
    })
  ).toBe("Tokenizing · 250 / 1,000 rows (25%)");
  expect(
    stageStatusLine({
      preparation: {
        completed_rows: 1000,
        stage: "uploading",
        state: "starting",
        total_rows: 1000,
      },
    })
  ).toBe("Uploading training data · 1,000 rows prepared");
  expect(stageLabel({ stage: "loading_model" })).toBe("Loading model");
  expect(stageDetailLine({ stage: "loading_model" })).toBe(
    "Base weights ready · loading onto training GPU"
  );
});

it("shows measured transfer progress and ages it without inventing more completed work", () => {
  vi.useFakeTimers();
  vi.setSystemTime(200_000);
  try {
    render(
      <StageProgress
        progress={{
          diagnostics: {
            completed: 250,
            last_progress_at: 190,
            source_at: 190,
            stage: "selecting_prepared_rows",
            stage_started_at: 150,
            total: 1000,
            unit: "rows",
          },
          stage: "transferring",
        }}
      />
    );
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
    expect(screen.getByText(/10s since last progress/)).toBeTruthy();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.getByText(/1m 10s since last progress/)).toBeTruthy();
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("25");
  } finally {
    vi.useRealTimers();
  }
});

it("does not invent a transfer percentage when the worker provides no measurements", () => {
  render(<StageProgress progress={{ stage: "transferring" }} />);
  expect(screen.queryByRole("progressbar")).toBeNull();
  expect(screen.getByText(/Progress not reported/)).toBeTruthy();
});

it("identifies upload counts as acknowledged bytes rather than live network progress", () => {
  const progress = {
    diagnostics: {
      completed: 1024,
      files_completed: 1,
      files_total: 2,
      measurement: "provider_acknowledged_files",
      stage: "uploading_selections",
      total: 2048,
      unit: "bytes",
    },
    stage: "transferring",
  };
  expect(stageDetailLine(progress)).toContain("1 KiB / 2 KiB acknowledged");
  expect(stageDetailLine(progress)).toContain("1 / 2 files");
  expect(stageLabel(progress)).toBe("Transferring data");
});
