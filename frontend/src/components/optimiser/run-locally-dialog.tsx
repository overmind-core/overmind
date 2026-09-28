import { useEffect, useMemo, useState } from "react";

import { ModelOptionLabel } from "@/components/model-option-label";
import type { ProviderId } from "@/components/model-provider";
import { getModelProviderInfo, getProviderInfoById } from "@/components/model-provider";
import { getProviderIcon, ProviderLogo } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { QueryError } from "@/components/ui/query-error";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useModelCatalogQuery,
  useProjectCapabilitiesQuery,
  useProjectDatasetsForEvalQuery,
} from "@/hooks/use-evaluations";
import { useDeployedModelsQuery } from "@/hooks/use-inference";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import { type CatalogModel, DeployedModelsListStatusEnum } from "@/openapi";

export const MAX_COMPARISON_MODELS = 5;

type ComparisonModelLane = "batch" | "standard";

/** OpenRouter batch slugs carry a `:batch` suffix (e.g. `openai/gpt-5-mini:batch`). */
export function isBatchModel(modelId: string): boolean {
  return modelId.includes(":batch");
}

function comparisonModelLane(selected: string[]): ComparisonModelLane | null {
  if (selected.length === 0) return null;
  return selected.some(isBatchModel) ? "batch" : "standard";
}

/** Chips only — every other provider stays reachable through the browse list. */
const POPULAR_PROVIDERS: ProviderId[] = [
  "openai",
  "anthropic",
  "google",
  "xai",
  "deepseek",
  "qwen",
  "kimi",
  "zhipu",
  "minimax",
  "bytedance",
];

export function toggleComparisonModel(selected: string[], modelId: string): string[] {
  if (selected.includes(modelId)) return selected.filter((id) => id !== modelId);
  if (selected.length >= MAX_COMPARISON_MODELS) return selected;
  const lane = comparisonModelLane(selected);
  if (lane && comparisonModelLane([modelId]) !== lane) return selected;
  return [...selected, modelId];
}

export function isComparisonOptionDisabled(selected: string[], modelId: string): boolean {
  if (selected.includes(modelId)) return false;
  if (selected.length >= MAX_COMPARISON_MODELS) return true;
  const lane = comparisonModelLane(selected);
  return lane !== null && comparisonModelLane([modelId]) !== lane;
}

/** An empty selection keeps every provider. */
function filterModelsByProvider(models: CatalogModel[], providers: ProviderId[]): CatalogModel[] {
  if (providers.length === 0) return models;
  const wanted = new Set(providers);
  return models.filter((model) => wanted.has(getModelProviderInfo(model.id).id));
}

export const DATASET_REF_PLACEHOLDER = "<dataset-id>";

export function buildOptimisePrompt(capabilitySlug: string, datasetRef: string): string {
  return `/overmind optimise
capability: ${capabilitySlug}
dataset: ${datasetRef}`;
}

export function buildBacktestPrompt(
  capabilitySlug: string,
  datasetRef: string,
  modelIds: string[]
): string {
  const models = (modelIds.length > 0 ? modelIds : ["<model>"]).join(", ");
  return `/overmind backtest
capability: ${capabilitySlug}
dataset: ${datasetRef}
models: ${models}`;
}

interface RunLocallyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  capabilityId?: string;
  datasetId?: string;
}

export function RunLocallyDialog({
  open,
  onOpenChange,
  projectId,
  capabilityId,
  datasetId,
}: RunLocallyDialogProps) {
  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent size="lg">
        {open ? (
          <RunLocallyBody
            capabilityId={capabilityId}
            datasetId={datasetId}
            onOpenChange={onOpenChange}
            projectId={projectId}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function RunLocallyBody({
  onOpenChange,
  projectId,
  capabilityId,
  datasetId,
}: Omit<RunLocallyDialogProps, "open">) {
  const [chosenModelIds, setChosenModelIds] = useState<string[] | null>(null);
  const [selectedCapabilityId, setSelectedCapabilityId] = useState(capabilityId ?? "");
  const [selectedDatasetId, setSelectedDatasetId] = useState(datasetId ?? "");
  const [selectedTab, setSelectedTab] = useState("optimize");
  const defaultModelIds = useModelCatalogQuery().data?.defaults.backtestModels ?? [];
  const modelIds = chosenModelIds ?? defaultModelIds;

  const capabilitiesQuery = useProjectCapabilitiesQuery(projectId);
  const capabilities = capabilitiesQuery.data?.results ?? [];
  useEffect(() => {
    if (selectedCapabilityId) return;
    if (capabilityId && capabilities.some((row) => row.id === capabilityId)) {
      setSelectedCapabilityId(capabilityId);
      return;
    }
    if (capabilities.length === 1) setSelectedCapabilityId(capabilities[0].id);
  }, [capabilities, capabilityId, selectedCapabilityId]);
  const capability = capabilities.find((row) => row.id === selectedCapabilityId);
  const capabilitySlug = capability?.slug ?? "<slug>";
  const incumbentModel = capability?.activeModel ?? null;

  const datasetsQuery = useProjectDatasetsForEvalQuery(projectId);
  const datasets = datasetsQuery.data?.results ?? [];
  useEffect(() => {
    if (selectedDatasetId) return;
    if (datasetId && datasets.some((row) => row.id === datasetId)) {
      setSelectedDatasetId(datasetId);
      return;
    }
    if (datasets.length === 1) setSelectedDatasetId(datasets[0].id);
  }, [datasetId, datasets, selectedDatasetId]);

  const datasetRef = selectedDatasetId.trim() || DATASET_REF_PLACEHOLDER;
  const optimisePrompt = buildOptimisePrompt(capabilitySlug, datasetRef);
  const backtestPrompt = buildBacktestPrompt(capabilitySlug, datasetRef, modelIds);

  return (
    <>
      <DialogHeader>
        <DialogTitle>New optimisation run</DialogTitle>
        <DialogDescription>
          Improve a capability's prompts and code, or compare the models running it.
        </DialogDescription>
      </DialogHeader>

      <DialogBody className="min-w-0 space-y-5">
        <Tabs onValueChange={setSelectedTab} value={selectedTab}>
          <TabsList
            aria-label="Run type"
            className="grid h-auto grid-cols-1 items-stretch gap-3 bg-transparent p-0 sm:grid-cols-2"
          >
            <TabsTrigger
              aria-describedby="optimise-mode-description"
              aria-labelledby="optimise-mode-label"
              className="items-start flex-col gap-1.5 whitespace-normal border border-border bg-card p-3 text-left data-[state=active]:border-primary data-[state=active]:bg-accent data-[state=active]:text-foreground data-[state=active]:hover:text-foreground"
              value="optimize"
            >
              <span id="optimise-mode-label">Improve prompts &amp; code</span>
              <span className="text-xs text-muted-foreground" id="optimise-mode-description">
                Test changes to prompts, tool definitions and application logic.
              </span>
            </TabsTrigger>
            <TabsTrigger
              aria-describedby="compare-mode-description"
              aria-labelledby="compare-mode-label"
              className="items-start flex-col gap-1.5 whitespace-normal border border-border bg-card p-3 text-left data-[state=active]:border-primary data-[state=active]:bg-accent data-[state=active]:text-foreground data-[state=active]:hover:text-foreground"
              value="model_comparison"
            >
              <span id="compare-mode-label">Compare models</span>
              <span className="text-xs text-muted-foreground" id="compare-mode-description">
                Keep prompts and logic fixed; evaluate different models on the same tasks.
              </span>
            </TabsTrigger>
          </TabsList>

          <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="min-w-0 space-y-1.5">
              <Label className="text-xs text-muted-foreground">Capability</Label>
              <EntitySelect
                ariaLabel="Capability"
                empty="No capabilities."
                error={capabilitiesQuery.error}
                errorFallback="Couldn't load capabilities."
                isLoading={capabilitiesQuery.isLoading}
                loadingLabel="Loading capabilities…"
                onRetry={capabilitiesQuery.refetch}
                onValueChange={setSelectedCapabilityId}
                options={capabilities.map((row) => ({ id: row.id, name: row.name }))}
                placeholder="Select a capability"
                value={selectedCapabilityId}
              />
            </div>

            <div className="min-w-0 space-y-1.5">
              <Label className="text-xs text-muted-foreground">Eval dataset</Label>
              <EntitySelect
                ariaLabel="Eval dataset"
                empty="No eval datasets."
                error={datasetsQuery.error}
                errorFallback="Couldn't load datasets."
                isLoading={datasetsQuery.isLoading}
                loadingLabel="Loading datasets…"
                onRetry={datasetsQuery.refetch}
                onValueChange={setSelectedDatasetId}
                options={datasets.map((row) => ({ id: row.id, name: row.name || row.id }))}
                placeholder="Select a dataset"
                value={selectedDatasetId}
              />
            </div>
          </div>

          <TabsContent className="mt-4 space-y-4" value="optimize">
            <p className={cn(PROSE, "text-sm text-muted-foreground")}>
              Your coding agent proposes code changes and tests each against the current version
              using this dataset. Overmind scores the results and records the winning diff for
              review.
            </p>
            <CommandBlock commands={optimisePrompt} />
          </TabsContent>
          <TabsContent className="mt-4 space-y-4" value="model_comparison">
            <p className={cn(PROSE, "text-sm text-muted-foreground")}>
              Run up to {MAX_COMPARISON_MODELS} models on the same examples. Compare evaluation
              scores with the current model, without fine-tuning or changing prompts.
            </p>
            <BacktestModelPicker
              incumbentModel={incumbentModel}
              modelIds={modelIds}
              onModelIdsChange={setChosenModelIds}
              projectId={projectId}
            />
            <CommandBlock commands={backtestPrompt} />
          </TabsContent>
        </Tabs>
      </DialogBody>

      <DialogFooter>
        <Button onClick={() => onOpenChange(false)} type="button" variant="secondary">
          Close
        </Button>
      </DialogFooter>
    </>
  );
}

function BacktestModelPicker({
  modelIds,
  onModelIdsChange,
  incumbentModel,
  projectId,
}: {
  modelIds: string[];
  onModelIdsChange: (modelIds: string[]) => void;
  incumbentModel: string | null;
  projectId: string;
}) {
  const catalogQuery = useModelCatalogQuery();
  const deployedQuery = useDeployedModelsQuery({
    projectId,
    status: DeployedModelsListStatusEnum.ready,
  });
  const [search, setSearch] = useState("");
  const [providerFilter, setProviderFilter] = useState<ProviderId[]>([]);

  const allModels = catalogQuery.data?.models ?? [];
  const providerCounts = useMemo(() => {
    const counts = new Map<ProviderId, number>();
    for (const model of allModels) {
      const provider = getModelProviderInfo(model.id).id;
      counts.set(provider, (counts.get(provider) ?? 0) + 1);
    }
    return counts;
  }, [allModels]);
  const providerChips = POPULAR_PROVIDERS.filter(
    (provider) => (providerCounts.get(provider) ?? 0) > 0
  );
  const deployedPicks = useMemo(
    () =>
      (deployedQuery.data?.results ?? []).map((deployed) => ({
        baseModelId: deployed.baseModelId,
        id: deployed.modelId,
        label: deployed.capabilityName
          ? `${deployed.capabilityName} · ${deployed.baseModelId.split("/").pop() ?? deployed.modelId}`
          : deployed.modelId,
      })),
    [deployedQuery.data?.results]
  );
  const selected = new Set(modelIds);
  const models = useMemo(() => {
    const q = search.trim().toLowerCase();
    return filterModelsByProvider(allModels, providerFilter)
      .filter(
        (model) =>
          !q ||
          model.id.toLowerCase().includes(q) ||
          model.name.toLowerCase().includes(q) ||
          model.provider.toLowerCase().includes(q)
      )
      .sort((a, b) => a.id.localeCompare(b.id));
  }, [allModels, providerFilter, search]);

  if (catalogQuery.isLoading) {
    return (
      <div className="flex h-16 items-center justify-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        Loading OpenRouter models…
      </div>
    );
  }

  if (catalogQuery.error) {
    return (
      <QueryError
        error={catalogQuery.error}
        fallback="Couldn't load the OpenRouter model catalogue."
        onRetry={catalogQuery.refetch}
      />
    );
  }

  return (
    <div className="space-y-3">
      {providerChips.length > 0 && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            <p className="font-medium">Popular providers</p>
            <span className="mx-1 text-muted-foreground/50">|</span>
            <p className={cn(PROSE)}>Pick up to {MAX_COMPARISON_MODELS} models</p>
          </div>
          <div aria-label="Filter by provider" className="flex flex-wrap gap-1.5">
            {providerChips.map((providerId) => {
              const active = providerFilter.includes(providerId);
              const info = getProviderInfoById(providerId);
              return (
                <button
                  aria-label={`${active ? "Remove" : "Add"} ${info.providerLabel} filter`}
                  aria-pressed={active}
                  className={cn(
                    "inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium transition-colors",
                    active
                      ? "border-primary/40 bg-primary/10 text-foreground"
                      : "border-border/70 bg-wash-raised text-muted-foreground hover:border-border hover:text-foreground"
                  )}
                  key={providerId}
                  onClick={() =>
                    setProviderFilter((current) =>
                      active ? current.filter((id) => id !== providerId) : [...current, providerId]
                    )
                  }
                  type="button"
                >
                  <ProviderLogo
                    Icon={getProviderIcon(providerId)}
                    providerLabel={info.providerLabel}
                    providerSlug={info.providerSlug}
                  />
                  <span className="truncate">{info.providerLabel}</span>
                  <span className="shrink-0 font-mono text-xs tabular-nums opacity-70">
                    {providerCounts.get(providerId)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {deployedPicks.length > 0 && (
        <div className="space-y-1.5">
          <p className="px-1 text-xs font-medium text-muted-foreground">Your fine-tuned models</p>
          <div aria-label="Fine-tuned OpenRouter models" className="flex flex-wrap gap-1.5">
            {deployedPicks.map((pick) => {
              const checked = selected.has(pick.id);
              const disabled = isComparisonOptionDisabled(modelIds, pick.id);
              return (
                <button
                  aria-label={`Select ${pick.id}`}
                  aria-pressed={checked}
                  className={cn(
                    "inline-flex max-w-full items-center gap-2 rounded-sm border px-2 py-1.5 text-sm transition-colors",
                    checked
                      ? "border-success/40 bg-success/10 text-foreground"
                      : "border-border/70 bg-wash-raised text-muted-foreground hover:border-border hover:text-foreground",
                    disabled && "cursor-not-allowed opacity-50"
                  )}
                  disabled={disabled}
                  key={pick.id}
                  onClick={() => onModelIdsChange(toggleComparisonModel(modelIds, pick.id))}
                  type="button"
                >
                  <ModelOptionLabel model={pick.baseModelId} name={pick.label} />
                  {checked && <Icon.success className="size-3 shrink-0 text-success" />}
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="relative">
        <Icon.search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          aria-label="Search OpenRouter models"
          className="pl-8"
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search model name, provider, or slug…"
          value={search}
        />
      </div>

      {modelIds.length > 0 && (
        <div aria-label="Selected OpenRouter models" className="flex flex-wrap gap-1.5">
          {modelIds.map((modelId) => (
            <Badge className="max-w-full gap-2" key={modelId} variant="outline">
              <ModelOptionLabel model={modelId} />
              {isBatchModel(modelId) && (
                <span className="font-normal text-muted-foreground">· batch</span>
              )}
              {modelId === incumbentModel && (
                <span className="font-normal text-muted-foreground">· current model</span>
              )}
              <button
                aria-label={`Remove ${modelId}`}
                className="rounded-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                onClick={() => onModelIdsChange(modelIds.filter((id) => id !== modelId))}
                type="button"
              >
                <Icon.close className="size-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}

      <div className="flex items-center justify-between gap-3 px-1 text-xs font-medium text-muted-foreground">
        <span>
          Browse all models
          {modelIds.length > 0 && (
            <span className="tabular-nums">
              {" "}
              · {modelIds.length} of {MAX_COMPARISON_MODELS} selected
            </span>
          )}
        </span>
      </div>

      <div className="max-h-60 overflow-y-auto rounded-md border border-border/70">
        {models.length === 0 ? (
          <p className="px-3 py-6 text-center text-sm text-muted-foreground">No matching models.</p>
        ) : (
          models.map((model) => {
            const checked = selected.has(model.id);
            const disabled = isComparisonOptionDisabled(modelIds, model.id);
            const batch = isBatchModel(model.id);
            return (
              <label
                className={cn(
                  "flex cursor-pointer items-start gap-2.5 border-b border-border/60 px-3 py-2 last:border-b-0 hover:bg-wash-raised",
                  disabled && "cursor-not-allowed opacity-50"
                )}
                key={model.id}
              >
                <Checkbox
                  aria-label={`Select ${model.id}`}
                  checked={checked}
                  disabled={disabled}
                  onCheckedChange={() =>
                    onModelIdsChange(toggleComparisonModel(modelIds, model.id))
                  }
                />
                <span className="min-w-0 flex-1">
                  <ModelOptionLabel className="text-sm" model={model.id} name={model.name}>
                    {batch && (
                      <Badge className="shrink-0" size="chip" variant="neutral">
                        Batch
                      </Badge>
                    )}
                  </ModelOptionLabel>
                  <span className="block truncate pl-5.5 font-mono text-xs text-muted-foreground">
                    {model.id}
                  </span>
                </span>
              </label>
            );
          })
        )}
      </div>

      <p className={cn(PROSE, "text-xs text-muted-foreground")}>
        Select 1–{MAX_COMPARISON_MODELS} models. Batch and standard models can't be mixed. The
        prompt below uses this list.
      </p>
    </div>
  );
}

function EntitySelect({
  ariaLabel,
  empty,
  error,
  errorFallback,
  isLoading,
  loadingLabel,
  onRetry,
  onValueChange,
  options,
  placeholder,
  value,
}: {
  ariaLabel: string;
  empty: string;
  error: unknown;
  errorFallback: string;
  isLoading: boolean;
  loadingLabel: string;
  onRetry: () => Promise<unknown> | unknown;
  onValueChange: (value: string) => void;
  options: { id: string; name: string }[];
  placeholder: string;
  value: string;
}) {
  if (isLoading) {
    return (
      <div className="flex h-8 items-center gap-2 text-sm text-muted-foreground">
        <Spinner />
        {loadingLabel}
      </div>
    );
  }
  if (error) {
    return <QueryError error={error} fallback={errorFallback} onRetry={onRetry} />;
  }
  if (options.length === 0) {
    return <p className={cn(PROSE, "text-sm text-muted-foreground")}>{empty}</p>;
  }
  return (
    <Select onValueChange={onValueChange} value={value || undefined}>
      <SelectTrigger aria-label={ariaLabel} className="w-full">
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {options.map((row) => (
          <SelectItem key={row.id} value={row.id}>
            {row.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function CommandBlock({ commands }: { commands: string }) {
  const { copied, copy } = useCopy(commands);

  return (
    <div className="space-y-2">
      <p className={cn(PROSE, "text-xs text-muted-foreground")}>
        Paste this prompt into your coding agent in the capability's repository. Execution runs
        locally; scores and progress appear in Overmind. Copying does not start a run.
      </p>
      <div className="overflow-hidden rounded-md border border-border/70 bg-wash-subtle">
        <div className="flex items-center justify-between gap-3 border-b border-border/70 bg-wash-raised px-3 py-1.5">
          <span className="text-xs text-muted-foreground">Coding agent prompt</span>
          <Button onClick={copy} size="xs" type="button" variant="secondary">
            {copied ? <Icon.success /> : <Icon.copy />}
            {copied ? "Copied" : "Copy prompt"}
          </Button>
        </div>
        <pre className="overflow-x-auto whitespace-pre p-3 font-mono text-xs leading-relaxed text-foreground">
          {commands}
        </pre>
      </div>
    </div>
  );
}
