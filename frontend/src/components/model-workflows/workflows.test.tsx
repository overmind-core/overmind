// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { ComparisonMetrics } from "./comparison-metrics";

const create = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({ default: { projects: { projectsCreate: create } } }));
afterEach(cleanup);
it("creates a data project without a repository or capability", async () => {
  create.mockResolvedValue({ id: "data-project" });
  const done = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <DataProjectForm onCreated={done} />
    </QueryClientProvider>
  );
  fireEvent.change(screen.getByLabelText("Project name"), { target: { value: "Rating research" } });
  fireEvent.click(screen.getByRole("button", { name: "Continue with data" }));
  await waitFor(() => expect(done).toHaveBeenCalledWith("data-project"));
  expect(create.mock.calls[0][0].projectRequest.name).toBe("Rating research");
  expect(create.mock.calls[0][0].projectRequest).not.toHaveProperty("repository");
});
it("keeps soft-target metrics and coverage visible without inventing accuracy", () => {
  const metric = { decisions: 7, groups: 5, interval_95: [0.2, 0.6], mean: 0.4 };
  const card = {
    expected: 10,
    incompatible_inputs: 1,
    invalid_predictions: 1,
    metrics: { cross_entropy: metric },
    missing_predictions: 1,
    scored: 7,
  };
  const delta = { candidate_minus_baseline: -0.1, decisions: 7, groups: 5, interval_95: [-0.2, 0] };
  render(
    <ComparisonMetrics
      comparisons={{
        trained: {
          raw: {
            baseline: { benchmarks: { soft: card } },
            benchmarks: { soft: { metrics: { cross_entropy: delta } } },
            candidate: { benchmarks: { soft: card } },
          },
        },
      }}
      mode="raw"
      names={{ trained: "Trained" }}
    />
  );
  expect(screen.getByText("Cross entropy")).toBeTruthy();
  expect(screen.queryByText("Accuracy")).toBeNull();
  expect(screen.getByText("7 / 10")).toBeTruthy();
  expect(screen.getByText("1 missing · 1 invalid · 1 incompatible")).toBeTruthy();
});
