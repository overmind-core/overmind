// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { EvalRun, EvalRunRow } from "@/openapi";
import { GeneratedRunResults } from "./generated-run-results";

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

it("opens all model outputs together, retaining full text, reasoning, errors and missing outputs", async () => {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  const runs = ["alpha", "beta", "missing"].map(
    (id) => ({ cell: "cell", id, label: id, status: "completed", summary: {} }) as EvalRun
  );
  const samples = [
    {
      id: 1,
      input: "the input",
      metrics: { helpful: { reason: "Explains the result", score: 0.8 } },
      output: "the full generated answer",
      reference: "reference",
      rowIndex: 0,
      run: "alpha",
      score: 0.8,
    },
    {
      error: "Generation failed",
      id: 2,
      input: "the input",
      metrics: {},
      output: null,
      reference: "reference",
      rowIndex: 0,
      run: "beta",
      score: null,
    },
  ] as EvalRunRow[];
  for (const run of runs) {
    const results = samples.filter((sample) => sample.run === run.id);
    client.setQueryData(["eval-run-rows", run.id], { count: results.length, results });
  }
  render(
    <QueryClientProvider client={client}>
      <GeneratedRunResults runs={runs} />
    </QueryClientProvider>
  );
  fireEvent.click(screen.getByRole("button", { name: "Open alpha output for datapoint 0" }));
  const dialog = within(await screen.findByRole("dialog", { name: "Datapoint 0" }));
  expect(dialog.getByText("the full generated answer")).toBeTruthy();
  expect(dialog.getByText("Explains the result")).toBeTruthy();
  expect(dialog.getByText("Generation failed")).toBeTruthy();
  expect(dialog.getByText("No result recorded for this model on this datapoint.")).toBeTruthy();
  expect(dialog.queryByRole("combobox")).toBeNull();
  expect(dialog.getByRole("region", { name: "Model outputs" }).getAttribute("tabindex")).toBe("0");
  fireEvent.click(dialog.getByRole("button", { name: "Close" }));
  client.clear();
});
