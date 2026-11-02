// @vitest-environment jsdom
// covers: measured-facts
import type { ReactNode } from "react";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterContextProvider,
} from "@tanstack/react-router";
import {
  cleanup,
  fireEvent,
  render as renderComponent,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import type { FinetuningJobList, NativeEvaluation } from "@/openapi";
import { NativeEvaluationTable } from "./native-evaluation-table";

afterEach(cleanup);

function render(children: ReactNode) {
  const router = createRouter({ history: createMemoryHistory(), routeTree: createRootRoute() });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderComponent(children, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>
        <RouterContextProvider router={router}>{children}</RouterContextProvider>
      </QueryClientProvider>
    ),
  });
}

const participants = ["0.8B", "2B", "4B"].map((size, index) => ({
  job: `job-${index}`,
  key: `candidate-${index}`,
  kind: "trained",
  model: `Qwen/Qwen3.5-${size}`,
  name: `Qwen ${size} Titanic`,
}));
const jobs = participants.map((p) => ({
  baseModel: p.model,
  groupId: "experiment",
  id: p.job,
  name: p.name,
  status: "succeeded",
})) as FinetuningJobList[];

function plan(overrides: Partial<NativeEvaluation> = {}): NativeEvaluation {
  return {
    calibration: {},
    calibrationCell: "calibration-frozen",
    calibrationSuite: null,
    calls: {},
    config: {
      baseline: "candidate-0",
      participants,
      suites: {
        calibration: { cell: "calibration-frozen", dataset: "calibration-dataset", rows: 89 },
        final: { cell: "final-frozen", dataset: "final-dataset", rows: 89 },
      },
    },
    createdAt: new Date("2026-10-11T12:00:00Z"),
    deletedAt: null,
    error: "",
    finalCell: "final-frozen",
    finalSuite: { datasetName: "Titanic final" },
    id: "benchmark-plan",
    job: null,
    name: "Titanic benchmark",
    project: "project",
    requestKey: "experiment:experiment",
    results: {},
    state: "waiting_for_checkpoint",
    updatedAt: new Date("2026-10-11T12:00:00Z"),
    ...overrides,
  } as NativeEvaluation;
}

function coverage(accuracy: number | null, expected: number, scored = expected) {
  return {
    expected,
    incompatible_inputs: 0,
    invalid_predictions: 0,
    metrics: {
      accuracy: {
        decisions: accuracy === null ? 0 : scored,
        groups: scored,
        interval_95: [0.5, 0.9],
        mean: accuracy,
      },
      brier: { decisions: scored, groups: scored, mean: 0.21 },
      cross_entropy: { decisions: scored, groups: scored, mean: 0.42 },
    },
    missing_predictions: expected - scored,
    scored,
  };
}

function completedPlan() {
  const baseline = { benchmarks: { A: coverage(0.9, 10), B: coverage(0.5, 80) } };
  return plan({
    results: {
      comparisons: {
        "candidate-1": {
          calibrated: {
            baseline,
            benchmarks: {},
            candidate: { benchmarks: { A: coverage(0.9, 10), B: coverage(0.5, 80) } },
          },
          raw: {
            baseline,
            benchmarks: {},
            candidate: {
              benchmarks: {
                A: coverage(0.8, 10),
                B: coverage(0.4, 80, 79),
                C: coverage(null, 4, 0),
              },
            },
          },
        },
        "candidate-2": {
          raw: { baseline, benchmarks: {}, candidate: { benchmarks: { A: coverage(0, 10) } } },
        },
      },
    },
    state: "completed",
  });
}

it("lists pinned participants before scores exist, using each participant's stage", () => {
  render(
    <NativeEvaluationTable
      jobs={[{ ...jobs[0], status: "running" }, ...jobs.slice(1)]}
      plans={[
        plan({
          calls: {
            "calibration_candidate-1": { state: "running" },
            "final_candidate-2": { state: "running" },
          },
          state: "running",
        }),
      ]}
      projectId="project"
    />
  );
  const table = screen.getByRole("table", { name: "Evaluation jobs" });
  expect(within(table).getAllByRole("row")).toHaveLength(4);
  expect(screen.getByText("Waiting for training")).toBeTruthy();
  expect(screen.getByText("Calibrating")).toBeTruthy();
  expect(screen.getByText("Evaluating")).toBeTruthy();
  expect(screen.getAllByText("— / 89")).toHaveLength(3);
  expect(screen.queryByRole("button", { name: /Show scores/ })).toBeNull();
  expect(screen.getByRole("link", { name: /Titanic final/ }).getAttribute("href")).toContain(
    "cell=final-frozen"
  );
  expect(
    screen.getAllByRole("link", { name: "Titanic benchmark" })[0].getAttribute("href")
  ).toContain("format=decision");
  expect(screen.queryByText("Select evaluation suites")).toBeNull();
});

it("shows shared and separate preparation receipts on the correct model rows", () => {
  const preparing = plan({
    calls: {
      calibration_prepare: { state: "running" },
      "final_prepare_candidate-1": { state: "running" },
    },
    state: "running",
  });
  preparing.config.participants = [
    { ...participants[0], key: "base", kind: "foundation" },
    participants[1],
    participants[0],
  ];
  render(<NativeEvaluationTable jobs={jobs} plans={[preparing]} projectId="project" />);
  expect(screen.getAllByText("Preparing evaluation")).toHaveLength(3);
});

it("averages the same metric across benchmarks and expands raw and calibrated scores", () => {
  render(<NativeEvaluationTable jobs={jobs} plans={[completedPlan()]} projectId="project" />);
  const table = screen.getByRole("table", { name: "Evaluation jobs" });
  expect(within(table).getAllByRole("row")).toHaveLength(4);
  expect(screen.getByText("70%")).toBeTruthy();
  expect(screen.getByText("60%")).toBeTruthy();
  expect(screen.getByText("0%")).toBeTruthy();
  expect(screen.getByText("5 missing · 0 invalid · 0 incompatible")).toBeTruthy();
  const expand = screen.getByRole("button", { name: "Show scores for Qwen 2B Titanic" });
  fireEvent.click(expand);
  expect(expand.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getAllByText("0.4200").length).toBeGreaterThan(0);
  expect(screen.getAllByText("95% interval: 50.0% to 90.0%").length).toBeGreaterThan(0);
  expect(screen.getByText("80.0%")).toBeTruthy();
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Calibrated" }), {
    button: 0,
    ctrlKey: false,
  });
  const participantRow = screen.getByRole("row", { name: "Qwen 2B Titanic" });
  expect(within(participantRow).getByText("70%")).toBeTruthy();
  expect(screen.getByText("90.0%")).toBeTruthy();
  const missingRow = screen.getByRole("row", { name: "Qwen 4B Titanic" });
  expect(within(missingRow).queryByText("0%")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Hide scores for Qwen 2B Titanic" }));
  expect(screen.queryByRole("row", { name: "Qwen 2B Titanic: A accuracy" })).toBeNull();
});

it("distinguishes a saved draft from running work and surfaces failed work", () => {
  const view = render(
    <NativeEvaluationTable jobs={jobs} plans={[plan({ state: "draft" })]} projectId="project" />
  );
  expect(screen.getAllByText("Draft")).toHaveLength(3);
  view.rerender(
    <NativeEvaluationTable
      jobs={jobs}
      plans={[plan({ error: "Checkpoint fingerprint mismatch", state: "failed" })]}
      projectId="project"
    />
  );
  expect(screen.getAllByText("Failed")).toHaveLength(3);
  expect(screen.getByRole("alert").textContent).toContain("Checkpoint fingerprint mismatch");
});

it("keeps loading and errors distinct from an unconfigured run", () => {
  const view = render(<NativeEvaluationTable jobs={jobs} loading plans={[]} projectId="project" />);
  expect(screen.queryByText("No evaluations pinned to this run")).toBeNull();
  view.rerender(
    <NativeEvaluationTable
      error={new Error("Connection failed")}
      jobs={jobs}
      plans={[]}
      projectId="project"
    />
  );
  expect(screen.getByRole("alert").textContent).toContain("Connection failed");
  expect(screen.queryByText("No evaluations pinned to this run")).toBeNull();
  view.rerender(<NativeEvaluationTable jobs={jobs} plans={[]} projectId="project" />);
  expect(screen.getByText("No evaluations pinned to this run")).toBeTruthy();
});

it("focuses the requested training model without borrowing another participant's score", () => {
  render(
    <NativeEvaluationTable
      focusJobId="job-1"
      jobs={jobs}
      plans={[completedPlan()]}
      projectId="project"
    />
  );
  const table = screen.getByRole("table", { name: "Evaluation jobs" });
  expect(within(table).getAllByRole("row")).toHaveLength(2);
  expect(screen.getByText("60%")).toBeTruthy();
  expect(screen.queryByText("70%")).toBeNull();
});
