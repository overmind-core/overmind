// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { seriesColor } from "@/lib/colors";
import { type Comparison, ComparisonMetrics } from "./comparison-metrics";

const create = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({ default: { projects: { projectsCreate: create } } }));
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
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
it("keeps soft-target metrics and coverage visible without inventing accuracy", async () => {
  const metric = { decisions: 7, groups: 5, interval_95: [1.2, 2.6], mean: 1.8 };
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
      colorIndices={{ base: 2, first: 0, second: 1, trained: 0 }}
      comparisons={{
        trained: {
          raw: {
            baseline: { benchmarks: { soft: card } },
            benchmarks: { soft: { metrics: { cross_entropy: delta } } },
            candidate: { benchmarks: { soft: card } },
          },
        },
      }}
      names={{ trained: "Trained" }}
    />
  );
  expect(screen.getAllByText("Cross entropy").length).toBeGreaterThan(0);
  expect(screen.getByRole("img", { name: "soft · Cross entropy" })).toBeTruthy();
  expect(screen.getAllByText("1.8000").length).toBeGreaterThan(0);
  expect(screen.queryByText("180.0%")).toBeNull();
  expect(screen.queryByText("Accuracy")).toBeNull();
  expect(screen.getAllByText("7 / 10")).toHaveLength(2);
  fireEvent.focus(screen.getByRole("button", { name: "Trained: 7 / 10 scored" }));
  expect(await screen.findAllByText("1 missing · 1 invalid · 1 incompatible")).not.toHaveLength(0);
});

it("compares all models once and omits hard-label aliases", () => {
  const metric = { decisions: 8, groups: 8, mean: 0.75 };
  const card = {
    expected: 8,
    incompatible_inputs: 0,
    invalid_predictions: 0,
    metrics: {
      accuracy: metric,
      brier: { ...metric, mean: 0.2 },
      cross_entropy: { ...metric, mean: 0.48 },
      mode_agreement: metric,
      top1_reference_mass: metric,
    },
    missing_predictions: 0,
    scored: 8,
  };
  const comparison: Comparison = {
    baseline: { benchmarks: { labels: card } },
    benchmarks: {},
    candidate: { benchmarks: { labels: card } },
  };
  render(
    <ComparisonMetrics
      colorIndices={{ base: 2, first: 0, second: 1, trained: 0 }}
      comparisons={{
        base: { raw: comparison },
        first: { raw: comparison },
        second: { raw: comparison },
      }}
      names={{ base: "Base", first: "First", second: "Second" }}
    />
  );
  for (const [name, colorIndex] of [
    ["Base", 2],
    ["First", 0],
    ["Second", 1],
  ] as const) {
    for (const value of ["75.0%", "0.2000", "0.4800"]) {
      const bar = screen.getByRole("button", { name: `${name}: ${value}` });
      expect(
        bar.querySelector<HTMLElement>("[style*='background-color']")?.style.backgroundColor
      ).toBe(seriesColor(colorIndex));
    }
  }
  expect(screen.getAllByRole("table")).toHaveLength(1);
  expect(screen.getAllByRole("row")).toHaveLength(4);
  expect(screen.getByRole("row", { name: /First/ })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: /Accuracy/ })).toBeTruthy();
  expect(screen.queryByText("Mode agreement")).toBeNull();
  expect(screen.queryByText("Top choice reference mass")).toBeNull();
  expect(screen.queryByText("Calibration, class and slice diagnostics")).toBeNull();
  const sortModel = screen.getByRole("button", { name: "Sort by Model" });
  fireEvent.click(sortModel);
  expect(screen.getByRole("columnheader", { name: "Model" }).getAttribute("aria-sort")).toBe(
    "ascending"
  );
  fireEvent.click(sortModel);
  expect(screen.getByRole("columnheader", { name: "Model" }).getAttribute("aria-sort")).toBe(
    "descending"
  );
  expect(screen.getAllByRole("row")[1].textContent).toContain("Second");
  fireEvent.click(sortModel);
  expect(screen.getByRole("columnheader", { name: "Model" }).getAttribute("aria-sort")).toBe(
    "none"
  );
  expect(screen.getAllByRole("row")[1].textContent).toContain("Base");
});

it("retains distribution metrics when accuracy covers only the hard-label subset", () => {
  const metric = { decisions: 8, groups: 8, mean: 0.75 };
  const card = {
    expected: 8,
    incompatible_inputs: 0,
    invalid_predictions: 0,
    metrics: {
      accuracy: { ...metric, decisions: 4, groups: 4 },
      mode_agreement: metric,
      top1_reference_mass: metric,
    },
    missing_predictions: 0,
    scored: 8,
  };
  const comparison: Comparison = {
    baseline: { benchmarks: { mixed: card } },
    benchmarks: {},
    candidate: { benchmarks: { mixed: card } },
  };
  render(
    <ComparisonMetrics
      colorIndices={{ base: 2, first: 0, second: 1, trained: 0 }}
      comparisons={{ trained: { raw: comparison } }}
      names={{ base: "Base", trained: "Trained" }}
    />
  );
  expect(screen.getByRole("columnheader", { name: /Mode agreement/ })).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: /Top choice reference mass/ })).toBeTruthy();
});

it("shows confidence as a separate metric and retains adjusted scores without replacing originals", async () => {
  const metric = { decisions: 1000, groups: 1000, mean: 0.712 };
  const card = {
    expected: 1000,
    incompatible_inputs: 0,
    invalid_predictions: 0,
    metrics: { accuracy: metric, brier: { ...metric, mean: 0.31 }, top1_reference_mass: metric },
    missing_predictions: 0,
    reliability: [{ confidence: 0.95, decisions: 1000, reference_mass: 0.712 }],
    scored: 1000,
  };
  const adjusted = {
    ...card,
    metrics: { ...card.metrics, brier: { ...metric, mean: 0.19 } },
    reliability: [{ confidence: 0.75, decisions: 1000, reference_mass: 0.712 }],
  };
  render(
    <ComparisonMetrics
      colorIndices={{ base: 2, first: 0, second: 1, trained: 0 }}
      comparisons={{
        trained: {
          calibrated: {
            baseline: { benchmarks: { sentiment: adjusted } },
            benchmarks: {},
            candidate: { benchmarks: { sentiment: adjusted } },
          },
          raw: {
            baseline: { benchmarks: { sentiment: card } },
            benchmarks: {},
            candidate: { benchmarks: { sentiment: card } },
          },
        },
      }}
      names={{ base: "Base", trained: "Trained" }}
    />
  );
  expect(screen.getByRole("heading", { name: "Confidence" })).toBeTruthy();
  for (const [name, colorIndex] of [
    ["Base", 2],
    ["Trained", 0],
  ] as const) {
    const metric = screen.getByRole("button", { name: `${name}: 71.2%` });
    expect(
      metric.querySelector<HTMLElement>("[style*='background-color']")?.style.backgroundColor
    ).toBe(seriesColor(colorIndex));
    for (const mode of ["Original", "Adjusted"]) {
      const bar = screen.getByRole("button", { name: new RegExp(`${name} · ${mode}:`) });
      expect(
        bar.querySelector<HTMLElement>("[style*='background-color']")?.style.backgroundColor
      ).toBe(seriesColor(colorIndex));
    }
  }
  expect(
    screen.getByRole("button", { name: /Trained · Original: Correct 71.2%, claimed 95.0%/ })
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /Trained · Adjusted: Correct 71.2%, claimed 75.0%/ })
  ).toBeTruthy();
  expect(screen.getByRole("columnheader", { name: /Calibration error/ })).toBeTruthy();
  const row = within(screen.getByRole("row", { name: /Trained/ }));
  expect(row.getByRole("button", { name: "23.8 pp" })).toBeTruthy();
  fireEvent.focus(row.getByRole("button", { name: "0.3100" }));
  expect(await screen.findAllByText("After calibration: 0.1900 · 1000 decisions")).not.toHaveLength(
    0
  );
  expect(screen.queryByRole("tablist")).toBeNull();
});
