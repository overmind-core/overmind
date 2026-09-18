import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useQueries } from "@tanstack/react-query";

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
  useValidateDatasetMutation,
} from "@/hooks/use-finetuning";
import { useCredits } from "@/hooks/use-subscription";
import { isPaymentRequired } from "@/lib/credits";
import { errorMessage } from "@/lib/notify";
import type {
  Dataset,
  DatasetValidationResponse,
  FinetuningEstimateResponse,
  FinetuningExperiment,
  FinetuningJobRequest,
  FinetuningJobRequestModelTierEnum,
  SplitMethodEnum,
} from "@/openapi";
import { BackendEnum } from "@/openapi";

export interface Holdout {
  enabled: boolean;
  ratio: number;
  method: SplitMethodEnum;
  datasetId: string;
}

const EMPTY_CATALOG: ModelCatalog = {
  backend: BackendEnum.baseten,
  hasToolCalling: false,
  maxContext: null,
  models: {},
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

/** A dataset belongs to one capability or to none; another capability's rows are off
 *  distribution for this run, so they never reach a picker. */
function forCapability(datasets: Dataset[], capabilityId: string): Dataset[] {
  if (!capabilityId) return datasets;
  return datasets.filter((d) => !d.capability || d.capability === capabilityId);
}

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
  const [holdout, setHoldoutState] = useState<Holdout>({
    datasetId: "",
    enabled: true,
    method: "random",
    ratio: 0.2,
  });
  const [validation, setValidation] = useState<DatasetValidationResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [evalDatasetId, setEvalDatasetId] = useState(initialEvalDatasetId ?? "");
  const [evalSetId, setEvalSetId] = useState("");
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

  const datasetsQuery = useProjectDatasetsQuery(projectId);
  const allDatasets = useMemo(() => datasetsQuery.data?.results ?? [], [datasetsQuery.data]);
  const datasets = useMemo(
    () => forCapability(allDatasets, capabilityId),
    [allDatasets, capabilityId]
  );
  const dataset = allDatasets.find((d) => d.id === datasetId);

  const evalDatasetsQuery = useProjectDatasetsForEvalQuery(projectId);
  const allEvalDatasets = useMemo(
    () => evalDatasetsQuery.data?.results ?? [],
    [evalDatasetsQuery.data]
  );
  const evalDatasets = useMemo(
    () => forCapability(allEvalDatasets, capabilityId),
    [allEvalDatasets, capabilityId]
  );
  const evalDataset = allEvalDatasets.find((d) => d.id === evalDatasetId);

  // Seeded, not user work: main's wizard picked these too, and both stay visible
  // and changeable in the form.
  useEffect(() => {
    // The capability arrives after the datasets; seeding before it would keep
    // whichever eval dataset is newest, whatever it belongs to.
    if (evalDatasetId || !capabilityId) return;
    const own = evalDatasets.find((d) => d.capability === capabilityId);
    if (own) setEvalDatasetId(own.id);
  }, [evalDatasets, capabilityId, evalDatasetId]);

  const evalSetsQuery = useEvalSetsQuery(projectId);
  const evalSets = useMemo(
    () => (evalSetsQuery.data?.results ?? []).filter((s) => s.capability === capabilityId),
    [evalSetsQuery.data, capabilityId]
  );
  const evalSet = evalSets.find((s) => s.id === evalSetId);
  useEffect(() => {
    if (!capabilityId || evalSetId || evalSets.length === 0) return;
    setEvalSetId((evalSets.find((s) => s.isActive) ?? evalSets[0]).id);
  }, [evalSets, capabilityId, evalSetId]);
  const evalPreload = useCapabilityEvalPreload(capabilityId, {
    enabled: !!capabilityId,
    projectId,
  });

  const validateMutation = useValidateDatasetMutation();
  const validateRef = useRef(validateMutation);
  validateRef.current = validateMutation;

  // One effect owns validation so a holdout edit re-derives the split counts the
  // same way a dataset change does. `holdout` is a new object only when the user
  // edits it, so it is a safe dependency.
  useEffect(() => {
    if (!datasetId) {
      setValidation(null);
      return;
    }
    let cancelled = false;
    setValidating(true);
    void validateRef.current
      .mutateAsync({
        datasetId,
        splitMethod: holdout.method,
        validationDatasetId: holdout.datasetId || null,
        validationEnabled: holdout.enabled,
        validationSplitRatio: holdout.ratio,
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
  }, [datasetId, holdout]);

  // A deep link can carry a dataset without its capability (the workshop rail does),
  // and the capability grounds the recommendation.
  const seededCapability = useRef(false);
  useEffect(() => {
    if (seededCapability.current || capabilityId || !datasetId) return;
    const picked = allDatasets.find((d) => d.id === datasetId);
    if (!picked?.capability) return;
    seededCapability.current = true;
    setCapabilityIdState(picked.capability);
  }, [capabilityId, datasetId, allDatasets]);

  const datasetValid = validation?.valid === true;

  const dataReady = !!capabilityId && !!datasetId && datasetValid && !validating;
  const evaluationReady = dataReady && !!evalDatasetId && !!evalSetId;

  // Recommendations start the moment the dataset validates, so the models
  // appear without a separate action.
  const recommendQuery = useRecommendModelsQuery(
    dataReady ? datasetId : "",
    dataReady ? capabilityId || undefined : undefined
  );
  const rec = recommendQuery.data;

  const hasToolCalling = Boolean(rec?.dataset?.hasToolCalling);
  const baseCatalogQuery = useModelCatalogQuery(false);
  const toolCatalogQuery = useModelCatalogQuery(hasToolCalling);
  const catalog: ModelCatalog =
    (hasToolCalling ? toolCatalogQuery.data : baseCatalogQuery.data) ??
    baseCatalogQuery.data ??
    EMPTY_CATALOG;

  // Every constraint-passing model, ranked by grade; `shown` names the opening line-up.
  const candidates = useMemo(() => rec?.candidates ?? [], [rec]);
  const excluded = useMemo(() => rec?.excluded ?? [], [rec]);
  const benchmarkSnapshot = rec?.benchmarkSnapshot;
  const shownModels = useMemo(() => rec?.shown ?? [], [rec]);
  const candidateByModel = useMemo(() => {
    const map = new Map<string, FinetuningExperiment>();
    for (const c of candidates) map.set(c.model, c);
    return map;
  }, [candidates]);

  const overlapQuery = useDatasetOverlapQuery(
    dataReady ? datasetId : "",
    dataReady ? evalDatasetId || undefined : undefined
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
            datasetId,
            nEpochs: draft.hyperparams.n_epochs ?? 1,
            useLora: draft.useLora,
          },
        }),
      queryKey: [
        "finetuning-estimate",
        datasetId,
        draft.model,
        draft.hyperparams.n_epochs,
        draft.useLora,
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
    return {
      longest:
        rows
          .map((e) => e?.timeEstimate)
          .filter(Boolean)
          .sort((a, b) => (b?.seconds ?? 0) - (a?.seconds ?? 0))[0]?.human ?? null,
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

  const setCapabilityId = useCallback(
    (id: string) => {
      setCapabilityIdState(id);
      // Eval sets and recommendations are both capability-scoped.
      setEvalSetId("");
      setDrafts([]);
      setSeededFor("");
      setLaunchError(null);
      // Drop picks that belong to the capability being switched away from.
      const stale = (list: Dataset[], picked: string) =>
        !!picked && !forCapability(list, id).some((d) => d.id === picked);
      if (stale(allDatasets, datasetId)) setDatasetIdState("");
      if (stale(allDatasets, holdout.datasetId)) setHoldoutState((h) => ({ ...h, datasetId: "" }));
      if (stale(allEvalDatasets, evalDatasetId)) setEvalDatasetId("");
    },
    [allDatasets, allEvalDatasets, datasetId, evalDatasetId, holdout.datasetId]
  );

  const setDatasetId = useCallback(
    (id: string) => {
      setDatasetIdState(id);
      setDrafts([]);
      setSeededFor("");
      setLaunchError(null);
      const picked = allDatasets.find((d) => d.id === id);
      if (picked?.capability && !capabilityId) setCapabilityIdState(picked.capability);
    },
    [capabilityId, allDatasets]
  );

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
    if (!capabilityId) return "Select a capability";
    if (!datasetId) return "Select a training dataset";
    if (validating) return "Validating the dataset";
    if (!datasetValid) return "This dataset can't be trained on yet";
    if (!evalDatasetId) return "Select an eval dataset";
    if (!evalSetId) return "Select an eval set";
    if (recommendQuery.isLoading && drafts.length === 0) return "Loading recommendations";
    if (drafts.length === 0) return "Add a model";
    if (selectedDrafts.length === 0) return "Select an experiment";
    if (!runName.trim()) return "Name the run";
    if (!hasCredits) return "Out of credits";
    return null;
  })();
  const canLaunch = !launchBlocker;

  const createMutation = useCreateFinetuningJobsMutation(projectId);

  const launch = useCallback(async () => {
    setLaunching(true);
    setLaunchError(null);
    try {
      const payloads: FinetuningJobRequest[] = selectedDrafts.map((draft) => {
        const fixed = applyCatalogTrainingFlags(draft, catalogModelById(draft.model)?.model);
        return {
          baseModel: fixed.model,
          capability: capabilityId || null,
          dataset: datasetId,
          evalDataset: evalDatasetId || null,
          evalSet: evalSetId || null,
          groupId,
          hyperparameters: buildHyperparameters(fixed),
          modelTier: fixed.tier as FinetuningJobRequestModelTierEnum,
          // Job names are `base · dataset · capability`: the run name is
          // model-independent, so prepend this job's own base model. Pages strip
          // the base and capability segments back off for display.
          name: [fixed.displayName, runName.trim() || computedName]
            .map((s) => s.trim())
            .filter(Boolean)
            .join(" · "),
          project: projectId,
          splitMethod: holdout.method,
          validationDataset: holdout.datasetId || null,
          validationEnabled: holdout.enabled,
          validationSplitRatio: holdout.ratio,
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
    capabilityId,
    catalogModelById,
    computedName,
    createMutation,
    datasetId,
    evalDatasetId,
    evalSetId,
    groupId,
    holdout,
    onLaunched,
    projectId,
    runName,
    selectedDrafts,
  ]);

  const dirty =
    datasetId !== (initialDatasetId ?? "") ||
    capabilityId !== (initialCapabilityId ?? "") ||
    runNameDirty ||
    drafts.length > 0;

  return {
    addModel,
    benchmarkSnapshot,
    candidateByModel,
    candidates,
    canLaunch,
    capabilities,
    capabilitiesQuery,
    capability,
    capabilityId,
    catalog,
    catalogModelById,
    dataReady,
    dataset,
    datasetId,
    datasets,
    datasetsQuery,
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
    evaluationReady,
    excluded,
    launch,
    launchBlocker,
    launchError,
    launching,
    overlapCount,
    rec,
    recommendQuery,
    removeModel,
    replaceDraftModel,
    runName,
    selectedDrafts,
    setCapabilityId,
    setDatasetId,
    setEvalDatasetId,
    setEvalSetId,
    setRunName: (value: string) => {
      setRunNameDirty(true);
      setRunName(value);
    },
    toggleSelected,
    totals,
    updateDraft,
    validating,
    validation,
  };
}

export type TrainWizard = ReturnType<typeof useTrainWizard>;
