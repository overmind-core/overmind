// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { useTrainWizard } from "../use-train-wizard";

const mocks = vi.hoisted(() => ({
  benchmarks: [{ baseModelId: "Qwen", finetuningJobName: "Support", modelId: "ft-trained" }],
  catalog: { backend: "baseten", models: {}, tiers: [] },
  context: {
    data: { checks: [{ message: "Context may be too small.", status: "warning" }] },
    isError: false,
  },
  create: vi.fn(),
  estimate: vi.fn(),
  estimateResults: [] as unknown[],
  evals: {
    results: [
      { active: "eval-cell", capability: null, id: "eval", name: "Evaluation", project: "project" },
    ],
  },
  overlapCount: 0,
  queries: vi.fn(),
  recommend: vi.fn(),
  recommendation: {
    candidates: [
      {
        displayName: "Model",
        hyperparams: {},
        learningRateFull: 0.0001,
        learningRateLora: 0.0001,
        model: "model",
        selected: true,
        tier: "small",
      },
    ],
    excluded: [] as Array<{ model: string; reason: string }>,
    shown: ["model"],
  },
  sets: {
    results: [
      { capability: null, id: "foreign", name: "Other project", project: "other" },
      { capability: null, id: "set", name: "General", project: "project" },
    ],
  },
  train: { results: [{ active: "train-cell", capability: "cap", id: "train", name: "Training" }] },
  validate: vi.fn(),
}));
vi.mock("@/client", () => ({
  default: { finetuningJobs: { finetuningJobsEstimateCreate: mocks.estimate } },
}));
vi.mock("@tanstack/react-query", () => ({
  useQueries: (options: { queries: unknown[] }) => {
    mocks.queries(options);
    return options.queries.map((_, index) => ({ data: mocks.estimateResults[index] }));
  },
  useQuery: () => mocks.context,
}));
vi.mock("@/components/finetuning/train/model-picker", () => ({ findCatalogModel: () => null }));
vi.mock("@/hooks/use-capability-eval-preload", () => ({ useCapabilityEvalPreload: () => ({}) }));
vi.mock("@/hooks/use-subscription", () => ({ useCredits: () => ({ hasCredits: true }) }));
vi.mock("@/hooks/use-evaluations", () => ({
  useEvalSetsQuery: () => ({ data: mocks.sets }),
  useProjectCapabilitiesQuery: () => ({
    data: { results: [{ id: "cap", model: "openai/gpt-5.6-sol" }] },
  }),
  useProjectDatasetsForEvalQuery: () => ({ data: mocks.evals }),
}));
vi.mock("@/hooks/use-finetuning", () => ({
  defaultFinetuneName: () => "Training",
  useCreateFinetuningJobsMutation: () => ({ mutateAsync: mocks.create }),
  useDatasetOverlapQuery: () => ({ data: { overlapCount: mocks.overlapCount } }),
  useModelCatalogQuery: () => ({ data: mocks.catalog }),
  useProjectDatasetsQuery: () => ({ data: mocks.train }),
  useRecommendModelsQuery: (...args: unknown[]) => {
    mocks.recommend(...args);
    return { data: mocks.recommendation };
  },
  useTrainingBenchmarksQuery: () => ({ data: mocks.benchmarks }),
  useValidateDatasetMutation: () => ({ mutateAsync: mocks.validate }),
}));
beforeEach(() => {
  mocks.recommendation.excluded = [];
  mocks.recommendation.candidates = [mocks.recommendation.candidates[0]];
  mocks.recommendation.shown = ["model"];
  mocks.estimateResults = [];
  mocks.catalog.backend = "baseten";
  mocks.overlapCount = 0;
  mocks.train.results[0].capability = "cap";
  mocks.validate.mockResolvedValue({ valid: true });
  mocks.create.mockResolvedValue([]);
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const args = {
  groupId: "group",
  initialDatasetId: "train",
  onLaunched: vi.fn(),
  projectId: "project",
};

it("summarizes selected model ranges without falling back to a point estimate", async () => {
  mocks.recommendation.shown.push("second-model");
  mocks.recommendation.candidates.push({
    ...mocks.recommendation.candidates[0],
    model: "second-model",
  });
  mocks.estimateResults = [
    {
      forecast: { training_seconds: [60, 600] },
      timeEstimate: { human: "1–10 min", seconds: null },
    },
    {
      forecast: { training_seconds: [120, 300] },
      timeEstimate: { human: "2–5 min", seconds: null },
    },
  ];
  const { result, rerender } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.selectedDrafts).toHaveLength(2));
  expect(result.current.totals.longest).toBe("2–10 min");
  mocks.estimateResults = [mocks.estimateResults[0], undefined];
  rerender();
  expect(result.current.totals.longest).toBeNull();
});

it("keeps context warnings advisory", async () => {
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.contextQuery.data?.checks[0].status).toBe("warning");
});

it("sends the explicit judge only for this setup and restores defaults on set changes", async () => {
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.judgeModel).toBe("");
  act(() => result.current.setJudgeModel("gpt-5.6-luna"));
  expect(result.current.canLaunch).toBe(true);
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({ evalJudgeModel: "gpt-5.6-luna" }),
  ]);
  act(() => result.current.setEvalSetId("different-set"));
  expect(result.current.judgeModel).toBe("");
});

it("blocks an already selected model for a non-context compatibility failure", async () => {
  const { result, rerender } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  mocks.recommendation = {
    ...mocks.recommendation,
    excluded: [{ model: "model", reason: "Model has no supported training method." }],
  };
  rerender();
  expect(result.current.launchBlocker).toBe("Model has no supported training method.");
  expect(result.current.canLaunch).toBe(false);
});

it("launches Modal jobs without starting or waiting for setup preprocessing", async () => {
  mocks.catalog.backend = "modal";
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.selectedDrafts).toHaveLength(1);
  const queries = mocks.queries.mock.calls.flatMap(([options]) => options.queries);
  expect(queries.length).toBeGreaterThan(0);
  expect(queries.every((query) => query.queryKey[0] === "finetuning-estimate")).toBe(true);
  expect(mocks.validate).toHaveBeenCalledExactlyOnceWith({
    cellId: "train-cell",
    datasetId: "train",
    splitMethod: "random",
    validationDatasetId: null,
    validationEnabled: true,
    validationSplitRatio: 0.2,
  });
  act(() => result.current.setRunName("Support training"));
  expect(mocks.validate).toHaveBeenCalledTimes(1);
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({
      baseModel: "model",
      cell: "train-cell",
      dataset: "train",
      evalCell: "eval-cell",
      evalDataset: "eval",
      splitMethod: "random",
      validationDataset: null,
      validationEnabled: true,
      validationSplitRatio: 0.2,
    }),
  ]);
});

it("allows launch with overlap and a different dataset capability", async () => {
  mocks.overlapCount = 2;
  mocks.train.results[0].capability = "other-capability";
  const { result } = renderHook(() => useTrainWizard({ ...args, initialCapabilityId: "cap" }));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.datasets.map((dataset) => dataset.id)).toContain("train");
  expect(result.current.overlapCount).toBe(2);
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalled();
});

it("defaults to the codebase incumbent and sends the selected benchmark with this run", async () => {
  const { result } = renderHook(() => useTrainWizard({ ...args, initialCapabilityId: "cap" }));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.benchmarkModel).toBe("openai/gpt-5.6-sol");
  expect(result.current.benchmarkOptions.map((option) => option.value)).toEqual([
    "openai/gpt-5.6-sol",
    "ft-trained",
  ]);
  act(() => {
    result.current.setBenchmarkModel("ft-trained");
    result.current.setEvaluationChoice("evalIncumbentBefore", true);
    result.current.setEvaluationChoice("evalIncumbentAfter", true);
  });
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({
      baselineModel: "ft-trained",
      evalIncumbentAfter: true,
      evalIncumbentBefore: true,
    }),
  ]);
  act(() => result.current.setBenchmarkModel("openai/gpt-5.6-sol"));
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenLastCalledWith([
    expect.objectContaining({ baselineModel: "openai/gpt-5.6-sol" }),
  ]);
});

it("allows benchmarking a trained model without a capability", async () => {
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  act(() => {
    result.current.setBenchmarkModel("ft-trained");
    result.current.setEvaluationChoice("evalIncumbentBefore", true);
  });
  expect(result.current.hasIncumbent).toBe(true);
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({
      baselineModel: "ft-trained",
      capability: null,
      evalIncumbentBefore: true,
    }),
  ]);
});

it("resets the benchmark when changing capability", async () => {
  const { result } = renderHook(() => useTrainWizard({ ...args, initialCapabilityId: "cap" }));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  act(() => result.current.setBenchmarkModel("ft-trained"));
  act(() => result.current.setCapabilityId(""));
  expect(result.current.benchmarkModel).toBe("");
  expect(result.current.hasIncumbent).toBe(false);
});

it("does not silently switch away from an unavailable selected benchmark", async () => {
  const { result } = renderHook(() => useTrainWizard({ ...args, initialCapabilityId: "cap" }));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  act(() => result.current.setBenchmarkModel("ft-deleted"));
  expect(result.current.launchBlocker).toBe("Select an available benchmark model");
  await act(() => result.current.launch());
  expect(mocks.create).not.toHaveBeenCalled();
});

it("launches with no capability and a project-scoped unassigned eval set", async () => {
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.capabilityId).toBe("");
  expect(result.current.evalSets.map((set) => set.id)).toEqual(["set"]);
  expect(mocks.recommend).toHaveBeenCalledWith("train", undefined, "eval");
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({
      capability: null,
      cell: "train-cell",
      dataset: "train",
      evalCell: "eval-cell",
      evalDataset: "eval",
      evalIncumbentAfter: false,
      evalIncumbentBefore: false,
      evalModelAfter: true,
      evalModelBefore: true,
      evalSet: "set",
    }),
  ]);
});

it("preserves independently selected eval choices in the launch payload", async () => {
  const { result } = renderHook(() => useTrainWizard({ ...args, initialCapabilityId: "cap" }));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.evaluationPlan).toEqual({
    evalIncumbentAfter: false,
    evalIncumbentBefore: false,
    evalModelAfter: true,
    evalModelBefore: true,
  });
  act(() => {
    result.current.setEvaluationChoice("evalIncumbentBefore", false);
    result.current.setEvaluationChoice("evalIncumbentAfter", true);
    result.current.setEvaluationChoice("evalModelBefore", true);
    result.current.setEvaluationChoice("evalModelAfter", false);
  });
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({
      evalIncumbentAfter: true,
      evalIncumbentBefore: false,
      evalModelAfter: false,
      evalModelBefore: true,
    }),
  ]);
});

it("allows all evals off without bypassing dataset and eval-set selection", async () => {
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  act(() => {
    result.current.setEvaluationChoice("evalModelBefore", false);
    result.current.setEvaluationChoice("evalModelAfter", false);
  });
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledWith([
    expect.objectContaining({
      evalIncumbentAfter: false,
      evalIncumbentBefore: false,
      evalModelAfter: false,
      evalModelBefore: false,
    }),
  ]);
});

it("does not infer a capability from dataset picks and keeps unassigned eval sets available", async () => {
  const { result } = renderHook(() => useTrainWizard({ ...args, initialCapabilityId: "cap" }));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  act(() => result.current.setCapabilityId(""));
  act(() => result.current.setDatasetId("train"));
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  expect(result.current.capabilityId).toBe("");
});

it.each(["evals", "sets"] as const)("still requires %s when capability is none", async (field) => {
  const savedEvals = mocks.evals;
  const savedSets = mocks.sets;
  mocks[field] = { results: [] };
  try {
    const { result } = renderHook(() => useTrainWizard(args));
    await waitFor(() => expect(result.current.dataReady).toBe(true));
    expect(result.current.canLaunch).toBe(false);
    expect(result.current.launchBlocker).toBe(
      field === "evals" ? "Select an eval dataset" : "Select an eval set"
    );
  } finally {
    mocks.evals = savedEvals;
    mocks.sets = savedSets;
  }
});

it.each([
  true,
  false,
])("launches a native contract with baseline %s and reuses its request identity", async (baseline) => {
  mocks.catalog.backend = "modal";
  mocks.validate.mockResolvedValue({ format: "decision", valid: true });
  const { result } = renderHook(() => useTrainWizard(args));
  await waitFor(() => expect(result.current.drafts.length).toBeGreaterThan(0));
  act(() =>
    result.current.updateDraft(result.current.drafts[0].id, {
      ...result.current.drafts[0],
      useLora: true,
    })
  );
  await waitFor(() => expect(result.current.canLaunch).toBe(true));
  act(() => {
    result.current.setEvalDatasetId("");
    result.current.setEvalSetId("");
  });
  expect(result.current.preTrainingBaseline).toBe(true);
  act(() => result.current.setPreTrainingBaseline(baseline));
  const queries = mocks.queries.mock.calls.at(-1)?.[0].queries;
  await queries[0].queryFn();
  expect(mocks.estimate).toHaveBeenLastCalledWith({
    finetuningEstimateRequestRequest: expect.objectContaining({
      hyperparameters: expect.objectContaining({ pre_training_baseline: baseline }),
      validationEnabled: true,
    }),
  });
  await act(() => result.current.launch());
  await act(() => result.current.launch());
  expect(mocks.create).toHaveBeenCalledTimes(2);
  const first = mocks.create.mock.calls[0][0][0];
  expect(first).toEqual(
    expect.objectContaining({
      evalDataset: null,
      evalModelAfter: false,
      evalModelBefore: false,
      evalSet: null,
      hyperparameters: expect.objectContaining({
        objective: "decision_cross_entropy",
        pre_training_baseline: baseline,
      }),
      validationEnabled: true,
    })
  );
  expect(first.requestKey).toBe(mocks.create.mock.calls[1][0][0].requestKey);
});

it("pins separate validation identically for checks, estimates and launch", async () => {
  const saved = mocks.train;
  mocks.train = {
    results: [
      ...saved.results,
      { active: "validation-cell", capability: "cap", id: "holdout", name: "Holdout" },
    ],
  };
  try {
    const { result } = renderHook(() => useTrainWizard(args));
    await waitFor(() => expect(result.current.canLaunch).toBe(true));
    act(() => result.current.setValidationMode("external"));
    expect(result.current.canLaunch).toBe(false);
    act(() => result.current.setValidationDatasetId("holdout"));
    await waitFor(() => expect(result.current.canLaunch).toBe(true));
    expect(mocks.validate).toHaveBeenLastCalledWith(
      expect.objectContaining({
        cellId: "train-cell",
        validationCellId: "validation-cell",
        validationDatasetId: "holdout",
        validationEnabled: true,
      })
    );
    const options = mocks.queries.mock.lastCall?.[0];
    await options.queries[0].queryFn();
    expect(mocks.estimate).toHaveBeenLastCalledWith({
      finetuningEstimateRequestRequest: expect.objectContaining({
        cell: "train-cell",
        validationCell: "validation-cell",
        validationEnabled: true,
      }),
    });
    await act(() => result.current.launch());
    expect(mocks.create).toHaveBeenLastCalledWith([
      expect.objectContaining({
        cell: "train-cell",
        validationCell: "validation-cell",
        validationDataset: "holdout",
        validationEnabled: true,
      }),
    ]);
    act(() => result.current.setValidationMode("none"));
    await waitFor(() => expect(result.current.canLaunch).toBe(true));
    await act(() => result.current.launch());
    expect(mocks.create).toHaveBeenLastCalledWith([
      expect.objectContaining({
        validationCell: undefined,
        validationDataset: null,
        validationEnabled: false,
      }),
    ]);
  } finally {
    mocks.train = saved;
  }
});
