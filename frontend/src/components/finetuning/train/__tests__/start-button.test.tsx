// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { TrainingStartButton } from "../start-button";
import type { TrainWizard } from "../use-train-wizard";

afterEach(cleanup);

function wizard(overrides: Record<string, unknown> = {}) {
  return {
    benchmarkModel: "small",
    candidateByModel: new Map(),
    canLaunch: true,
    contextQuery: {
      data: { checks: [{ model: "small", role: "generation", status: "warning" }] },
    },
    evaluationPlan: { evalModelAfter: true },
    launch: vi.fn(),
    selectedDrafts: [{ model: "training-model" }],
    ...overrides,
  } as unknown as TrainWizard;
}

it("uses the whole orange start button to show a short warning before starting anyway", () => {
  const model = wizard();
  render(<TrainingStartButton wizard={model} />);
  const start = screen.getByRole("button", { name: "Start training" });
  expect(start.getAttribute("data-variant")).toBe("warning");
  expect(start.querySelector("svg")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(start);
  expect(model.launch).not.toHaveBeenCalled();
  const text = screen.getByRole("alert").textContent;
  expect(text).toContain("Evaluations may fail");
  expect(text).toContain("Benchmark model: some rows may exceed its limits.");
  expect(text).not.toContain("Ready benchmarks");
  fireEvent.click(screen.getByRole("button", { name: "Start anyway" }));
  expect(model.launch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alert")).toBeNull();
});

it("can dismiss the warning without launching", () => {
  const model = wizard();
  render(<TrainingStartButton wizard={model} />);
  fireEvent.click(screen.getByRole("button", { name: "Start training" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("alert")).toBeNull();
  expect(model.launch).not.toHaveBeenCalled();
});

it("keeps start neutral while a new selection is still being checked", () => {
  const model = wizard({ contextQuery: { isFetching: true } });
  render(<TrainingStartButton wizard={model} />);
  const start = screen.getByRole("button", { name: "Start training" });
  expect(start.getAttribute("data-variant")).toBe("default");
  fireEvent.click(start);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(model.launch).toHaveBeenCalledTimes(1);
});

it("starts immediately when context fits", () => {
  const model = wizard({ contextQuery: { data: { checks: [{ status: "fits" }] } } });
  render(<TrainingStartButton wizard={model} />);
  const start = screen.getByRole("button", { name: "Start training" });
  expect(start.getAttribute("data-variant")).toBe("default");
  fireEvent.click(start);
  expect(model.launch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("alert")).toBeNull();
});

it.each(["unknown", "unavailable"])("does not warn when context is %s", (status) => {
  const model = wizard({
    contextQuery:
      status === "unavailable"
        ? { isError: true }
        : { data: { checks: [{ model: "small", role: "generation", status }] } },
  });
  render(<TrainingStartButton wizard={model} />);
  const start = screen.getByRole("button", { name: "Start training" });
  expect(start.getAttribute("data-variant")).toBe("default");
  fireEvent.click(start);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(model.launch).toHaveBeenCalledTimes(1);
});

it("keeps technical launch blockers and the launching state disabled", () => {
  const model = wizard({ canLaunch: false, launchBlocker: "Select a training dataset" });
  const { rerender } = render(<TrainingStartButton wizard={model} />);
  expect(screen.getByRole("button", { name: "Start training" }).hasAttribute("disabled")).toBe(
    true
  );
  rerender(<TrainingStartButton wizard={{ ...model, canLaunch: true, launching: true }} />);
  expect(screen.getByRole("button", { name: "Starting" }).hasAttribute("disabled")).toBe(true);
  expect(model.launch).not.toHaveBeenCalled();
});

it("merges grader warnings without adding unverified models to the summary", () => {
  const model = wizard({
    contextQuery: {
      data: {
        checks: [
          { model: "gpt-5.6-luna", role: "judge", status: "warning" },
          { model: "gpt-5.6-luna", role: "judge", status: "warning" },
          { model: "gpt-5.6-luna", role: "judge", status: "unknown" },
          { model: "unverified-model", role: "generation", status: "unknown" },
        ],
      },
    },
  });
  render(<TrainingStartButton wizard={model} />);
  fireEvent.click(screen.getByRole("button", { name: "Start training" }));
  expect(screen.getAllByRole("listitem")).toHaveLength(1);
  expect(screen.getByRole("listitem").textContent).toContain("some rows may exceed");
  expect(screen.getByRole("alert").textContent).not.toContain("unverified");
});

it("warns for the planned serving limit even when the base model fits", () => {
  const model = wizard({
    candidateByModel: new Map([
      [
        "training-model",
        { displayName: "Training model", servingContext: { rows: 150, warnings: ["Too small"] } },
      ],
    ]),
    contextQuery: {
      data: { checks: [{ model: "training-model", role: "generation", status: "fits" }] },
    },
  });
  render(<TrainingStartButton wizard={model} />);
  fireEvent.click(screen.getByRole("button", { name: "Start training" }));
  expect(screen.getByRole("alert").textContent).toContain("Training model: some rows may exceed");
});

it("treats unverified judge compatibility consistently with the dropdown", () => {
  const model = wizard({
    contextQuery: {
      data: {
        checks: [{ model: "judge", role: "judge", status: "fits" }],
        judgeModels: [{ model: "judge", status: "unknown" }],
      },
    },
  });
  render(<TrainingStartButton wizard={model} />);
  expect(screen.getByRole("button", { name: "Start training" }).getAttribute("data-variant")).toBe(
    "default"
  );
});
