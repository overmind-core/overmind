// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { DevelopmentMonitor } from "./development-monitor";

const mocks = vi.hoisted(() => ({ evidence: vi.fn(), summary: vi.fn(), track: vi.fn() }));
vi.mock("@/client", () => ({
  default: {
    finetuningJobs: {
      finetuningJobsMonitoringEvidenceRetrieve: mocks.evidence,
      finetuningJobsMonitoringRetrieve: mocks.summary,
    },
  },
}));
vi.mock("@/analytics", () => ({ trackEvent: mocks.track }));
vi.mock("./finetuning-charts", () => ({ MetricSeriesChart: () => null }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <DevelopmentMonitor jobId="job" status="cancelled" />
    </QueryClientProvider>
  );
}

it("keeps cancelled-run evidence readable and separates failures from zero quality", async () => {
  mocks.summary.mockResolvedValue({
    checkpoints: [],
    checks: [
      {
        coverage: { expected: 4, scored: 4 },
        error: {},
        evidenceAvailable: true,
        facts: {},
        id: "valid",
        key: "1:development:2",
        metrics: { eval_loss: 0.4, generation: { accuracy: 0, coverage: 1 } },
        state: "completed",
        step: 2,
        stream: "development",
      },
      {
        coverage: { expected: 4, scored: 0 },
        error: { message: "Checkpoint write failed" },
        evidenceAvailable: false,
        facts: {},
        id: "failed",
        key: "1:development:4",
        metrics: {},
        state: "failed",
        step: 4,
        stream: "development",
      },
    ],
    count: 2,
    current: {
      collection: { error: "Retained examples temporarily unavailable", state: "unavailable" },
      monitoring_seconds: 15,
      optimizer_seconds: 120,
      schedule: { next_step: 7 },
    },
    limitations: [],
    nextOffset: null,
  });
  mocks.evidence.mockResolvedValue({
    available: true,
    count: 1,
    items: [
      { input: "private input", output: "no", reference: "yes", row: 0, status: "completed" },
    ],
    nextOffset: null,
  });
  show();
  expect(await screen.findByText("Checkpoint write failed")).toBeTruthy();
  expect(screen.getByText("Step 7")).toBeTruthy();
  expect(screen.getByText("Retained examples temporarily unavailable")).toBeTruthy();
  expect(screen.getByText("0.0%")).toBeTruthy();
  expect(screen.getAllByText("Not measured").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Inspect step 2" }));
  expect(await screen.findByText("private input")).toBeTruthy();
  expect(mocks.evidence).toHaveBeenCalledWith({ check: "valid", id: "job", limit: 10, offset: 0 });
  expect(JSON.stringify(mocks.track.mock.calls)).not.toContain("private input");
});

it("does not claim historical runs have monitoring evidence", async () => {
  mocks.summary.mockResolvedValue({
    checkpoints: [],
    checks: [],
    count: 0,
    limitations: [],
    nextOffset: null,
    policy: null,
  });
  show();
  expect(await screen.findByText("No development checks recorded.")).toBeTruthy();
  expect(mocks.evidence).not.toHaveBeenCalled();
});

it("shows class coverage and paired changes without treating missing classes as perfect", async () => {
  mocks.summary.mockResolvedValue({
    checkpoints: [],
    checks: [
      {
        coverage: {},
        error: {},
        evidenceAvailable: false,
        facts: {},
        id: "check",
        metrics: {
          generation: {
            confusion_matrix: [
              [3, 1],
              [0, 0],
            ],
            labels: ["yes", "no"],
            per_class: {
              no: { f1: null, precision: 0, recall: null, support: 0 },
              yes: { f1: 0.857, precision: 1, recall: 0.75, support: 4 },
            },
            unrepresented_labels: ["no"],
          },
          paired_generation: {
            delta: 0.25,
            groups: 3,
            improved: 2,
            interval_95: [-0.25, 0.75],
            paired: 4,
            regressed: 1,
            unpaired: 1,
          },
        },
        state: "completed",
        step: 2,
        stream: "development",
      },
    ],
    count: 1,
    nextOffset: null,
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Inspect step 2" }));
  expect(screen.getByRole("table", { name: "Development class metrics" })).toBeTruthy();
  expect(screen.getByRole("region", { name: "Development confusion matrix" })).toBeTruthy();
  expect(screen.getByText("Unrepresented labels: no")).toBeTruthy();
  expect(screen.getByText("2 improved · 1 regressed · 4 paired · 1 unpaired")).toBeTruthy();
});

it("pages frozen sample identities through the passive evidence endpoint", async () => {
  mocks.summary.mockResolvedValue({
    checkpoints: [],
    checks: [],
    count: 0,
    nextOffset: null,
    probes: { development: { actual_rows: 26, population_rows: 100 } },
  });
  mocks.evidence.mockResolvedValue({
    available: true,
    count: 26,
    items: [{ index: 3, sha256: "row-fingerprint" }],
    nextOffset: 25,
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Inspect development sample" }));
  expect(await screen.findByText(/row-fingerprint/)).toBeTruthy();
  expect(mocks.evidence).toHaveBeenCalledWith({
    id: "job",
    limit: 25,
    offset: 0,
    probe: "development",
  });
});

it("keeps native distribution and ordinal measurements distinct", async () => {
  mocks.summary.mockResolvedValue({
    checkpoints: [],
    checks: [
      {
        coverage: {},
        error: {},
        evidenceAvailable: true,
        facts: {},
        id: "native",
        metrics: {
          brier: 0.15,
          distribution_decisions: 4,
          eval_loss: 0.3,
          expected_score_mae: 0.25,
          hard_label_accuracy: null,
          mean_decisions: 3,
        },
        state: "completed",
        step: 2,
        stream: "development",
      },
    ],
    count: 1,
    nextOffset: null,
  });
  mocks.evidence.mockResolvedValue({
    available: true,
    count: 1,
    items: [
      {
        option_values: [0, 1],
        probabilities: [0.25, 0.75],
        status: "completed",
        target_mean: 0,
        weight: 2,
      },
    ],
    nextOffset: null,
  });
  show();
  fireEvent.click(await screen.findByRole("button", { name: "Inspect step 2" }));
  expect(screen.getByText("Brier · 4 distributions")).toBeTruthy();
  expect(screen.getByText("Expected-score MAE · 3 means")).toBeTruthy();
  expect(screen.getByText("0.1500")).toBeTruthy();
  expect(screen.getByText("0.2500")).toBeTruthy();
  expect(await screen.findByText("Probabilities")).toBeTruthy();
  expect(screen.getByText("Target mean")).toBeTruthy();
  expect(screen.queryByText("Not recorded")).toBeNull();
});

it.each([
  "exact_match",
  "json_schema",
  "json_fields",
])("presents %s contract pass rates without calling them accuracy", async (kind) => {
  mocks.summary.mockResolvedValue({
    checkpoints: [],
    checks: [
      {
        evidenceAvailable: false,
        id: "schema",
        metrics: { generation: { coverage: 1, pass_rate: 0.75 } },
        state: "completed",
        step: 2,
        stream: "development",
      },
    ],
    count: 1,
    nextOffset: null,
    policy: { generation: { kind } },
  });
  show();
  expect(await screen.findByText("Contract pass rate")).toBeTruthy();
  expect(screen.getByText("75.0%")).toBeTruthy();
  expect(screen.queryByText("Generated accuracy")).toBeNull();
});
