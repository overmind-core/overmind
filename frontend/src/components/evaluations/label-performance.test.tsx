// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { seriesColor } from "@/lib/colors";
import { LabelPerformance } from "./label-performance";

const monitor = vi.hoisted(() => vi.fn());
vi.mock("@/client", () => ({
  default: { nativeEvaluations: { nativeEvaluationsMonitorRetrieve: monitor } },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("pages and searches all labels, sorts by a model, and switches metrics without inventing missing scores", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  );
  HTMLElement.prototype.scrollIntoView = vi.fn();
  monitor.mockImplementation(async ({ offset, search }) => ({
    count: search ? 1 : 77,
    labels: [
      {
        benchmark: "banking77",
        expected: 40,
        index: offset,
        label: search || `label-${offset}`,
        models: {
          base: {
            correct: 24,
            f1: 2 / 3,
            precision: 0.75,
            predicted: 32,
            recall: 0.6,
            support: 40,
          },
          candidate: {
            correct: 0,
            f1: null,
            precision: null,
            predicted: 0,
            recall: null,
            support: 0,
          },
        },
      },
    ],
    state: "ready",
  }));
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <TooltipProvider>
        <LabelPerformance
          colorIndices={{ base: 2, candidate: 0 }}
          evaluation="evaluation"
          names={{ base: "Base", candidate: "Candidate" }}
          stopped
          suiteRole="final"
        />
      </TooltipProvider>
    </QueryClientProvider>
  );
  expect(await screen.findByText("label-0")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "66.7%" })
      .querySelector<HTMLElement>("[style*='background-color']")?.style.backgroundColor
  ).toBe(seriesColor(2));
  expect(screen.getByRole("button", { name: "—" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Go to next page" }));
  expect(await screen.findByText("label-10")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Sort by Base" }));
  await waitFor(() =>
    expect(monitor).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, ordering: "base:f1", view: "labels" })
    )
  );
  fireEvent.change(screen.getByRole("searchbox", { name: "Search labels" }), {
    target: { value: "activate_my_card" },
  });
  expect(await screen.findByText("activate_my_card")).toBeTruthy();
  await waitFor(() =>
    expect(monitor).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, search: "activate_my_card" })
    )
  );
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Per-label metric" }), {
    key: "ArrowDown",
  });
  fireEvent.click(await screen.findByRole("option", { name: "Recall" }));
  expect(await screen.findByRole("button", { name: "60.0%" })).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "60.0%" })
      .querySelector<HTMLElement>("[style*='background-color']")?.style.backgroundColor
  ).toBe(seriesColor(2));
  fireEvent.focus(screen.getByRole("button", { name: "60.0%" }));
  const tooltip = await screen.findByRole("tooltip");
  expect(within(tooltip).getByText("40 / 40 reference examples scored")).toBeTruthy();
  expect(within(tooltip).getByText("32 predictions · 24 correct")).toBeTruthy();
});
