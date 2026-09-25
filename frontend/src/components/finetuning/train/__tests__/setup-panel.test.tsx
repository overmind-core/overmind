// @vitest-environment jsdom

import type { ReactNode } from "react";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetupPanel } from "@/components/finetuning/train/setup-panel";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Dataset, DatasetValidationResponse } from "@/openapi";

afterEach(cleanup);

// Radix scrolls the selected option; jsdom has no layout or scrolling.
HTMLElement.prototype.scrollIntoView = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, params }: { children: ReactNode; params: { datasetId: string } }) => (
    <a href={`/datasets/${params.datasetId}`}>{children}</a>
  ),
}));

vi.mock("@/components/model-provider-chip", () => ({
  getProviderIcon: () => undefined,
  ModelProviderChip: ({ model }: { model: string }) => <span title={model}>{model}</span>,
  ProviderLogo: () => null,
}));

const TRAIN_SET = {
  capability: "capability-1",
  id: "ds-train",
  name: "Support transcripts",
  usable: { contract: "train", id: "v-train", label: "v1", number: 1, rows: 342, run_state: "ok" },
} as unknown as Dataset;

const EVAL_SET = {
  capability: "capability-1",
  id: "ds-eval",
  name: "Support holdout",
  usable: { contract: "eval", id: "v-eval", label: "v1", number: 1, rows: 30, run_state: "ok" },
} as unknown as Dataset;

const CAPABILITY = { id: "capability-1", name: "Support" } as unknown as TrainWizard["capability"];

const VALIDATION: DatasetValidationResponse = {
  errors: [],
  format: "chat",
  numExamples: 342,
  stats: { format: "chat", maxTokenLength: 193, trainExamples: 273, valExamples: 69 },
  valid: true,
  warnings: [],
};

function wizard(over: Partial<TrainWizard>): TrainWizard {
  return {
    benchmarkModel: "",
    benchmarkOptions: [],
    benchmarksQuery: { isError: false, isLoading: false },
    capabilities: [{ id: "capability-1", name: "Support" }],
    capabilitiesQuery: { error: null, isLoading: false },
    capability: undefined,
    capabilityId: "",
    dataset: TRAIN_SET,
    datasetId: TRAIN_SET.id,
    datasets: [TRAIN_SET],
    datasetsQuery: { error: null, isLoading: false },
    evalDataset: EVAL_SET,
    evalDatasetId: EVAL_SET.id,
    evalDatasets: [EVAL_SET],
    evalDatasetsQuery: { error: null, isLoading: false },
    evalPreload: { data: undefined },
    evalSet: { generativeCount: 3, id: "es-1", isActive: true, name: "Default" },
    evalSetId: "es-1",
    evalSets: [{ generativeCount: 3, id: "es-1", isActive: true, name: "Default" }],
    evalSetsQuery: { error: null, isLoading: false },
    evaluationPlan: {
      evalIncumbentAfter: false,
      evalIncumbentBefore: false,
      evalModelAfter: true,
      evalModelBefore: true,
    },
    hasIncumbent: false,
    overlapCount: 12,
    runName: "Support · transcripts",
    selectedBenchmark: undefined,
    setBenchmarkModel: () => {},
    setCapabilityId: () => {},
    setDatasetId: () => {},
    setEvalDatasetId: () => {},
    setEvalSetId: () => {},
    setEvaluationChoice: () => {},
    setRunName: () => {},
    validating: false,
    validation: VALIDATION,
    ...over,
  } as unknown as TrainWizard;
}

function setup(over: Partial<TrainWizard> = {}) {
  return render(
    <TooltipProvider>
      <SetupPanel projectId="p-1" wizard={wizard(over)} />
    </TooltipProvider>
  );
}

describe("SetupPanel", () => {
  it("aligns evaluation controls on shared grid rows", () => {
    setup();
    const field = (control: HTMLElement) => control.parentElement?.parentElement;
    const evalSet = field(screen.getByRole("combobox", { name: /^Eval set/ }));
    const judge = field(screen.getByRole("combobox", { name: /^Judge model/ }));
    const benchmark = field(screen.getByRole("combobox", { name: /^Benchmark model/ }));
    const baseline = field(screen.getByRole("group", { name: "Incumbent evaluations" }));
    const training = field(screen.getByRole("group", { name: "Training model evaluations" }));
    expect(evalSet?.parentElement).toBe(judge?.parentElement);
    expect(benchmark?.parentElement).toBe(judge?.parentElement);
    expect(baseline?.parentElement).toBe(judge?.parentElement);
    expect(training?.parentElement).toBe(judge?.parentElement);
    expect(evalSet?.className).toContain("sm:row-start-1");
    expect(benchmark?.className).toContain("sm:row-start-1");
    expect(judge?.className).toContain("sm:row-start-2");
    expect(baseline?.className).toContain("sm:row-start-2");
    expect(training?.className).toContain("lg:row-start-2");
    expect(screen.getByText("Benchmark evals")).toBeTruthy();
  });

  it("shows the checked row count and reserved-context estimate for a fitting benchmark", () => {
    setup({
      benchmarkModel: "ft-benchmark",
      contextQuery: {
        data: {
          checks: [
            {
              checkedRows: 500,
              contextWindow: 16384,
              model: "ft-benchmark",
              requiredContext: 9316,
              role: "generation",
              status: "fits",
            },
          ],
        },
      } as unknown as TrainWizard["contextQuery"],
      evaluationPlan: {
        evalIncumbentAfter: false,
        evalIncumbentBefore: true,
        evalModelAfter: true,
        evalModelBefore: false,
      },
    });
    expect(screen.getByRole("status").textContent).toBe(
      "500 rows · 9,316 / 16,384 tokens estimated"
    );
    expect(screen.getByRole("combobox", { name: /^Benchmark model/ }).className).not.toContain(
      "bg-warning/10"
    );
  });

  it("tints the affected benchmark selector and keeps muted alternatives selectable", () => {
    const setBenchmarkModel = vi.fn();
    setup({
      benchmarkModel: "small",
      benchmarkOptions: [
        { kind: "Trained model", label: "Small benchmark", value: "small" },
        { kind: "Trained model", label: "Large benchmark", value: "large" },
        { kind: "Codebase incumbent", label: "Unknown benchmark", value: "unknown" },
      ],
      benchmarksQuery: {
        data: [{ maxModelLen: 32000, modelId: "large", status: "ready" }],
      } as unknown as TrainWizard["benchmarksQuery"],
      contextQuery: {
        data: {
          checks: [
            {
              checkedRows: 30,
              model: "small",
              requiredContext: 16000,
              role: "generation",
              status: "warning",
            },
          ],
        },
      } as unknown as TrainWizard["contextQuery"],
      evaluationPlan: {
        evalIncumbentAfter: false,
        evalIncumbentBefore: true,
        evalModelAfter: true,
        evalModelBefore: true,
      },
      selectedBenchmark: { kind: "Trained model", label: "Small benchmark", value: "small" },
      setBenchmarkModel,
    });
    const selector = screen.getByRole("combobox", { name: /Benchmark model/ });
    expect(selector.className).toContain("bg-warning/10");
    expect(screen.getByRole("combobox", { name: /Judge model/ }).className).not.toContain(
      "bg-warning/10"
    );
    fireEvent.keyDown(selector, { key: "ArrowDown" });
    const small = screen.getByRole("option", { name: /Small benchmark/ });
    expect(small.className).toContain("text-muted-foreground");
    expect(small.getAttribute("aria-disabled")).not.toBe("true");
    expect(screen.getByRole("option", { name: /Large benchmark/ }).textContent).toContain(
      "Fits estimated context"
    );
    const unknown = screen.getByRole("option", { name: /Unknown/ });
    expect(unknown.textContent).toContain("Context unverified");
    expect(unknown.classList.contains("text-muted-foreground")).toBe(false);
    fireEvent.click(unknown);
    expect(setBenchmarkModel).toHaveBeenCalledWith("unknown");
  });
  it("keeps an unverified benchmark selector neutral", () => {
    setup({
      benchmarkModel: "unknown",
      contextQuery: {
        data: {
          checks: [{ model: "unknown", role: "generation", status: "unknown" }],
        },
      } as unknown as TrainWizard["contextQuery"],
      evaluationPlan: {
        evalIncumbentAfter: false,
        evalIncumbentBefore: true,
        evalModelAfter: true,
        evalModelBefore: false,
      },
    });
    expect(screen.getByRole("combobox", { name: /^Benchmark model/ }).className).not.toContain(
      "bg-warning/10"
    );
    expect(screen.getByRole("status").textContent).toBe("Context unverified");
  });
  it("puts the eval-set judge in a selector below the set without a warning card", () => {
    setup({
      contextQuery: {
        data: {
          checks: [
            {
              checkedRows: 30,
              estimatedCostUsd: 1,
              message: "Judge context is too small.",
              model: "small",
              role: "judge",
              status: "warning",
              suggestions: [
                {
                  contextWindow: 128000,
                  costDeltaUsd: 1,
                  estimatedCostUsd: 2,
                  model: "larger",
                  name: "Fitting judge",
                  reservedOutputTokens: 17000,
                },
              ],
            },
          ],
        },
        isError: false,
      } as unknown as TrainWizard["contextQuery"],
    });
    const selector = screen.getByRole("combobox", { name: /Judge model/ });
    expect(selector.textContent).toContain("Small");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Judge context is too small.")).toBeNull();
  });
  it("puts benchmark selection in the training setup grid", () => {
    const benchmark = {
      baseModelId: "Qwen/Qwen3.5-27B",
      kind: "Trained model",
      label: "Support · Qwen",
      value: "ft-12345678-qwen3-5-27b",
    };
    setup({
      benchmarkModel: benchmark.value,
      benchmarkOptions: [benchmark],
      selectedBenchmark: benchmark,
    });
    const selector = screen.getByRole("combobox", { name: "Benchmark model" });
    expect(within(selector).getByTitle(benchmark.value).getAttribute("data-slot")).not.toBe(
      "badge"
    );
    expect(selector.textContent).toContain("Qwen3.5 27B · FT");
    expect(selector.getAttribute("title")).toBe("Support · Qwen");
    expect(
      selector.compareDocumentPosition(
        screen.getByRole("group", { name: "Incumbent evaluations" })
      ) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
  });
  it("keeps workshop review recommendations out of training setup", () => {
    const readiness = {
      formatReason: "",
      formatValid: true,
      qualityPassed: false,
      qualityReason: "Input evidence: unknown",
      qualityReviewed: true,
      trainingConfiguration: "not_checked",
    };
    setup({
      capabilityId: "another-capability",
      dataset: { ...TRAIN_SET, readiness },
      evalDataset: { ...EVAL_SET, readiness },
    });
    expect(screen.queryByText("Review recommended")).toBeNull();
    expect(screen.queryByText("Input evidence: unknown")).toBeNull();
    expect(screen.queryByRole("link", { name: "Open workshop" })).toBeNull();
    expect(screen.queryByText("Can't be trained on yet")).toBeNull();
  });
  it("groups each model's evaluation targets in its own field", () => {
    setup();

    const incumbent = within(screen.getByRole("group", { name: "Incumbent evaluations" }));
    expect(incumbent.getByText("Baseline")).toBeTruthy();
    expect(incumbent.getByText("After")).toBeTruthy();
    const training = within(screen.getByRole("group", { name: "Training model evaluations" }));
    expect(training.getByText("Base")).toBeTruthy();
    expect(training.getByText("Trained")).toBeTruthy();
  });

  it("disables incumbent checkboxes without an incumbent", () => {
    setup();
    expect(screen.getAllByRole("checkbox")).toHaveLength(4);
    expect(
      screen.getByRole("checkbox", { name: "Evaluate incumbent baseline" }).hasAttribute("disabled")
    ).toBe(true);
    expect(
      screen
        .getByRole("checkbox", { name: "Evaluate incumbent after training" })
        .hasAttribute("disabled")
    ).toBe(true);
    expect(
      screen.getByRole("checkbox", { name: "Evaluate base model" }).getAttribute("aria-checked")
    ).toBe("true");
  });

  it.each([
    ["Evaluate incumbent baseline", "evalIncumbentBefore"],
    ["Evaluate incumbent after training", "evalIncumbentAfter"],
    ["Evaluate base model", "evalModelBefore"],
    ["Evaluate trained model", "evalModelAfter"],
  ])("allows independent selection: %s", (name, field) => {
    const setEvaluationChoice = vi.fn();
    setup({
      evaluationPlan: {
        evalIncumbentAfter: false,
        evalIncumbentBefore: false,
        evalModelAfter: false,
        evalModelBefore: false,
      },
      hasIncumbent: true,
      setEvaluationChoice,
    });
    fireEvent.click(screen.getByRole("checkbox", { name }).closest("label")!);
    expect(setEvaluationChoice).toHaveBeenCalledWith(field, true);
  });

  it("shows the dataset and evaluation inputs without a capability", () => {
    setup();

    expect(screen.queryByText("Select a capability first.")).toBeNull();
    expect(screen.getByLabelText("Capability (optional)").textContent).toContain("None");
    expect(screen.getByRole("combobox", { name: "Training Dataset" }).textContent).toContain(
      TRAIN_SET.name
    );
    expect(screen.getByRole("combobox", { name: "Eval Dataset" }).textContent).toContain(
      EVAL_SET.name
    );
    expect(screen.getByRole("combobox", { name: "Eval set" }).textContent).toContain("Default");
    expect(screen.getByText(/12 training rows overlap this eval dataset/)).toBeTruthy();
  });

  it("keeps None available when the project has no capabilities", () => {
    setup({ capabilities: [] });
    expect(screen.getByLabelText("Capability (optional)").textContent).toContain("None");
    expect(screen.getByRole("combobox", { name: "Training Dataset" })).toBeTruthy();
  });

  it("warns on the rows the two datasets share once the capability is selected", () => {
    setup({ capability: CAPABILITY, capabilityId: "capability-1" });

    expect(screen.getByText(/12 training rows overlap this eval dataset/)).toBeTruthy();
  });

  it("describes no eval dataset while the capability has none to pick", () => {
    setup({ capability: CAPABILITY, capabilityId: "capability-1", evalDatasets: [] });

    expect(screen.getByText("No eval datasets for this capability.")).toBeTruthy();
    expect(screen.queryByText(/also appear in the training set/)).toBeNull();
  });
});
