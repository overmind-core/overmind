// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { DevelopmentMonitor } from "./development-monitor";

const mocks = vi.hoisted(() => ({ summary: vi.fn() }));
vi.mock("@/client", () => ({
  default: { finetuningJobs: { finetuningJobsMonitoringRetrieve: mocks.summary } },
}));
vi.mock("./finetuning-charts", () => ({
  MetricSeriesChart: ({ data }: { data: { step: number; value: number }[] }) => (
    <output aria-label="Validation points">{JSON.stringify(data)}</output>
  ),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <DevelopmentMonitor jobId="job" modelName="Qwen3 0.6B" status="cancelled" />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

it("loads later checks without mixing final-population loss or invalid measurements into the curve", async () => {
  mocks.summary
    .mockResolvedValueOnce({
      checks: [
        { metrics: { eval_loss: 0.7 }, state: "completed", step: 0, stream: "development" },
        { metrics: {}, state: "failed", step: 5, stream: "development" },
        { metrics: { eval_loss: null }, state: "completed", step: 6, stream: "development" },
        { metrics: { eval_loss: Number.NaN }, state: "completed", step: 7, stream: "development" },
        { metrics: { eval_loss: 0.1 }, state: "running", step: 8, stream: "development" },
      ],
      nextOffset: 5,
    })
    .mockResolvedValueOnce({
      checks: [
        { metrics: { eval_loss: 0.4 }, state: "completed", step: 10, stream: "development" },
        { metrics: { eval_loss: 0.2 }, state: "completed", step: 10, stream: "final_development" },
      ],
      nextOffset: null,
    });
  show();
  expect((await screen.findByLabelText("Validation points")).textContent).toBe(
    JSON.stringify([
      { step: 0, value: 0.7 },
      { step: 10, value: 0.4 },
    ])
  );
  expect(mocks.summary).toHaveBeenNthCalledWith(
    2,
    { id: "job", limit: 100, offset: 5 },
    expect.objectContaining({ signal: expect.any(AbortSignal) })
  );
});

it("renders a single recorded measurement including zero loss", async () => {
  mocks.summary.mockResolvedValue({
    checks: [{ metrics: { eval_loss: 0 }, state: "completed", step: 0, stream: "development" }],
    nextOffset: null,
  });
  show();
  expect((await screen.findByLabelText("Validation points")).textContent).toBe(
    JSON.stringify([{ step: 0, value: 0 }])
  );
});

it("leaves missing validation unmeasured", async () => {
  mocks.summary.mockResolvedValue({ checks: [], nextOffset: null });
  show();
  expect(await screen.findByText("No validation loss recorded")).toBeTruthy();
  expect(screen.queryByLabelText("Validation points")).toBeNull();
});

it("reports a failed history request rather than displaying an empty or partial curve", async () => {
  mocks.summary
    .mockResolvedValueOnce({
      checks: [{ metrics: { eval_loss: 0.7 }, state: "completed", step: 0, stream: "development" }],
      nextOffset: 1,
    })
    .mockRejectedValueOnce(new Error("Validation history unavailable"));
  show();
  expect(await screen.findByText("Validation history unavailable")).toBeTruthy();
  expect(screen.queryByLabelText("Validation points")).toBeNull();
});
