import { useEffect, useRef, useState } from "react";

import { Link } from "@tanstack/react-router";

import { IntentBadge } from "@/components/datasets/badges";
import { EvalSetEmptyWizardState } from "@/components/evaluations/eval-set-empty-wizard-state";
import { JudgeModelSelect } from "@/components/evaluations/judge-model-select";
import { Column, EmptyField, Field } from "@/components/finetuning/train/field";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { ModelOptionLabel } from "@/components/model-option-label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { count } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import { benchmarkContextStatus, contextStatusLabel } from "./context-checks";
import { MonitoringControls } from "./monitoring-controls";

/** Backend findings read `"<locator>: <reason>"`. */
function splitFinding(text: string): [string | null, string] {
  const match = /^((?:Row|Example|Validation row|Validation example)\s[^:]*):\s*(.+)$/.exec(text);
  return match ? [match[1], match[2]] : [null, text];
}

function Findings({ items, tone }: { items: string[]; tone: "error" | "warning" }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      {items.map((item) => {
        const [locator, reason] = splitFinding(item);
        return (
          <div className="col-span-2 grid grid-cols-subgrid" key={item}>
            <dt
              className={
                locator
                  ? tone === "error"
                    ? "tabular-nums text-destructive"
                    : "tabular-nums text-warning"
                  : "sr-only"
              }
            >
              {locator ?? "—"}
            </dt>
            <dd className="min-w-0 text-muted-foreground">{reason}</dd>
          </div>
        );
      })}
    </dl>
  );
}

export function SetupPanel({
  wizard,
  projectId,
  section,
}: {
  wizard: TrainWizard;
  projectId: string;
  section: "data" | "evaluation";
}) {
  const {
    capability,
    capabilityId,
    capabilities,
    capabilitiesQuery,
    dataset,
    datasetId,
    datasets,
    datasetsQuery,
    evalDataset,
    evalDatasetId,
    evalDatasets,
    evalDatasetsQuery,
    evalPreload,
    evalSet,
    evalSetId,
    evalSets,
    evalSetsQuery,
    overlapCount,
    runName,
    setCapabilityId,
    setDatasetId,
    setEvalDatasetId,
    setEvalSetId,
    setRunName,
    validating,
    validation,
  } = wizard;

  const invalid = !!validation && !validating && validation.valid !== true;

  // Each consequence block below is gated on the same condition as the control that
  // produces it: a pick outlives the capability it was made under, so the id alone would
  // describe a value the control cannot show.
  const datasetShown = !datasetsQuery.isLoading && datasets.some((d) => d.id === datasetId);
  const shownEvalDataset =
    !evalDatasetsQuery.isLoading && evalDatasets.some((d) => d.id === evalDatasetId)
      ? evalDataset
      : undefined;

  return (
    <Column>
      <div className="grid grid-cols-1 items-start gap-x-5 gap-y-4 sm:grid-cols-2">
        {section === "data" && (
          <>
            <Field className="sm:col-span-2" htmlFor="train-run-name" label="Name">
              <Input
                id="train-run-name"
                onChange={(e) => setRunName(e.target.value)}
                size="default"
                value={runName}
              />
            </Field>

            <Field htmlFor="train-capability" label="Capability (optional)">
              {capabilitiesQuery.isLoading ? (
                <Skeleton className="h-8 w-full" />
              ) : (
                <Select
                  onValueChange={(value) => setCapabilityId(value === "none" ? "" : value)}
                  value={capabilityId || "none"}
                >
                  <SelectTrigger className="w-full" id="train-capability" size="default">
                    {/* Name only: the list carries the measurements, and a busy trigger
                  truncates the one thing the user needs to read. */}
                    <SelectValue>
                      <span className="truncate">{capability?.name ?? "None"}</span>
                    </SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">None</SelectItem>
                    {capabilities.map((a) => (
                      <SelectItem key={a.id} value={a.id}>
                        <span className="truncate">{a.name}</span>
                        {a.traceCount ? (
                          <span className="text-xs text-muted-foreground">
                            {a.traceCount.toLocaleString()} traces
                          </span>
                        ) : null}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </Field>
          </>
        )}

        {section === "data" && (
          <Field
            hint="20% of training data is held out for validation."
            htmlFor="train-dataset"
            label="Training Dataset"
          >
            {datasetsQuery.isLoading ? (
              <Skeleton className="h-8 w-full" />
            ) : datasets.length === 0 ? (
              <EmptyField>
                {datasetsQuery.error
                  ? "Couldn't load datasets."
                  : capabilityId
                    ? "No train datasets for this capability."
                    : "No train datasets in this project."}
              </EmptyField>
            ) : (
              <Select onValueChange={setDatasetId} value={datasetId}>
                <SelectTrigger className="w-full" id="train-dataset" size="default">
                  <SelectValue placeholder="Select a dataset">
                    <span className="truncate">{dataset?.name || "Untitled"}</span>
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {datasets.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      <span className="truncate">{d.name || "Untitled"}</span>
                      <span className="text-xs text-muted-foreground">
                        {d.activeVersion} · {count(d.rows ?? 0, "row")}
                      </span>
                      <IntentBadge intent={d.intent} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {datasetShown && (
              <>
                {validating && (
                  <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Spinner size="sm" />
                    Validating
                  </p>
                )}

                {invalid && dataset && (
                  <div className="flex flex-col gap-2 rounded-md border border-destructive/40 p-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <p className="flex items-center gap-1.5 text-xs font-medium text-destructive">
                        <Icon.warning className="size-3.5 shrink-0" />
                        Can&apos;t be trained on yet
                      </p>
                      <Button asChild size="xs" variant="secondary">
                        <Link params={{ datasetId: dataset.id }} to="/datasets/$datasetId">
                          <Icon.workshop />
                          Fix in workshop
                        </Link>
                      </Button>
                    </div>
                    <div className="max-h-24 overflow-y-auto">
                      {validation.errors.length > 0 && (
                        <Findings items={validation.errors} tone="error" />
                      )}
                      {validation.warnings.length > 0 && (
                        <Findings items={validation.warnings} tone="warning" />
                      )}
                    </div>
                  </div>
                )}
              </>
            )}
            {datasetShown && !invalid && !!validation?.warnings.length && (
              <Findings items={validation.warnings} tone="warning" />
            )}
          </Field>
        )}

        {section === "data" && wizard.monitoring && (
          <MonitoringControls onChange={wizard.setMonitoring} value={wizard.monitoring} />
        )}
        {section === "evaluation" &&
          (wizard.nativeDecision ? (
            <div className="sm:col-span-2 text-sm">
              <p className="font-medium">Native probability training · Modal LoRA</p>
              <p className="text-muted-foreground">
                Full probability targets · decision cross entropy · typed probability output. Select
                separate calibration and final suites on the run page.
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                The baseline measures the starting model on development data. Training validation
                and final evaluations run separately.
              </p>
            </div>
          ) : wizard.evaluationEnabled ? (
            <>
              <Field
                hint="The graders used to score each model."
                htmlFor="train-eval-set"
                label="Eval set"
              >
                <div className="flex min-w-0 items-center gap-2">
                  {evalSetsQuery.isLoading ? (
                    <Skeleton className="h-8 min-w-0 flex-1" />
                  ) : evalSets.length === 0 && capabilityId ? (
                    <EvalSetEmptyWizardState
                      capabilityId={capabilityId}
                      error={evalPreload.data?.error}
                      projectId={projectId}
                      status={evalPreload.data?.status ?? null}
                      variant="compact"
                    />
                  ) : evalSets.length === 0 ? (
                    <EmptyField>No eval sets in this project.</EmptyField>
                  ) : (
                    <Select onValueChange={setEvalSetId} value={evalSetId}>
                      <SelectTrigger className="min-w-0 flex-1" id="train-eval-set" size="default">
                        <SelectValue placeholder="Select an eval set">
                          <span className="truncate">{evalSet?.name}</span>
                        </SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {evalSets.map((set) => (
                          <SelectItem key={set.id} value={set.id}>
                            <span className="truncate">{set.name}</span>
                            {!capabilityId && (
                              <span className="text-xs text-muted-foreground">
                                {capabilities.find((c) => c.id === set.capability)?.name}
                              </span>
                            )}
                            {set.isActive && (
                              <span className="text-xs text-muted-foreground">Active</span>
                            )}
                            <span className="text-xs text-muted-foreground">
                              {count(set.generativeCount, "grader")}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  )}
                  {evalSetId && (
                    <Popover>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <PopoverTrigger asChild>
                            <Button
                              aria-label="Change judge model"
                              className="shrink-0"
                              size="icon"
                              type="button"
                              variant="default"
                            >
                              <Icon.edit />
                            </Button>
                          </PopoverTrigger>
                        </TooltipTrigger>
                        <TooltipContent side="top">Change judge model</TooltipContent>
                      </Tooltip>
                      <PopoverContent align="end" className="w-96 max-w-[calc(100vw-2rem)]">
                        <JudgeModelSelect
                          checking={wizard.contextQuery?.isFetching}
                          compact
                          id="train-judge-model"
                          onChange={wizard.setJudgeModel}
                          report={wizard.contextQuery?.data}
                          value={wizard.judgeModel}
                        />
                      </PopoverContent>
                    </Popover>
                  )}
                </div>
              </Field>

              <Field
                hint="The held-out examples scored with the selected eval set."
                htmlFor="train-eval-dataset"
                label="Eval Dataset"
              >
                {evalDatasetsQuery.isLoading ? (
                  <Skeleton className="h-8 w-full" />
                ) : evalDatasets.length === 0 ? (
                  <EmptyField>
                    {capabilityId
                      ? "No eval datasets for this capability."
                      : "No eval datasets in this project."}
                  </EmptyField>
                ) : (
                  <Select onValueChange={setEvalDatasetId} value={evalDatasetId}>
                    <SelectTrigger className="w-full" id="train-eval-dataset" size="default">
                      <SelectValue placeholder="Select an eval dataset">
                        <span className="truncate">{evalDataset?.name || "Untitled"}</span>
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {evalDatasets.map((d) => (
                        <SelectItem key={d.id} value={d.id}>
                          <span className="truncate">{d.name || "Untitled"}</span>
                          <span className="text-xs text-muted-foreground">
                            {d.activeVersion} · {count(d.rows ?? 0, "row")}
                          </span>
                          <IntentBadge intent={d.intent} />
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
                {shownEvalDataset && overlapCount > 0 && (
                  <p className="flex items-start gap-1.5 text-xs text-warning">
                    <Icon.warning className="mt-0.5 size-3 shrink-0" />
                    {overlapCount.toLocaleString()} training rows overlap this eval dataset. Review
                    the split before training.
                  </p>
                )}
              </Field>

              <div className="sm:col-span-2">
                <BenchmarkSelector wizard={wizard} />
              </div>
            </>
          ) : null)}
      </div>
    </Column>
  );
}

function BenchmarkSelector({ wizard }: { wizard: TrainWizard }) {
  const [search, setSearch] = useState("");
  const initializedTrainingBases = useRef(new Set<string>());
  const selected = wizard.benchmarkModels;
  const trainingBases = wizard.selectedDrafts.map((draft) => ({
    baseModelId: draft.model,
    kind: "Training base",
    label: draft.model,
    value: draft.model,
  }));
  const allChoices = [...trainingBases, ...wizard.benchmarkOptions].filter(
    (option, index, options) => options.findIndex((row) => row.value === option.value) === index
  );
  const choices = allChoices.filter((option) =>
    `${option.label} ${option.value}`.toLowerCase().includes(search.toLowerCase())
  );

  useEffect(() => {
    if (!wizard.benchmarkingEnabled) return;
    for (const option of trainingBases) {
      if (selected.includes(option.value) || initializedTrainingBases.current.has(option.value)) {
        continue;
      }
      initializedTrainingBases.current.add(option.value);
      wizard.toggleBenchmarkModel(option.value);
    }
  }, [selected, trainingBases, wizard.benchmarkingEnabled, wizard.toggleBenchmarkModel]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Checkbox
          aria-label="Benchmark models"
          checked={wizard.benchmarkingEnabled}
          id="train-benchmark-enabled"
          onCheckedChange={(value) => wizard.setBenchmarkingEnabled(value === true)}
        />
        <h4 className="text-base font-medium">
          <Label className="cursor-pointer text-base font-medium" htmlFor="train-benchmark-enabled">
            Benchmark models
          </Label>
        </h4>
        <p className="ml-1 text-xs text-muted-foreground">
          Compare starting models; trained models are scored after training.
        </p>
      </div>
      {wizard.benchmarkingEnabled && (
        <div className="flex flex-col gap-3 rounded-md border border-border/70 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-20 shrink-0 text-xs text-muted-foreground">Compare with</span>
            <Popover>
              <PopoverTrigger asChild>
                <Button type="button" variant="default">
                  <Icon.add /> Add model
                </Button>
              </PopoverTrigger>
              <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)] p-2">
                <Input
                  aria-label="Search benchmark models"
                  onChange={(event) => setSearch(event.target.value)}
                  placeholder="Search OpenRouter and trained models"
                  value={search}
                />
                <div className="mt-2 max-h-64 overflow-y-auto">
                  {choices.map((option) => {
                    const isTrainingBase = option.kind === "Training base";
                    const status = benchmarkContextStatus(wizard, option.value);
                    return (
                      <button
                        aria-pressed={selected.includes(option.value)}
                        className={cn(
                          "flex min-h-9 w-full items-center gap-2 rounded-sm px-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                          isTrainingBase
                            ? "bg-info/10 text-info hover:bg-info/20"
                            : "hover:bg-muted",
                          selected.includes(option.value) &&
                            (isTrainingBase ? "bg-info/20" : "bg-muted")
                        )}
                        key={option.value}
                        onClick={() => wizard.toggleBenchmarkModel(option.value)}
                        type="button"
                      >
                        <span
                          aria-hidden="true"
                          className="flex size-4 shrink-0 items-center justify-center rounded-xs border border-current/50"
                        >
                          {selected.includes(option.value) && <Icon.success className="size-3" />}
                        </span>
                        <span className="min-w-0 flex-1 truncate">
                          <ModelOptionLabel baseModel={option.baseModelId} model={option.value} />
                        </span>
                        <span className="shrink-0 text-xs text-current/70">
                          {isTrainingBase
                            ? "Base"
                            : option.kind === "Codebase incumbent"
                              ? "Incumbent"
                              : status
                                ? contextStatusLabel(status)
                                : option.kind}
                        </span>
                      </button>
                    );
                  })}
                  {choices.length === 0 && (
                    <p className="p-2 text-xs text-muted-foreground">No models found.</p>
                  )}
                </div>
              </PopoverContent>
            </Popover>
            {selected.map((model) => {
              const option = allChoices.find((row) => row.value === model);
              return (
                <button
                  aria-label={`Remove ${option?.label ?? model} from benchmarks`}
                  className="inline-flex min-h-8 items-center gap-2 rounded-sm border border-border/70 px-2.5 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                  key={model}
                  onClick={() => wizard.toggleBenchmarkModel(model)}
                  type="button"
                >
                  <ModelOptionLabel baseModel={option?.baseModelId} model={model} />
                  {option?.kind === "Training base" ? (
                    <span className="text-info">Base</span>
                  ) : option?.kind === "Codebase incumbent" ? (
                    <span className="text-muted-foreground">Incumbent</span>
                  ) : null}
                  <Icon.close className="size-3 text-muted-foreground" />
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
