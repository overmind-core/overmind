import { Link } from "@tanstack/react-router";

import { IntentBadge } from "@/components/datasets/badges";
import { EvalSetEmptyWizardState } from "@/components/evaluations/eval-set-empty-wizard-state";
import { JudgeModelSelect } from "@/components/evaluations/judge-model-select";
import { Column, EmptyField, Field, SetupGrid } from "@/components/finetuning/train/field";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { ModelOptionLabel } from "@/components/model-option-label";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { count } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import {
  benchmarkContextStatus,
  contextStatusLabel,
  MUTED_MODEL_OPTION,
  WARNING_SELECT,
} from "./context-checks";

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

export function SetupPanel({ wizard, projectId }: { wizard: TrainWizard; projectId: string }) {
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
  const benchmarkStatus = benchmarkContextStatus(wizard, wizard.benchmarkModel);
  const benchmarkWarning = benchmarkStatus === "warning";

  // Each consequence block below is gated on the same condition as the control that
  // produces it: a pick outlives the capability it was made under, so the id alone would
  // describe a value the control cannot show.
  const datasetShown = !datasetsQuery.isLoading && datasets.some((d) => d.id === datasetId);
  const shownEvalDataset =
    !evalDatasetsQuery.isLoading && evalDatasets.some((d) => d.id === evalDatasetId)
      ? evalDataset
      : undefined;

  return (
    <Column className="border-b border-border/70 pb-4">
      <SetupGrid>
        <Field className="sm:col-span-2 lg:col-span-3" htmlFor="train-run-name" label="Name">
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

        <Field
          hint="Split by trace into training and validation rows. Validation rows measure loss and never reach the weights."
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

        <Field
          hint="Used for the selected model evaluations."
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
              {overlapCount.toLocaleString()} training rows overlap this eval dataset. Review the
              split before training.
            </p>
          )}
        </Field>

        <div className="sm:col-span-2 lg:col-span-3">
          <SetupGrid>
            <Field
              className="sm:col-start-1 sm:row-start-1"
              hint="Graders used for each selected evaluation."
              htmlFor="train-eval-set"
              label="Eval set"
            >
              {evalSetsQuery.isLoading ? (
                <Skeleton className="h-8 w-full" />
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
                  <SelectTrigger className="w-full" id="train-eval-set" size="default">
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
            </Field>

            <JudgeModelSelect
              checking={wizard.contextQuery?.isFetching}
              className="sm:col-start-1 sm:row-start-2"
              disabled={!wizard.evalSetId}
              id="train-judge-model"
              onChange={wizard.setJudgeModel}
              report={wizard.contextQuery?.data}
              value={wizard.judgeModel}
            />
            <Field
              className="sm:col-start-2 sm:row-start-1"
              hint="Model to evaluate and compare against in this run."
              htmlFor="train-benchmark-model"
              label="Benchmark model"
            >
              <Select onValueChange={wizard.setBenchmarkModel} value={wizard.benchmarkModel}>
                <SelectTrigger
                  className={cn("w-full", benchmarkWarning && WARNING_SELECT)}
                  id="train-benchmark-model"
                  size="default"
                  title={wizard.selectedBenchmark?.label}
                >
                  <SelectValue
                    className="min-w-0 flex-1"
                    placeholder="Select a model to compare against"
                  >
                    {wizard.selectedBenchmark && (
                      <ModelOptionLabel
                        baseModel={wizard.selectedBenchmark.baseModelId}
                        model={wizard.selectedBenchmark.value}
                      />
                    )}
                  </SelectValue>
                  {benchmarkWarning && <Icon.warning className="size-3 text-warning" />}
                </SelectTrigger>
                <SelectContent>
                  {wizard.benchmarkOptions.map((option) => {
                    const status = benchmarkContextStatus(wizard, option.value);
                    const muted = status === "warning";
                    return (
                      <SelectItem
                        className={cn(
                          "[&>span:last-child]:min-w-0 [&>span:last-child]:flex-1",
                          muted && MUTED_MODEL_OPTION
                        )}
                        key={option.value}
                        textValue={option.label}
                        value={option.value}
                      >
                        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <ModelOptionLabel baseModel={option.baseModelId} model={option.value}>
                            <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                              {option.kind}
                            </span>
                          </ModelOptionLabel>
                          <span className="flex min-w-0 items-center gap-2 pl-5.5">
                            {option.kind === "Trained model" && (
                              <span className="truncate text-xs text-muted-foreground">
                                {option.label}
                              </span>
                            )}
                            {status && (
                              <span
                                className={
                                  status === "fits"
                                    ? "ml-auto shrink-0 text-xs text-success"
                                    : "ml-auto shrink-0 text-xs text-muted-foreground"
                                }
                              >
                                {contextStatusLabel(status)}
                              </span>
                            )}
                          </span>
                        </span>
                      </SelectItem>
                    );
                  })}
                  {wizard.benchmarksQuery.isLoading && (
                    <div className="p-2">
                      <Spinner size="sm" />
                    </div>
                  )}
                  {!wizard.benchmarksQuery.isLoading && wizard.benchmarkOptions.length === 0 && (
                    <div className="p-2 text-sm text-muted-foreground">
                      No benchmark models available.
                    </div>
                  )}
                </SelectContent>
              </Select>
              {wizard.benchmarksQuery.isError && (
                <p className="text-xs text-destructive">Couldn't load trained models.</p>
              )}
              <BenchmarkContextEstimate wizard={wizard} />
            </Field>
            <Field
              className="sm:col-start-2 sm:row-start-2"
              hint="Evaluate the selected benchmark before or after training."
              label="Benchmark evals"
            >
              <div
                aria-label="Incumbent evaluations"
                className="grid grid-cols-2 gap-2"
                role="group"
              >
                <Label
                  className="h-8 rounded-sm bg-control px-3 has-disabled:cursor-not-allowed has-disabled:opacity-50 [&:not(:has(:disabled))]:cursor-pointer [&:not(:has(:disabled))]:hover:bg-control-hover"
                  htmlFor="train-eval-incumbent-before"
                >
                  <Checkbox
                    aria-label="Evaluate incumbent baseline"
                    checked={wizard.evaluationPlan.evalIncumbentBefore}
                    disabled={!wizard.hasIncumbent}
                    id="train-eval-incumbent-before"
                    onCheckedChange={(value) =>
                      wizard.setEvaluationChoice("evalIncumbentBefore", value === true)
                    }
                  />
                  Baseline
                </Label>
                <Label
                  className="h-8 rounded-sm bg-control px-3 has-disabled:cursor-not-allowed has-disabled:opacity-50 [&:not(:has(:disabled))]:cursor-pointer [&:not(:has(:disabled))]:hover:bg-control-hover"
                  htmlFor="train-eval-incumbent-after"
                >
                  <Checkbox
                    aria-label="Evaluate incumbent after training"
                    checked={wizard.evaluationPlan.evalIncumbentAfter}
                    disabled={!wizard.hasIncumbent}
                    id="train-eval-incumbent-after"
                    onCheckedChange={(value) =>
                      wizard.setEvaluationChoice("evalIncumbentAfter", value === true)
                    }
                  />
                  After
                </Label>
              </div>
            </Field>

            <Field
              className="sm:col-start-2 sm:row-start-3 lg:col-start-3 lg:row-start-2"
              hint="Compare the untouched base model with the trained checkpoint."
              label="Training model evals"
            >
              <div
                aria-label="Training model evaluations"
                className="grid grid-cols-2 gap-2"
                role="group"
              >
                <Label
                  className="h-8 cursor-pointer rounded-sm bg-control px-3 hover:bg-control-hover"
                  htmlFor="train-eval-model-before"
                >
                  <Checkbox
                    aria-label="Evaluate base model"
                    checked={wizard.evaluationPlan.evalModelBefore}
                    id="train-eval-model-before"
                    onCheckedChange={(value) =>
                      wizard.setEvaluationChoice("evalModelBefore", value === true)
                    }
                  />
                  Base
                </Label>
                <Label
                  className="h-8 cursor-pointer rounded-sm bg-control px-3 hover:bg-control-hover"
                  htmlFor="train-eval-model-after"
                >
                  <Checkbox
                    aria-label="Evaluate trained model"
                    checked={wizard.evaluationPlan.evalModelAfter}
                    id="train-eval-model-after"
                    onCheckedChange={(value) =>
                      wizard.setEvaluationChoice("evalModelAfter", value === true)
                    }
                  />
                  Trained
                </Label>
              </div>
            </Field>
          </SetupGrid>
        </div>
      </SetupGrid>
    </Column>
  );
}

function BenchmarkContextEstimate({ wizard }: { wizard: TrainWizard }) {
  if (!wizard.evaluationPlan.evalIncumbentBefore && !wizard.evaluationPlan.evalIncumbentAfter) {
    return null;
  }
  const check = wizard.contextQuery?.data?.checks.find(
    (item) => item.role === "generation" && item.model === wizard.benchmarkModel
  );
  return (
    <p className="text-xs text-muted-foreground" role="status">
      {wizard.contextQuery?.isFetching && !wizard.contextQuery.data
        ? "Checking context"
        : check?.contextWindow && check.checkedRows
          ? `${count(check.checkedRows, "row")} · ${check.requiredContext.toLocaleString()} / ${check.contextWindow.toLocaleString()} tokens estimated`
          : "Context unverified"}
    </p>
  );
}
