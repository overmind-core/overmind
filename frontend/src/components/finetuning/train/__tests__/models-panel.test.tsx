// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The @lobehub icon ESM subpaths behind the provider chip don't resolve under vitest's
// node resolver; the chip's chrome isn't the assertion.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: () => ({}),
  getProviderIcon: () => null,
  ModelProviderChip: ({ model }: { model: string }) => <span>{model}</span>,
  ProviderLogo: () => null,
}));

import type { ModelDraft } from "@/components/finetuning/train/model-config";
import { ModelsPanel } from "@/components/finetuning/train/models-panel";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { ModelCatalog } from "@/hooks/use-finetuning";
import type {
  Dataset,
  FinetuningEvidence,
  FinetuningExperiment,
  FinetuningRecommendationResponse,
  FinetuningSkillScore,
} from "@/openapi";

afterEach(cleanup);

const SKILL_SCORES: FinetuningSkillScore[] = [
  {
    fieldN: 30,
    percentileGlobal: 74.4,
    percentileInField: 98.3,
    rankInField: 1,
    skill: "Instruction Following",
    weight: 0.5,
  },
];

const evidence = (benchmark: string): FinetuningEvidence => ({
  benchmark,
  cohortN: 450,
  percentile: 88.2,
  provenance: "measured",
  skill: "Instruction Following",
  source: "Artificial Analysis",
  url: "https://artificialanalysis.ai/evaluations/ifbench",
});

function draft(id: string, model: string, displayName: string): ModelDraft {
  return {
    confidence: "high",
    displayName,
    evidence: [evidence(`${displayName}-bench`), evidence("IFBench")],
    grade: 78,
    hyperparams: { batch_size: 8, learning_rate: 1e-4, n_epochs: 3, warmup_ratio: 0.05 },
    id,
    labClaimedOnly: false,
    learningRateFull: 1e-5,
    learningRateLora: 1e-4,
    loraParams: { lora_alpha: 32, lora_dropout: 0, lora_r: 16 },
    match: 98,
    matchPool: 30,
    matchRank: 1,
    maxBatchSize: 32,
    minBatchSize: 1,
    model,
    nBenchmarks: 2,
    params: "27B",
    skillScores: SKILL_SCORES,
    supportsFull: false,
    supportsLora: true,
    tier: "mid",
    useLora: true,
  };
}

const DRAFTS = [
  draft("d-1", "Qwen/Qwen3.5-27B", "Qwen3.5 27B"),
  draft("d-2", "meta/llama-3.3-8b", "Llama 3.3 8B"),
];

const REC = {
  benchmarkSnapshot: {
    generatedAt: "2026-08-11",
    sources: [
      {
        attributionRequired: false,
        name: "Artificial Analysis",
        url: "https://artificialanalysis.ai/evaluations/",
      },
      {
        attributionRequired: false,
        name: "HuggingFace eval results",
        url: "https://huggingface.co",
      },
    ],
  },
  candidates: [],
  dataset: { hasToolCalling: false, maxTokenLength: 193, rows: 342, totalTokens: 60_000 },
  excluded: [],
  selected: [],
  skillWeights: { Faithfulness: 0.3, "Instruction Following": 0.5, Reasoning: 0.2 },
  taskType: "extraction",
  taskTypeSource: "heuristic",
} as unknown as FinetuningRecommendationResponse;

const CANDIDATES = [
  { evidence: [evidence("IFBench"), evidence("AA-LCR")], model: "Qwen/Qwen3.5-27B" },
] as unknown as FinetuningExperiment[];

function wizard(over: Partial<TrainWizard>): TrainWizard {
  return {
    addModel: () => {},
    candidateByModel: new Map<string, FinetuningExperiment>(),
    candidates: CANDIDATES,
    catalog: { models: {}, tiers: [] } as unknown as ModelCatalog,
    catalogModelById: () => null,
    dataReady: true,
    drafts: DRAFTS,
    estimates: new Map(),
    evalDataset: { activeVersion: "1.0", rows: 30 } as unknown as Dataset,
    excluded: [],
    rec: REC,
    recommendQuery: { error: null, isLoading: false },
    removeModel: () => {},
    replaceDraftModel: () => {},
    selectedDrafts: DRAFTS,
    toggleSelected: () => {},
    updateDraft: () => {},
    validation: { stats: { trainExamples: 273, valExamples: 69 }, valid: true },
    ...over,
  } as unknown as TrainWizard;
}

function setup(over: Partial<TrainWizard> = {}) {
  return render(
    <TooltipProvider>
      <ModelsPanel wizard={wizard(over)} />
    </TooltipProvider>
  );
}

const cards = () =>
  within(screen.getByRole("list", { name: "Experiments" })).getAllByRole("listitem");

describe("candidate list", () => {
  it("stacks the candidates one to a row", () => {
    setup();

    expect(screen.getByRole("list", { name: "Experiments" }).className).toContain("flex-col");
    expect(cards()).toHaveLength(2);
  });

  it("opens the evidence chart under the card it belongs to", () => {
    setup();
    const [first, second] = cards();

    fireEvent.click(within(first).getByRole("button", { name: "Why this model" }));

    expect(within(first).getByText("Instruction Following")).toBeTruthy();
    expect(within(second).queryByText("Instruction Following")).toBeNull();
  });

  it("opens the tuning fields under the card it belongs to", () => {
    setup();
    const [first, second] = cards();

    fireEvent.click(within(second).getByRole("button", { name: "Parameters" }));

    expect(within(second).getByLabelText("Epochs")).toBeTruthy();
    expect(within(first).queryByLabelText("Epochs")).toBeNull();
  });

  it("closes the disclosure on a second click of the same tab", () => {
    setup();
    const [first] = cards();
    const tab = within(first).getByRole("button", { name: "Parameters" });

    fireEvent.click(tab);
    fireEvent.click(tab);

    expect(within(first).queryByLabelText("Epochs")).toBeNull();
  });
});

describe("analysis strip", () => {
  it("states the task type, the blend and the run split on one line each", () => {
    setup();

    expect(screen.getByText("Extraction").getAttribute("title")).toBe(
      "Classified from dataset structure"
    );
    expect(screen.getByText("342 rows")).toBeTruthy();
    expect(screen.getByText("Instruction Following 50%")).toBeTruthy();
    expect(screen.getByText("Reasoning 20%")).toBeTruthy();
    expect(screen.getByText("Train")).toBeTruthy();
    expect(screen.getByText("273")).toBeTruthy();
  });

  it("keeps the snapshot date off the page and the sources out of the strip", () => {
    setup();

    expect(screen.getByText("2 benchmarks").getAttribute("title")).toContain("2026");
    expect(screen.queryByText(/2026/)).toBeNull();
    expect(screen.queryByText(/Artificial Analysis/)).toBeNull();
    expect(screen.queryByText(/HuggingFace/)).toBeNull();
  });
});
