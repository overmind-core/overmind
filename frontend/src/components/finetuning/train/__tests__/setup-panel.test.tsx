// @vitest-environment jsdom

import type { ReactNode } from "react";

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SetupPanel } from "@/components/finetuning/train/setup-panel";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Dataset, DatasetValidationResponse } from "@/openapi";

afterEach(cleanup);

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, params }: { children: ReactNode; params: { datasetId: string } }) => (
    <a href={`/datasets/${params.datasetId}`}>{children}</a>
  ),
}));

vi.mock("@/components/model-provider-chip", () => ({
  ModelProviderChip: ({ model }: { model: string }) => <span title={model}>{model}</span>,
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
  it("puts benchmark selection in the training setup grid", () => {
    const benchmark = {
      kind: "Trained model",
      label: "Support · Qwen",
      value: "ft-12345678-qwen3-8b",
    };
    setup({
      benchmarkModel: benchmark.value,
      benchmarkOptions: [benchmark],
      selectedBenchmark: benchmark,
    });
    const selector = screen.getByRole("combobox", { name: "Benchmark model" });
    expect(within(selector).getByTitle(benchmark.value).getAttribute("data-slot")).toBe("badge");
    expect(selector.textContent).toContain("FT");
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
