import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useQueries, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import {
  applyCandidateGrade,
  applyCatalogTrainingFlags,
  buildHyperparameters,
  draftFromCatalogModel,
  draftFromRecommendation,
  isUntuned,
  MAX_MODELS,
  type ModelDraft,
  TIER_ORDER,
} from "@/components/finetuning/train/model-config";
import { findCatalogModel } from "@/components/finetuning/train/model-picker";
import { useCapabilityEvalPreload } from "@/hooks/use-capability-eval-preload";
import {
  useEvalSetsQuery,
  useModelCatalogQuery as useEvaluationModelCatalogQuery,
  useProjectCapabilitiesQuery,
  useProjectDatasetsForEvalQuery,
} from "@/hooks/use-evaluations";
import {
  defaultFinetuneName,
  type ModelCatalog,
  type ModelEntry,
  useCreateFinetuningJobsMutation,
  useDatasetOverlapQuery,
  useModelCatalogQuery,
  useProjectDatasetsQuery,
  useRecommendModelsQuery,
  useTrainingBenchmarksQuery,
  useValidateDatasetMutation,
} from "@/hooks/use-finetuning";
import { useCredits } from "@/hooks/use-subscription";
import { isPaymentRequired } from "@/lib/credits";
import { errorMessage } from "@/lib/notify";
import type {
  DatasetValidationResponse,
  EvaluationContextRequestRequest,
  FinetuningEstimateResponse,
  FinetuningExperiment,
  FinetuningJobRequest,
  FinetuningJobRequestModelTierEnum,
} from "@/openapi";
import { BackendEnum } from "@/openapi";
import { defaultMonitoring, type MonitoringOptions } from "./monitoring-controls";

const VALIDATION_SPLIT = {
  splitMethod: "random",
  validationEnabled: true,
  validationSplitRatio: 0.2,
} as const;

export type EvaluationPlan = Required<
  Pick<
    FinetuningJobRequest,
    "evalIncumbentBefore" | "evalIncumbentAfter" | "evalModelBefore" | "evalModelAfter"
  >
>;

const EMPTY_CATALOG: ModelCatalog = {
  backend: BackendEnum.baseten,
  hasToolCalling: false,
  maxContext: null,
  models: {},
  monitoring: {},
  tiers: [...TIER_ORDER],
};

const FAILED_VALIDATION: DatasetValidationResponse = {
  errors: ["Couldn't validate this dataset."],
  format: "unknown",
  numExamples: 0,
  stats: {},
  valid: false,
  warnings: [],
};

export interface TrainWizardArgs {
  projectId: string;
  groupId: string;
  initialCapabilityId?: string;
  initialDatasetId?: string;
  initialEvalDatasetId?: string;
  onLaunched: (groupId: string) => void;
}

export function useTrainWizard({
  projectId,
  groupId,
  initialCapabilityId,
  initialDatasetId,
  initialEvalDatasetId,
  onLaunched,
}: TrainWizardArgs) {
  const [capabilityId, setCapabilityIdState] = useState(initialCapabilityId ?? "");
  const [datasetId, setDatasetIdState] = useState(initialDatasetId ?? "");
  const [preTrainingBaseline, setPreTrainingBaseline] = useState(true);
  const [monitoringChoice, setMonitoring] = useState<MonitoringOptions | null>(null);
  const [validation, setValidation] = useState<DatasetValidationResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [evalDatasetId, setEvalDatasetId] = useState(initialEvalDatasetId ?? "");
  const [evaluationEnabled, setEvaluationEnabled] = useState(Boolean(initialEvalDatasetId));
  const [benchmarkingEnabled, setBenchmarkingEnabled] = useState(false);
  const [evalSetId, setEvalSetId] = useState("");
  const [judgeChoice, setJudgeChoice] = useState<{ setId: string; model: string } | null>(null);
  const judgeModel = judgeChoice?.setId === evalSetId ? judgeChoice.model : "";
  const setJudgeModel = (model: string) => setJudgeChoice({ model, setId: evalSetId });
  const [benchmarkSelection, setBenchmarkSelection] = useState<string[] | null>(null);
  const [drafts, setDrafts] = useState<ModelDraft[]>([]);
  const [deselected, setDeselected] = useState<Set<string>>(new Set());
  const [runName, setRunName] = useState("");
  const [runNameDirty, setRunNameDirty] = useState(false);
  const [seededFor, setSeededFor] = useState("");
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const { hasCredits } = useCredits();

  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilities = useMemo(
    () => capabilitiesQuery.data?.results ?? [],
    [capabilitiesQuery.data]
  );
  const capability = capabilities.find((a) => a.id === capabilityId);
  const benchmarksQuery = useTrainingBenchmarksQuery(projectId);
  const evaluationCatalogQuery = useEvaluationModelCatalogQuery();
  const benchmarkOptions = useMemo<
    { kind: string; label: string; value: string; baseModelId?: string }[]
  >(() => {
    const incumbent = capability?.model?.trim();
    const options = [
      ...(incumbent ? [{ kind: "Codebase incumbent", label: incumbent, value: incumbent }] : []),
      ...(evaluationCatalogQuery.data?.models ?? []).map((model) => ({
        kind: "OpenRouter",
        label: model.name,
        value: model.id,
      })),
      ...(benchmarksQuery.data ?? [])
        .filter((model) => model.modelId !== incumbent)
        .map((model) => ({
          baseModelId: model.baseModelId || undefined,
          kind: "Trained model",
          label: `${model.finetuningJobName || model.modelId}${model.baseModelId ? ` · ${model.baseModelId}` : ""}`,
          value: model.modelId,
        })),
    ];
    return options.filter(
      (option, index) => options.findIndex((row) => row.value === option.value) === index
    );
  }, [capability?.model, benchmarksQuery.data, evaluationCatalogQuery.data]);
  const benchmarkModels =
    benchmarkSelection ?? (capability?.model?.trim() ? [capability.model.trim()] : []);
  const benchmarkModel = benchmarkModels[0] ?? "";
  const selectedBenchmark = benchmarkOptions.find((option) => option.value === benchmarkModel);
  const nativeDecision = validation?.format === "decision";
  const hasIncumbent =
    evaluationEnabled && benchmarkingEnabled && !nativeDecision && Boolean(selectedBenchmark);
  const evaluationPlan = useMemo<EvaluationPlan>(
    () => ({
      evalIncumbentAfter: false,
      evalIncumbentBefore: hasIncumbent,
      evalModelAfter: evaluationEnabled && !nativeDecision,
      evalModelBefore: evaluationEnabled && benchmarkingEnabled && !nativeDecision,
    }),
    [hasIncumbent, evaluationEnabled, benchmarkingEnabled, nativeDecision]
  );
  const toggleBenchmarkModel = useCallback(
    (model: string) => {
      setBenchmarkSelection((previous) => {
        const selected = previous ?? (capability?.model?.trim() ? [capability.model.trim()] : []);
        return selected.includes(model)
          ? selected.filter((id) => id !== model)
          : [...selected, model];
      });
    },
    [capability?.model]
  );

  const datasetsQuery = useProjectDatasetsQuery(projectId);
  const datasets = useMemo(() => datasetsQuery.data?.results ?? [], [datasetsQuery.data]);
  const dataset = datasets.find((d) => d.id === datasetId);
  const decisionObjective =
    dataset?.cells?.find((cell) => cell.id === dataset.active)?.intentReport?.train?.objective ??
    "decision_cross_entropy";
  const validationSelection = VALIDATION_SPLIT;

  const evalDatasetsQuery = useProjectDatasetsForEvalQuery(projectId);
  const evalDatasets = useMemo(
    () => evalDatasetsQuery.data?.results ?? [],
    [evalDatasetsQuery.data]
  );
  const evalDataset = evalDatasets.find((d) => d.id === evalDatasetId);

  useEffect(() => {
    if (evalDatasetId || evalDatasets.length === 0) return;
    const own = capabilityId ? evalDatasets.find((d) => d.capability === capabilityId) : undefined;
    setEvalDatasetId((own ?? evalDatasets[0]).id);
  }, [evalDatasets, capabilityId, evalDatasetId]);

  const evalSetsQuery = useEvalSetsQuery(projectId);
  const evalSets = useMemo(
    () =>
      (evalSetsQuery.data?.results ?? []).filter(
        (s) =>
          s.project === projectId &&
          (!capabilityId || !s.capability || s.capability === capabilityId)
      ),
    [evalSetsQuery.data, capabilityId, projectId]
  );
  const evalSet = evalSets.find((s) => s.id === evalSetId);
  useEffect(() => {
    if (evalSetId || evalSets.length === 0) return;
    setEvalSetId((evalSets.find((s) => s.isActive) ?? evalSets[0]).id);
  }, [evalSets, evalSetId]);
  const evalPreload = useCapabilityEvalPreload(capabilityId, {
    enabled: evaluationEnabled && !!capabilityId,
    projectId,
  });

  const validateMutation = useValidateDatasetMutation();
  const validateRef = useRef(validateMutation);
  validateRef.current = validateMutation;

  useEffect(() => {
    if (!datasetId) {
      setValidation(null);
      setValidating(false);
      return;
    }
    let cancelled = false;
    setValidating(true);
    void validateRef.current
      .mutateAsync({
        cellId: dataset?.active ?? undefined,
        datasetId,
        splitMethod: validationSelection.splitMethod,
        validationDatasetId: null,
        validationEnabled: validationSelection.validationEnabled,
        validationSplitRatio: validationSelection.validationSplitRatio,
      })
      .then((result) => {
        if (!cancelled) setValidation(result);
      })
      .catch(() => {
        if (!cancelled) setValidation(FAILED_VALIDATION);
      })
      .finally(() => {
        if (!cancelled) setValidating(false);
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId, dataset?.active]);

  const datasetValid = validation?.valid === true;

  const dataReady = !!datasetId && datasetValid && !validating;

  // Recommendations start the moment the dataset validates, so the models
  // appear without a separate action.
  const recommendQuery = useRecommendModelsQuery(
    dataReady ? datasetId : "",
    dataReady ? capabilityId || undefined : undefined,
    dataReady && evaluationEnabled && !nativeDecision ? evalDatasetId || undefined : undefined
  );
  const rec = recommendQuery.data;

  const hasToolCalling = Boolean(rec?.dataset?.hasToolCalling);
  const baseCatalogQuery = useModelCatalogQuery(false);
  const toolCatalogQuery = useModelCatalogQuery(hasToolCalling);
  const catalog: ModelCatalog =
    (hasToolCalling ? toolCatalogQuery.data : baseCatalogQuery.data) ??
    baseCatalogQuery.data ??
    EMPTY_CATALOG;
  const monitoring = useMemo<MonitoringOptions>(
    () =>
      monitoringChoice ?? {
        ...defaultMonitoring,
        mode: catalog.backend === "together" ? "off" : "adaptive",
      },
    [monitoringChoice, catalog.backend]
  );

  // Every constraint-passing model, ranked by grade; `shown` names the opening line-up.
  const candidates = useMemo(() => rec?.candidates ?? [], [rec]);
  const excluded = useMemo(() => rec?.excluded ?? [], [rec]);
  const shownModels = useMemo(() => rec?.shown ?? [], [rec]);
  const candidateByModel = useMemo(() => {
    const map = new Map<string, FinetuningExperiment>();
    for (const c of candidates) map.set(c.model, c);
    return map;
  }, [candidates]);

  const overlapQuery = useDatasetOverlapQuery(
    dataReady ? datasetId : "",
    dataReady && evaluationEnabled && !nativeDecision ? evalDatasetId || undefined : undefined
  );
  const overlapCount = overlapQuery.data?.overlapCount ?? 0;

  const catalogModelById = useCallback(
    (modelId: string): { tier: string; model: ModelEntry } | null =>
      findCatalogModel(catalog, modelId),
    [catalog]
  );

  /** Guarded on (id, model) so a stale response never clobbers a newer pick; on
      failure the draft keeps blank batch/epochs for the backend to derive. */
  const applyModelDefaults = useCallback(
    async (draftId: string, modelId: string, trainDatasetId: string) => {
      try {
        const defaults = await apiClient.finetuningJobs.finetuningJobsModelDefaultsCreate({
          finetuningModelDefaultsRequestRequest: { baseModel: modelId, datasetId: trainDatasetId },
        });
        const found = findCatalogModel(catalog, modelId);
        setDrafts((prev) =>
          prev.map((d) => {
            if (d.id !== draftId || d.model !== modelId) return d;
            // This endpoint derives hyperparameters only and returns an ungraded row.
            const next = applyCandidateGrade(
              draftFromRecommendation(defaults, found?.model),
              candidateByModel.get(modelId)
            );
            // Respect a LoRA toggle made while the fetch was in flight, but never
            // leave Full on when the catalog forbids it.
            const useLora = !next.supportsFull || (next.supportsLora && d.useLora);
            return {
              ...next,
              hyperparams: {
                ...next.hyperparams,
                learning_rate:
                  useLora === next.useLora
                    ? next.hyperparams.learning_rate
                    : useLora
                      ? next.learningRateLora
                      : next.learningRateFull,
              },
              id: d.id,
              useLora,
            };
          })
        );
      } catch {
        // silent — blank fields read "auto" and the backend derives them
      }
    },
    [candidateByModel, catalog]
  );

  // Seed the opening line-up in the backend's order. Only the models it marks selected
  // start checked; the rest are there to be compared against.
  useEffect(() => {
    const key = `${datasetId}:${capabilityId}`;
    if (!dataReady || candidates.length === 0 || seededFor === key) return;
    setSeededFor(key);
    const byModel = new Map(candidates.map((c) => [c.model, c]));
    const seeded = shownModels
      .slice(0, MAX_MODELS)
      .map((model) => byModel.get(model))
      .filter((c) => c !== undefined)
      .map((c) => ({
        draft: draftFromRecommendation(c, findCatalogModel(catalog, c.model)?.model),
        selected: c.selected,
      }));
    setDrafts(seeded.map((entry) => entry.draft));
    setDeselected(new Set(seeded.filter((e) => !e.selected).map((e) => e.draft.id)));
  }, [capabilityId, candidates, catalog, dataReady, datasetId, seededFor, shownModels]);

  // Tracked as an exclusion set so a newly added model is selected by default.
  const selectedDrafts = useMemo(
    () => drafts.filter((d) => !deselected.has(d.id)),
    [drafts, deselected]
  );

  const contextModels = [
    ...(evaluationPlan.evalModelBefore || evaluationPlan.evalModelAfter
      ? selectedDrafts.map((draft) => draft.model)
      : []),
    ...(evaluationPlan.evalIncumbentBefore ? benchmarkModels : []),
  ];
  const contextOutput = candidateByModel.get(selectedDrafts[0]?.model)?.servingContext
    ?.outputTokens;
  const contextVariants = contextModels.map((model) => ({
    label:
      candidateByModel.get(model)?.displayName ??
      benchmarkOptions.find((option) => option.value === model)?.label ??
      model,
    modelName: model,
    outputTokens: candidateByModel.get(model)?.servingContext?.outputTokens ?? contextOutput,
  }));
  const contextQuery = useQuery({
    enabled: Boolean(evalDatasetId && evalSetId && Object.values(evaluationPlan).some(Boolean)),
    queryFn: () =>
      apiClient.evalRuns.evalRunsContextCheckCreate({
        evaluationContextRequestRequest: {
          capability: capabilityId || null,
          dataset: evalDatasetId,
          evalSet: nativeDecision ? null : evalSetId,
          judgeModel: judgeModel as EvaluationContextRequestRequest["judgeModel"],
          project: projectId,
          variants: contextVariants,
        },
      }),
    queryKey: [
      "evaluation-context",
      projectId,
      evalDatasetId,
      evalDataset?.updatedAt,
      evalSetId,
      evalSet?.updatedAt,
      judgeModel,
      capabilityId,
      contextVariants,
    ],
    retry: false,
    staleTime: 60_000,
  });

  const toggleSelected = useCallback((draftId: string) => {
    setDeselected((prev) => {
      const next = new Set(prev);
      if (next.has(draftId)) next.delete(draftId);
      else next.add(draftId);
      return next;
    });
  }, []);

  const estimateQueries = useQueries({
    queries: drafts.map((draft) => ({
      enabled: !!datasetId && !!draft.model && (draft.hyperparams.n_epochs ?? 0) >= 1,
      queryFn: () =>
        apiClient.finetuningJobs.finetuningJobsEstimateCreate({
          finetuningEstimateRequestRequest: {
            baseModel: draft.model,
            cell: dataset?.active ?? undefined,
            datasetId,
            nEpochs: draft.hyperparams.n_epochs ?? 1,
            useLora: draft.useLora,
            ...validationSelection,
            hyperparameters: {
              ...buildHyperparameters(draft),
              monitoring: {
                ...monitoring,
                ...(nativeDecision ? { initial: preTrainingBaseline } : {}),
              },
              ...(nativeDecision
                ? { objective: decisionObjective, pre_training_baseline: preTrainingBaseline }
                : {}),
            },
          },
        }),
      queryKey: [
        "finetuning-estimate",
        datasetId,
        draft.model,
        draft.hyperparams.n_epochs,
        draft.useLora,
        draft.hyperparams,
        nativeDecision,
        preTrainingBaseline,
        monitoring,
        dataset?.active,
        validationSelection,
      ] as const,
      staleTime: 30_000,
    })),
  });

  const estimates = useMemo(() => {
    const map = new Map<string, FinetuningEstimateResponse | undefined>();
    drafts.forEach((draft, i) => {
      map.set(draft.id, estimateQueries[i]?.data as FinetuningEstimateResponse | undefined);
    });
    return map;
  }, [drafts, estimateQueries]);

  const totals = useMemo(() => {
    const rows = selectedDrafts.map((d) => estimates.get(d.id));
    const priced = rows.filter((e) => e?.costEstimate != null);
    const timings = rows.map((row) => {
      const range = row?.forecast?.training_seconds;
      if (
        Array.isArray(range) &&
        range.length === 2 &&
        range.every((n) => typeof n === "number" && Number.isFinite(n) && n >= 0) &&
        range[1] >= range[0]
      ) {
        return { human: row?.timeEstimate.human, lower: range[0], range: true, upper: range[1] };
      }
      const seconds = row?.timeEstimate.seconds;
      return typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0
        ? { human: row?.timeEstimate.human, lower: seconds, range: false, upper: seconds }
        : null;
    });
    let longest: string | null = null;
    if (timings.length && timings.every((time) => time !== null)) {
      if (timings.some((time) => time.range)) {
        const lower = Math.floor(Math.max(...timings.map((time) => time.lower)) / 60);
        const upper = Math.ceil(Math.max(...timings.map((time) => time.upper)) / 60);
        longest = `${lower === upper ? lower : `${lower}–${upper}`} min`;
      } else {
        longest = timings.sort((a, b) => b.upper - a.upper)[0].human ?? null;
      }
    }
    return {
      longest,
      pricedCount: priced.length,
      usd: priced.reduce((sum, e) => sum + (e?.costEstimate?.usd ?? 0), 0),
    };
  }, [selectedDrafts, estimates]);

  const capabilityLabel = (capability?.name || "").trim();
  const datasetLabel = (dataset?.name || "").trim();
  const computedName = defaultFinetuneName({
    capabilityName: capabilityLabel,
    datasetName: datasetLabel,
  });
  // The generated name follows capability and dataset until the user types over it.
  useEffect(() => {
    if (!runNameDirty) setRunName(computedName);
  }, [computedName, runNameDirty]);

  const setCapabilityId = useCallback((id: string) => {
    setCapabilityIdState(id);
    setBenchmarkSelection(null);
    // Eval sets and recommendations are both capability-scoped.
    setEvalSetId("");
    setDrafts([]);
    setSeededFor("");
    setLaunchError(null);
  }, []);

  const setDatasetId = useCallback((id: string) => {
    setDatasetIdState(id);
    setDrafts([]);
    setSeededFor("");
    setLaunchError(null);
  }, []);

  const addModel = useCallback(
    (modelId: string) => {
      if (drafts.length >= MAX_MODELS || drafts.some((d) => d.model === modelId)) return;
      const found = findCatalogModel(catalog, modelId);
      if (!found) return;
      const candidate = candidateByModel.get(modelId);
      const draft = candidate
        ? draftFromRecommendation(candidate, found.model)
        : draftFromCatalogModel(found.tier, found.model);
      setDrafts((prev) => [...prev, draft]);
      if (!candidate) void applyModelDefaults(draft.id, modelId, datasetId);
    },
    [applyModelDefaults, candidateByModel, catalog, datasetId, drafts]
  );

  const removeModel = useCallback((draftId: string) => {
    setDrafts((prev) => prev.filter((d) => d.id !== draftId));
  }, []);

  const updateDraft = useCallback((draftId: string, next: ModelDraft) => {
    setDrafts((prev) => prev.map((d) => (d.id === draftId ? next : d)));
  }, []);

  const replaceDraftModel = useCallback(
    (draftId: string, next: ModelDraft, previous: ModelDraft) => {
      const graded = applyCandidateGrade(next, candidateByModel.get(next.model));
      setDrafts((prev) => prev.map((d) => (d.id === draftId ? graded : d)));
      if (isUntuned(previous)) void applyModelDefaults(draftId, next.model, datasetId);
    },
    [applyModelDefaults, candidateByModel, datasetId]
  );

  /** Everything the page still needs, in the order it reads top to bottom.
   *  Surfaced on the Start button, not printed beside it. */
  const launchBlocker = ((): string | null => {
    if (!datasetId) return "Select a training dataset";
    if (validating) return "Validating the dataset";
    if (!datasetValid) return "This dataset can't be trained on yet";
    if (evaluationEnabled && !nativeDecision && !evalDataset) return "Select an eval dataset";
    if (evaluationEnabled && !nativeDecision && !evalSet) return "Select an eval set";
    if (nativeDecision && catalog.backend !== "modal") return "Native decisions require Modal LoRA";
    if (nativeDecision && selectedDrafts.some((draft) => !draft.useLora))
      return "Native decisions require LoRA";
    if (
      evaluationEnabled &&
      benchmarkingEnabled &&
      benchmarkModels.some(
        (model) =>
          !benchmarkOptions.some((option) => option.value === model) &&
          !selectedDrafts.some((draft) => draft.model === model)
      )
    )
      return "Select an available benchmark model";
    if (recommendQuery.isLoading) return "Checking model compatibility";
    if (drafts.length === 0) return "Add a model";
    if (selectedDrafts.length === 0) return "Select an experiment";
    const incompatible = excluded.find((entry) =>
      selectedDrafts.some((draft) => draft.model === entry.model)
    );
    if (incompatible) return incompatible.reason;
    if (!runName.trim()) return "Name the run";
    if (!hasCredits) return "Out of credits";
    return null;
  })();
  const canLaunch = !launchBlocker;

  const createMutation = useCreateFinetuningJobsMutation(projectId);

  const launch = useCallback(async () => {
    if (!canLaunch) return;
    setLaunching(true);
    setLaunchError(null);
    try {
      const payloads: FinetuningJobRequest[] = selectedDrafts.map((draft) => {
        const fixed = applyCatalogTrainingFlags(draft, catalogModelById(draft.model)?.model);
        return {
          baseModel: fixed.model,
          ...(evaluationPlan.evalIncumbentBefore && benchmarkModel
            ? { baselineModel: benchmarkModel }
            : {}),
          benchmarkModels: evaluationPlan.evalIncumbentBefore ? benchmarkModels : [],
          capability: capabilityId || null,
          cell: dataset?.active ?? undefined,
          dataset: datasetId,
          evalCell:
            evaluationEnabled && !nativeDecision ? (evalDataset?.active ?? undefined) : undefined,
          evalDataset: evaluationEnabled && !nativeDecision ? evalDatasetId : null,
          evalJudgeModel: (evaluationEnabled && !nativeDecision
            ? judgeModel
            : "") as FinetuningJobRequest["evalJudgeModel"],
          evalSet: evaluationEnabled && !nativeDecision ? evalSetId : null,
          ...evaluationPlan,
          groupId,
          hyperparameters: {
            ...buildHyperparameters(fixed),
            monitoring: {
              ...monitoring,
              ...(nativeDecision ? { initial: preTrainingBaseline } : {}),
            },
            ...(nativeDecision
              ? { objective: decisionObjective, pre_training_baseline: preTrainingBaseline }
              : {}),
          },
          modelTier: fixed.tier as FinetuningJobRequestModelTierEnum,
          // Job names are `base · dataset · capability`: the run name is
          // model-independent, so prepend this job's own base model. Pages strip
          // the base and capability segments back off for display.
          name: [fixed.displayName, runName.trim() || computedName]
            .map((s) => s.trim())
            .filter(Boolean)
            .join(" · "),
          project: projectId,
          requestKey: `${groupId}:${draft.id}`,
          ...validationSelection,
          validationDataset: null,
        };
      });
      await createMutation.mutateAsync(payloads);
      onLaunched(groupId);
    } catch (err) {
      // A spent balance already raises the out-of-credits dialog.
      setLaunchError(isPaymentRequired(err) ? null : errorMessage(err, "Couldn't start training"));
    } finally {
      setLaunching(false);
    }
  }, [
    canLaunch,
    monitoring,
    preTrainingBaseline,
    decisionObjective,
    evaluationEnabled,
    dataset?.active,
    evalDataset?.active,
    nativeDecision,
    benchmarkModel,
    benchmarkModels,
    capabilityId,
    catalogModelById,
    computedName,
    createMutation,
    datasetId,
    evalDatasetId,
    evalSetId,
    evaluationPlan,
    judgeModel,
    groupId,
    onLaunched,
    projectId,
    runName,
    selectedDrafts,
  ]);

  const dirty =
    monitoringChoice !== null ||
    evaluationEnabled !== Boolean(initialEvalDatasetId) ||
    Boolean(judgeModel) ||
    benchmarkSelection !== null ||
    benchmarkingEnabled ||
    datasetId !== (initialDatasetId ?? "") ||
    capabilityId !== (initialCapabilityId ?? "") ||
    runNameDirty ||
    drafts.length > 0;

  return {
    addModel,
    benchmarkingEnabled,
    benchmarkModel,
    benchmarkModels,
    benchmarkOptions,
    benchmarksQuery,
    candidateByModel,
    candidates,
    canLaunch,
    capabilities,
    capabilitiesQuery,
    capability,
    capabilityId,
    catalog,
    catalogModelById,
    contextQuery,
    dataReady,
    dataset,
    datasetId,
    datasets,
    datasetsQuery,
    decisionObjective,
    dirty,
    drafts,
    estimates,
    evalDataset,
    evalDatasetId,
    evalDatasets,
    evalDatasetsQuery,
    evalPreload,
    evalSet,
    evalSetId,
    evalSets,
    evalSetsQuery,
    evaluationEnabled,
    evaluationPlan,
    excluded,
    hasIncumbent,
    judgeModel,
    launch,
    launchBlocker,
    launchError,
    launching,
    monitoring,
    nativeDecision,
    overlapCount,
    preTrainingBaseline,
    rec,
    recommendQuery,
    removeModel,
    replaceDraftModel,
    runName,
    selectedBenchmark,
    selectedDrafts,
    setBenchmarkingEnabled,
    setCapabilityId,
    setDatasetId,
    setEvalDatasetId,
    setEvalSetId,
    setEvaluationEnabled,
    setJudgeModel,
    setMonitoring,
    setPreTrainingBaseline,
    setRunName: (value: string) => {
      setRunNameDirty(true);
      setRunName(value);
    },
    toggleBenchmarkModel,
    toggleSelected,
    totals,
    updateDraft,
    validating,
    validation,
  };
}

export type TrainWizard = ReturnType<typeof useTrainWizard>;
