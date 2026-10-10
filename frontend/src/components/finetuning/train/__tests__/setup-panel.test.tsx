// @vitest-environment jsdom

import type { ReactNode } from "react";

import { cleanup, render, screen } from "@testing-library/react";
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
    benchmarkingEnabled: false,
    benchmarkModel: "",
    benchmarkModels: [],
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
    evaluationEnabled: true,
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
    selectedDrafts: [],
    setBenchmarkingEnabled: () => {},
    setCapabilityId: () => {},
    setDatasetId: () => {},
    setEvalDatasetId: () => {},
    setEvalSetId: () => {},
    setEvaluationChoice: () => {},
    setEvaluationEnabled: () => {},
    setRunName: () => {},
    toggleBenchmarkModel: () => {},
    validating: false,
    validation: VALIDATION,
    ...over,
  } as unknown as TrainWizard;
}

function setup(over: Partial<TrainWizard> = {}, section: "data" | "evaluation" = "evaluation") {
  return render(
    <TooltipProvider>
      <SetupPanel projectId="p-1" section={section} wizard={wizard(over)} />
    </TooltipProvider>
  );
}

describe("SetupPanel", () => {
  it("puts eval set first, keeps its judge override attached, and separates benchmarking", () => {
    setup({
      benchmarkingEnabled: false,
      benchmarkModels: ["openai/gpt-5.6-sol"],
      benchmarkOptions: [
        { kind: "Codebase incumbent", label: "Incumbent", value: "openai/gpt-5.6-sol" },
      ],
      selectedDrafts: [{ id: "draft-1", model: "qwen/qwen3-8b" }],
    } as Partial<TrainWizard>);
    const evalSet = screen.getByRole("combobox", { name: "Eval set" });
    const dataset = screen.getByRole("combobox", { name: "Eval Dataset" });
    expect(
      evalSet.compareDocumentPosition(dataset) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Change judge model" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Benchmark models" })).toBeTruthy();
    expect(screen.queryByRole("group", { name: "Training model evaluations" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Incumbent evaluations" })).toBeNull();
  });

  it("keeps native evaluation details separate from its heading toggle", () => {
    setup({ nativeDecision: true, preTrainingBaseline: true });
    expect(
      screen.queryByRole("checkbox", { name: "Run pre-training baseline evaluation" })
    ).toBeNull();
    expect(screen.getByText(/Native probability training/)).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: /Validation source/ })).toBeNull();
  });

  it("renders evaluation details without a second toggle row", () => {
    setup();
    expect(screen.queryByRole("checkbox", { name: "Run evaluations" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Training Dataset" })).toBeNull();
    expect(screen.getByRole("combobox", { name: "Eval Dataset" })).toBeTruthy();
    expect(screen.getByRole("combobox", { name: "Eval set" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Benchmark models" })).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Validation source" })).toBeNull();
  });

  it("shows only training inputs when evaluations are off", () => {
    setup({ evaluationEnabled: false });
    expect(screen.queryByRole("combobox", { name: "Training Dataset" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Eval Dataset" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Eval set" })).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Benchmark model" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Run evaluations" })).toBeNull();
  });

  it("shows the selected training base and incumbent when benchmarking is on", () => {
    setup({
      benchmarkingEnabled: true,
      benchmarkModels: ["openai/gpt-5.6-sol", "qwen/qwen3-8b"],
      benchmarkOptions: [
        { kind: "Codebase incumbent", label: "Incumbent", value: "openai/gpt-5.6-sol" },
      ],
      selectedDrafts: [{ id: "draft-1", model: "qwen/qwen3-8b" }],
    } as Partial<TrainWizard>);
    expect(screen.getByText("Compare with")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Remove Incumbent from benchmarks/ })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: /Remove qwen\/qwen3-8b from benchmarks/ })
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add model" })).toBeTruthy();
  });

  it("keeps workshop review recommendations out of training setup", () => {
    const readiness = {
      assessment: {},
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
  it("shows training inputs without a capability", () => {
    setup({}, "data");

    expect(screen.queryByText("Select a capability first.")).toBeNull();
    expect(screen.getByLabelText("Capability (optional)").textContent).toContain("None");
    expect(screen.getByRole("combobox", { name: "Training Dataset" }).textContent).toContain(
      TRAIN_SET.name
    );
    expect(screen.queryByRole("combobox", { name: "Eval Dataset" })).toBeNull();
  });

  it("keeps None available when the project has no capabilities", () => {
    setup({ capabilities: [] }, "data");
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

it("keeps validation at the standard split without an extra selector", () => {
  setup({}, "data");
  expect(screen.queryByRole("combobox", { name: "Validation source" })).toBeNull();
  expect(screen.queryByText(/20% of training data/)).toBeNull();
  expect(screen.getByRole("button", { name: "About training dataset" })).toBeTruthy();
});
