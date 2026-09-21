import { type ReactNode, useId, useState } from "react";

import {
  EvidencePanel,
  ExcludedSummary,
  MatchScore,
  UngradedNote,
} from "@/components/finetuning/train/evidence";
import { Column, FactLine, type Segment, SplitBar } from "@/components/finetuning/train/field";
import {
  clampBatchSize,
  draftWithModel,
  MAX_MODELS,
  type ModelDraft,
  TIER_META,
} from "@/components/finetuning/train/model-config";
import {
  type GradeIndex,
  ModelPickerList,
  ModelSelectOptions,
} from "@/components/finetuning/train/model-picker";
import { TuningField } from "@/components/finetuning/train/tuning";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { CreditsAmount } from "@/components/ui/credits";
import { Icon } from "@/components/ui/icons";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import type { ModelCatalog } from "@/hooks/use-finetuning";
import { count } from "@/lib/formatters";
import { humanizeKey } from "@/lib/label-case";
import { cn } from "@/lib/utils";
import type {
  FinetuningEstimateResponse,
  FinetuningRecommendationResponse,
  TaskTypeSourceEnum,
} from "@/openapi";

const CLASSIFIED_FROM: Record<TaskTypeSourceEnum, string> = {
  capability: "Classified from capability codebase context",
  heuristic: "Classified from dataset structure",
  semantic: "Classified from dataset content",
  unknown: "Capability task could not be classified from codebase context",
};

function compactTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${Math.round(tokens / 1_000).toLocaleString()}k`;
  return tokens.toLocaleString();
}

/** The bare `2026-08-11` form parses as UTC midnight, which renders a day early
 *  west of Greenwich; the time makes it local. */
function snapshotDate(generatedAt: string): string {
  const date = new Date(`${generatedAt}T00:00:00`);
  if (Number.isNaN(date.getTime())) return generatedAt;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function MetaRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="col-span-2 grid grid-cols-subgrid items-start">
      <dt className="pixel-label text-xs text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function AnalysisHeader({
  rec,
  benchmarkCount,
  composition,
  format,
}: {
  rec: FinetuningRecommendationResponse;
  /** Distinct benchmarks behind the ranked set, not the artifact's whole catalogue. */
  benchmarkCount: number;
  composition: Segment[];
  /** Row shape from dataset validation; the setup controls no longer restate it. */
  format?: string;
}) {
  const { benchmarkSnapshot, dataset } = rec;
  const blend = Object.entries(rec.skillWeights)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([skill, weight]) => `${skill} ${Math.round(weight * 100)}%`);

  return (
    <dl className="grid shrink-0 grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 border-b border-border/70 pb-3">
      <MetaRow label="Task type">
        <FactLine
          items={[
            <span
              className="text-foreground"
              key="type"
              title={CLASSIFIED_FROM[rec.taskTypeSource]}
            >
              {humanizeKey(rec.taskType || "unknown")}
            </span>,
            rec.taskTypeSource === "capability" || rec.taskTypeSource === "unknown"
              ? "Capability context"
              : "Dataset",
          ]}
        />
      </MetaRow>

      <MetaRow label="Training data">
        <FactLine
          items={[
            dataset.rows > 0 ? count(dataset.rows, "row") : null,
            format ?? null,
            dataset.totalTokens > 0 ? `~${compactTokens(dataset.totalTokens)} tokens` : null,
            dataset.maxTokenLength > 0
              ? `longest ≈${dataset.maxTokenLength.toLocaleString()} tok`
              : null,
            dataset.hasToolCalling ? "tool-calling" : null,
          ]}
        />
      </MetaRow>

      <MetaRow label="Graded on">
        <FactLine
          items={[
            ...(blend.length > 0 ? blend : ["No skill weights"]),
            benchmarkCount > 0 ? (
              <span
                key="benchmarks"
                title={`Snapshot ${snapshotDate(benchmarkSnapshot.generatedAt)}`}
              >
                {count(benchmarkCount, "benchmark")}
              </span>
            ) : (
              "No benchmark data"
            ),
          ]}
        />
      </MetaRow>

      {composition.length > 0 && (
        <MetaRow label="This run">
          <SplitBar segments={composition} />
        </MetaRow>
      )}
    </dl>
  );
}

function DisclosureTab({
  controls,
  label,
  onClick,
  open,
}: {
  controls: string;
  label: string;
  onClick: () => void;
  open: boolean;
}) {
  return (
    <button
      aria-controls={controls}
      aria-expanded={open}
      className={cn(
        "flex flex-1 items-center justify-center gap-1.5 px-3 py-1.5 text-xs transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
        open ? "text-foreground" : "text-muted-foreground"
      )}
      onClick={onClick}
      type="button"
    >
      <Icon.chevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
      {label}
    </button>
  );
}

function TuneFields({
  catalog,
  draft,
  grades,
  onChange,
  onChangeModel,
}: {
  catalog: ModelCatalog;
  draft: ModelDraft;
  grades: GradeIndex;
  onChange: (next: ModelDraft) => void;
  onChangeModel: (modelId: string) => void;
}) {
  const supportsLora = draft.supportsLora;
  const supportsFull = draft.supportsFull;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex min-w-0 flex-col gap-1.5">
        <Label className="text-xs text-muted-foreground">Model</Label>
        <Select onValueChange={onChangeModel} value={draft.model}>
          <SelectTrigger className="w-full" size="default">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <ModelSelectOptions catalog={catalog} grades={grades} />
          </SelectContent>
        </Select>
      </div>

      {supportsLora && !supportsFull ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>LoRA fine-tuning</span>
          <span className="ml-auto">adapter weights</span>
        </div>
      ) : supportsLora ? (
        <div className="flex items-center gap-2">
          <Switch
            checked={draft.useLora}
            id={`lora-${draft.id}`}
            onCheckedChange={(useLora) =>
              onChange({
                ...draft,
                hyperparams: {
                  ...draft.hyperparams,
                  learning_rate: useLora ? draft.learningRateLora : draft.learningRateFull,
                },
                useLora,
              })
            }
          />
          <Label className="text-xs" htmlFor={`lora-${draft.id}`}>
            LoRA adapters
          </Label>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <TuningField
          id={`epochs-${draft.id}`}
          label="Epochs"
          min={1}
          onCommit={(v) =>
            onChange({
              ...draft,
              hyperparams: {
                ...draft.hyperparams,
                n_epochs: Number.parseInt(v, 10) || 1,
              },
            })
          }
          parse="int"
          recommended={draft.recommendedValues?.n_epochs}
          value={draft.hyperparams.n_epochs == null ? "" : String(draft.hyperparams.n_epochs)}
        />
        <TuningField
          id={`lr-${draft.id}`}
          label="Learning rate"
          min={0}
          onCommit={(v) =>
            onChange({
              ...draft,
              hyperparams: {
                ...draft.hyperparams,
                learning_rate: Number.parseFloat(v) || 0,
              },
            })
          }
          recommended={draft.recommendedValues?.learning_rate}
          value={String(draft.hyperparams.learning_rate)}
        />
        <TuningField
          hint={
            draft.minBatchSize != null && draft.maxBatchSize != null
              ? ` ${draft.minBatchSize}–${draft.maxBatchSize}`
              : undefined
          }
          id={`batch-${draft.id}`}
          label="Batch"
          max={draft.maxBatchSize}
          min={draft.minBatchSize ?? 1}
          onCommit={(v) =>
            onChange({
              ...draft,
              hyperparams: {
                ...draft.hyperparams,
                batch_size: clampBatchSize(v, draft.minBatchSize, draft.maxBatchSize),
              },
            })
          }
          parse="int"
          recommended={draft.recommendedValues?.batch_size}
          value={draft.hyperparams.batch_size == null ? "" : String(draft.hyperparams.batch_size)}
        />

        {supportsLora && draft.useLora && (
          <>
            <TuningField
              id={`lora-r-${draft.id}`}
              label="Rank"
              min={1}
              onCommit={(v) => {
                const r = Number.parseInt(v, 10) || 1;
                onChange({
                  ...draft,
                  loraParams: {
                    ...draft.loraParams,
                    lora_alpha: Math.max(draft.loraParams.lora_alpha, r),
                    lora_r: r,
                  },
                });
              }}
              parse="int"
              recommended={draft.recommendedValues?.lora_r}
              value={String(draft.loraParams.lora_r)}
            />
            <TuningField
              id={`lora-alpha-${draft.id}`}
              label="Alpha"
              min={1}
              onCommit={(v) =>
                onChange({
                  ...draft,
                  loraParams: {
                    ...draft.loraParams,
                    lora_alpha: Number.parseInt(v, 10) || 1,
                  },
                })
              }
              parse="int"
              value={String(draft.loraParams.lora_alpha)}
            />
            <TuningField
              id={`lora-dropout-${draft.id}`}
              label="Dropout"
              max={1}
              min={0}
              onCommit={(v) =>
                onChange({
                  ...draft,
                  loraParams: {
                    ...draft.loraParams,
                    lora_dropout: Number.parseFloat(v) || 0,
                  },
                })
              }
              step={0.01}
              value={String(draft.loraParams.lora_dropout)}
            />
          </>
        )}
      </div>
    </div>
  );
}

function ModelRow({
  draft,
  catalog,
  estimate,
  grades,
  selected,
  incompatibility,
  skillWeights,
  canRemove,
  onChange,
  onChangeModel,
  onRemove,
  onToggle,
}: {
  draft: ModelDraft;
  catalog: ModelCatalog;
  estimate?: FinetuningEstimateResponse;
  grades: GradeIndex;
  selected: boolean;
  incompatibility?: string;
  skillWeights: Record<string, number>;
  canRemove: boolean;
  onChange: (next: ModelDraft) => void;
  onChangeModel: (modelId: string) => void;
  onRemove: () => void;
  onToggle: () => void;
}) {
  // One panel region for both triggers: two stacked disclosures cost more chrome
  // than the row's own content inside a fixed-width dialog.
  const [panel, setPanel] = useState<"evidence" | "tune" | null>(null);
  const panelId = useId();
  const tier = TIER_META[draft.tier];
  const servingContext = grades.get(draft.model)?.servingContext;
  const openPanel = (next: "evidence" | "tune") =>
    setPanel((prev) => (prev === next ? null : next));
  // Swapping the model under an open evidence panel can leave it with nothing to show.
  const shown = panel === "evidence" && draft.evidence.length === 0 ? null : panel;

  return (
    <li
      className={cn(
        "rounded-md border transition-colors duration-150",
        selected ? "border-primary/40 bg-primary/5" : "border-border/60 bg-card"
      )}
    >
      <div className="flex items-start gap-2.5 px-3 py-2.5">
        {/* h-6 matches the chip line box, so the 16px box centres on it. */}
        <span className="flex h-6 shrink-0 items-center">
          <Checkbox
            aria-label={`Train ${draft.displayName}`}
            checked={selected}
            onCheckedChange={onToggle}
          />
        </span>

        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-center gap-2">
            <ModelProviderChip className="min-w-0" compact model={draft.model} />
            <span className="flex min-w-0 flex-1 items-center gap-1.5 text-xs text-muted-foreground">
              <span className="truncate">{draft.params}</span>
              {tier && (
                <>
                  <span className="text-border">·</span>
                  <span className="shrink-0 font-medium text-foreground" title={tier.description}>
                    {tier.label}
                  </span>
                </>
              )}
            </span>
            <MatchScore
              match={draft.match}
              matchPool={draft.matchPool}
              matchRank={draft.matchRank}
            />
            {estimate?.costEstimate && (
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                <CreditsAmount usd={estimate.costEstimate.usd} />
              </span>
            )}
            {estimate?.timeEstimate && (
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                ~{estimate.timeEstimate.human}
              </span>
            )}
            {canRemove && (
              <Button
                aria-label={`Remove ${draft.displayName}`}
                onClick={onRemove}
                size="icon-xs"
                variant="ghost"
              >
                <Icon.close />
              </Button>
            )}
          </div>

          {draft.evidence.length === 0 && <UngradedNote className="mt-1.5" />}
          {servingContext && (
            <p className="mt-1.5 text-xs text-muted-foreground">
              Serving context {servingContext.maxModelLen.toLocaleString()} tokens
              {" · "}Output budget {servingContext.outputTokens.toLocaleString()} tokens
              {" · "}Estimated from eval data
            </p>
          )}
          {incompatibility && <p className="mt-1.5 text-xs text-destructive">{incompatibility}</p>}
        </div>
      </div>

      <Collapsible open={shown !== null}>
        <div className="flex items-stretch border-t border-border/70">
          {draft.evidence.length > 0 && (
            <>
              <DisclosureTab
                controls={panelId}
                label="Why this model"
                onClick={() => openPanel("evidence")}
                open={shown === "evidence"}
              />
              <span aria-hidden className="w-px shrink-0 bg-border/70" />
            </>
          )}
          <DisclosureTab
            controls={panelId}
            label="Parameters"
            onClick={() => openPanel("tune")}
            open={shown === "tune"}
          />
        </div>
        <CollapsibleContent id={panelId}>
          <div className="border-t border-border/70 px-3 py-3">
            {shown === "evidence" ? (
              <EvidencePanel
                confidence={draft.confidence}
                evidence={draft.evidence}
                nBenchmarks={draft.nBenchmarks}
                skillScores={draft.skillScores}
                weights={skillWeights}
              />
            ) : (
              <TuneFields
                catalog={catalog}
                draft={draft}
                grades={grades}
                onChange={onChange}
                onChangeModel={onChangeModel}
              />
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </li>
  );
}

export function ModelsPanel({ wizard }: { wizard: TrainWizard }) {
  const {
    addModel,
    candidateByModel,
    candidates,
    catalog,
    catalogModelById,
    dataReady,
    drafts,
    estimates,
    evalDataset,
    excluded,
    rec,
    recommendQuery,
    removeModel,
    replaceDraftModel,
    selectedDrafts,
    toggleSelected,
    updateDraft,
    validation,
  } = wizard;
  const [pickerSlot, setPickerSlot] = useState<number | null>(null);

  const stats = validation?.stats ?? {};
  const trainRows = stats.trainExamples ?? 0;
  const valRows = stats.valExamples ?? 0;
  const evalRows = evalDataset?.rows ?? 0;
  const composition: Segment[] =
    trainRows + valRows > 0 && evalRows > 0
      ? [
          // Two shades of one hue for the two halves of the training set, a separate hue
          // for the eval set: they come from different datasets.
          { className: "bg-primary/70", label: "Train", value: trainRows },
          { className: "bg-primary/30", label: "Validation", value: valRows },
          { className: "bg-cat-2/70", label: "Eval", value: evalRows },
        ]
      : [];

  const used = new Set(drafts.map((d) => d.model));
  const selectedIds = new Set(selectedDrafts.map((d) => d.id));
  const canCompare = drafts.length < MAX_MODELS;
  const benchmarkCount = new Set(candidates.flatMap((c) => c.evidence.map((row) => row.benchmark)))
    .size;
  const skillWeights = rec?.skillWeights ?? {};

  return (
    <Column>
      {!dataReady ? (
        <p className="flex h-24 items-center justify-center rounded-md border border-dashed border-border px-4 text-center text-xs text-muted-foreground">
          Pick a capability and a valid dataset.
        </p>
      ) : recommendQuery.isLoading && drafts.length === 0 ? (
        <>
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Spinner size="sm" />
            Reading the dataset and the capability&apos;s context
          </p>
          <Skeleton className="h-20 w-full shrink-0" />
        </>
      ) : (
        <>
          {rec && (
            <AnalysisHeader
              benchmarkCount={benchmarkCount}
              composition={composition}
              format={stats.format}
              rec={rec}
            />
          )}

          {recommendQuery.error != null && drafts.length === 0 && (
            <Alert variant="destructive">
              <Icon.warning className="size-4 shrink-0" />
              <span className="text-sm">
                Couldn&apos;t load recommendations. Add a model to continue.
              </span>
            </Alert>
          )}

          {drafts.length > 0 && (
            <ul aria-label="Experiments" className="flex flex-col gap-2">
              {drafts.map((draft) => (
                <ModelRow
                  canRemove={drafts.length > 1}
                  catalog={catalog}
                  draft={draft}
                  estimate={estimates.get(draft.id)}
                  grades={candidateByModel}
                  incompatibility={excluded.find((entry) => entry.model === draft.model)?.reason}
                  key={draft.id}
                  onChange={(next) => updateDraft(draft.id, next)}
                  onChangeModel={(modelId) => {
                    const found = catalogModelById(modelId);
                    if (!found) return;
                    replaceDraftModel(
                      draft.id,
                      draftWithModel(draft, found.tier, found.model),
                      draft
                    );
                  }}
                  onRemove={() => removeModel(draft.id)}
                  onToggle={() => toggleSelected(draft.id)}
                  selected={selectedIds.has(draft.id)}
                  skillWeights={skillWeights}
                />
              ))}
            </ul>
          )}

          {/* Gone rather than disabled at the ceiling: a permanently dead row reads as
              something broken. */}
          {canCompare && (
            <Popover
              onOpenChange={(open) => setPickerSlot(open ? 0 : null)}
              open={pickerSlot === 0}
            >
              <PopoverTrigger asChild>
                <button
                  className="flex h-9 items-center justify-center gap-1.5 rounded-md border border-dashed border-border text-xs text-muted-foreground outline-none transition-colors hover:border-border/70 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
                  type="button"
                >
                  <Icon.add className="size-3.5" />
                  Add a model
                </button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-80 overflow-hidden p-0">
                <ModelPickerList
                  catalog={catalog}
                  exclude={used}
                  grades={candidateByModel}
                  onPick={(modelId) => {
                    setPickerSlot(null);
                    addModel(modelId);
                  }}
                />
              </PopoverContent>
            </Popover>
          )}

          <ExcludedSummary excluded={excluded} />
        </>
      )}
    </Column>
  );
}
