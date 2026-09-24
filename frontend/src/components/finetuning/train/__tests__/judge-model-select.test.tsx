// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { JudgeModelSelect } from "@/components/evaluations/judge-model-select";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TrainWizard } from "../use-train-wizard";

afterEach(cleanup);

function setup(overrides: Partial<TrainWizard> = {}) {
  const setJudgeModel = vi.fn();
  const wizard = {
    contextQuery: {
      data: {
        checks: [{ configuredModel: "small", model: "small", role: "judge", status: "warning" }],
        judgeModels: [
          {
            costDeltaUsd: 0,
            estimatedCostUsd: 1,
            model: "small",
            name: "Small judge",
            status: "warning",
          },
          {
            costDeltaUsd: 1,
            estimatedCostUsd: 2,
            model: "large",
            name: "Large judge",
            status: "fits",
          },
          { model: "unknown", name: "Unknown judge", status: "unknown" },
        ],
      },
    },
    evalSetId: "set-1",
    judgeModel: "",
    setJudgeModel,
    ...overrides,
  } as unknown as TrainWizard;
  render(
    <TooltipProvider>
      <JudgeModelSelect
        checking={wizard.contextQuery?.isFetching}
        disabled={!wizard.evalSetId}
        onChange={wizard.setJudgeModel}
        report={wizard.contextQuery?.data}
        value={wizard.judgeModel}
      />
    </TooltipProvider>
  );
  return setJudgeModel;
}

function openOptions() {
  fireEvent.keyDown(screen.getByRole("combobox", { name: /Judge model/ }), { key: "ArrowDown" });
}

it("prefills the saved judge and labels every option without disabling uncertain choices", () => {
  const setJudgeModel = setup();
  expect(screen.getByRole("combobox").textContent).toContain("Small judge");
  expect(screen.getByRole("combobox").className).toContain("bg-warning/10");
  expect(screen.queryByRole("alert")).toBeNull();
  openOptions();
  const fitting = screen.getByRole("option", { name: /Large judge/ });
  expect(within(fitting).getByText(/Fits estimated context/)).toBeTruthy();
  expect(fitting.textContent).toContain("+$1.00");
  const small = screen.getByRole("option", { name: /Small judge.*Context too small/ });
  expect(small.getAttribute("aria-disabled")).not.toBe("true");
  expect(small.className).toContain("text-muted-foreground");
  const unknown = screen.getByRole("option", { name: /Unknown judge/ });
  expect(unknown.textContent).toContain("Context unverified");
  expect(unknown.textContent).toContain("Cost unavailable");
  expect(unknown.getAttribute("aria-disabled")).not.toBe("true");
  expect(unknown.classList.contains("text-muted-foreground")).toBe(false);
  fireEvent.click(fitting);
  expect(setJudgeModel).toHaveBeenCalledWith("large");
});

it.each(["unknown", "unavailable", "pending"])("keeps an %s judge neutral", (status) => {
  setup({
    contextQuery: {
      data:
        status === "unknown"
          ? {
              checks: [{ model: "unknown", role: "judge", status: "unknown" }],
              judgeModels: [{ model: "unknown", name: "Unknown judge", status: "unknown" }],
            }
          : undefined,
      isError: status === "unavailable",
      isFetching: status === "pending",
    } as unknown as TrainWizard["contextQuery"],
  });
  expect(screen.getByRole("combobox").className).not.toContain("bg-warning/10");
  expect(screen.queryByRole("alert")).toBeNull();
});

it("lets the user select an unsuitable model despite its muted appearance", () => {
  const setJudgeModel = setup({ judgeModel: "large" });
  expect(screen.getByRole("combobox").className).not.toContain("bg-warning/10");
  openOptions();
  fireEvent.click(screen.getByRole("option", { name: /Small judge.*Context too small/ }));
  expect(setJudgeModel).toHaveBeenCalledWith("small");
});

it("can restore the eval-set default after an explicit run-only selection", () => {
  const setJudgeModel = setup({ judgeModel: "large" });
  expect(screen.getByRole("combobox").textContent).toContain("Large judge");
  expect(screen.getByText("This run only")).toBeTruthy();
  openOptions();
  fireEvent.click(screen.getByRole("option", { name: /Small judge Eval set/ }));
  expect(setJudgeModel).toHaveBeenCalledWith("");
});

it("preserves a mixed-judge set rather than silently replacing its judges", () => {
  const setJudgeModel = setup({
    contextQuery: {
      data: {
        checks: [
          { configuredModel: "one", model: "one", role: "judge", status: "fits" },
          { configuredModel: "two", model: "two", role: "judge", status: "fits" },
        ],
        judgeModels: [],
      },
    } as unknown as TrainWizard["contextQuery"],
  });
  expect(screen.getByRole("combobox").textContent).toContain("Mixed models");
  expect(setJudgeModel).not.toHaveBeenCalled();
});
