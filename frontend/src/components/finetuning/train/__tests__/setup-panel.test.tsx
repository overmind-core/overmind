// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SetupPanel } from "@/components/finetuning/train/setup-panel";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Dataset, DatasetValidationResponse } from "@/openapi";

afterEach(cleanup);

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

/** Every pick already made, so only `capabilityId` decides what the controls can show. */
function wizard(over: Partial<TrainWizard>): TrainWizard {
  return {
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
    evaluationReady: false,
    overlapCount: 12,
    runName: "Support · transcripts",
    setCapabilityId: () => {},
    setDatasetId: () => {},
    setEvalDatasetId: () => {},
    setEvalSetId: () => {},
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
  it("describes no dataset while no capability is selected", () => {
    setup();

    expect(screen.getAllByText("Select a capability first.")).toHaveLength(3);
    expect(screen.queryByText(/\d+ rows?/)).toBeNull();
    expect(screen.queryByText("workshop")).toBeNull();
    expect(screen.queryByText("custom")).toBeNull();
    expect(screen.queryByText(/longest/)).toBeNull();
    expect(screen.queryByText(/also appear in the training set/)).toBeNull();
  });

  it("warns on the rows the two datasets share once the capability is selected", () => {
    setup({ capability: CAPABILITY, capabilityId: "capability-1" });

    expect(screen.getByText(/12 rows also appear in the training set/)).toBeTruthy();
  });

  it("describes no eval dataset while the capability has none to pick", () => {
    setup({ capability: CAPABILITY, capabilityId: "capability-1", evalDatasets: [] });

    expect(screen.getByText("No eval datasets for this capability.")).toBeTruthy();
    expect(screen.queryByText(/also appear in the training set/)).toBeNull();
  });

  it("offers the run name before anything is picked", () => {
    setup();

    expect(screen.getByLabelText("Name")).toBeTruthy();
  });
});
