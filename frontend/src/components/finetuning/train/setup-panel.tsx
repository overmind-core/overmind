import { Link } from "@tanstack/react-router";

import { IntentBadge } from "@/components/datasets/badges";
import { EvalSetEmptyWizardState } from "@/components/evaluations/eval-set-empty-wizard-state";
import { Column, EmptyField, Field, SetupGrid } from "@/components/finetuning/train/field";
import type { TrainWizard } from "@/components/finetuning/train/use-train-wizard";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
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

  // Each consequence block below is gated on the same condition as the control that
  // produces it: a pick outlives the capability it was made under, so the id alone would
  // describe a value the control cannot show.
  const datasetShown =
    !datasetsQuery.isLoading && !!capabilityId && datasets.length > 0 && !!datasetId;
  const shownEvalDataset =
    !evalDatasetsQuery.isLoading && capabilityId && evalDatasets.length > 0
      ? evalDataset
      : undefined;

  return (
    <Column className="border-b border-border/70 pb-4">
      <SetupGrid>
        <Field htmlFor="train-capability" label="Capability">
          {capabilitiesQuery.isLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : capabilities.length === 0 ? (
            <EmptyField>
              {capabilitiesQuery.error
                ? "Couldn't load capabilities."
                : "No capabilities in this project."}
            </EmptyField>
          ) : (
            <Select onValueChange={setCapabilityId} value={capabilityId}>
              <SelectTrigger className="w-full" id="train-capability" size="default">
                {/* Name only: the list carries the measurements, and a busy trigger
                  truncates the one thing the user needs to read. */}
                <SelectValue placeholder="Select a capability">
                  <span className="truncate">{capability?.name}</span>
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
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
          ) : !capabilityId ? (
            <EmptyField>Select a capability first.</EmptyField>
          ) : datasets.length === 0 ? (
            <EmptyField>
              {datasetsQuery.error
                ? "Couldn't load datasets."
                : "No train datasets for this capability."}
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
        </Field>

        <Field
          hint="Scored against the capability's production model and the fine-tuned model. Rows whose source trace is in the training set are dropped from training."
          htmlFor="train-eval-dataset"
          label="Eval Dataset"
        >
          {evalDatasetsQuery.isLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : !capabilityId ? (
            <EmptyField>Select a capability first.</EmptyField>
          ) : evalDatasets.length === 0 ? (
            <EmptyField>No eval datasets for this capability.</EmptyField>
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
              {overlapCount.toLocaleString()} rows also appear in the training set and are excluded
              from training
            </p>
          )}
        </Field>

        <Field
          hint="Graders run against both scored models."
          htmlFor="train-eval-set"
          label="Graders"
        >
          {!capabilityId ? (
            <EmptyField>Select a capability first.</EmptyField>
          ) : evalSetsQuery.isLoading ? (
            <Skeleton className="h-8 w-full" />
          ) : evalSets.length === 0 ? (
            <EvalSetEmptyWizardState
              capabilityId={capabilityId}
              error={evalPreload.data?.error}
              projectId={projectId}
              status={evalPreload.data?.status ?? null}
              variant="compact"
            />
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
                    {set.isActive && <span className="text-xs text-muted-foreground">Active</span>}
                    <span className="text-xs text-muted-foreground">
                      {count(set.generativeCount, "grader")}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </Field>

        {/* Spans the trailing cell so the grid stays closed. */}
        <Field className="sm:col-span-2" htmlFor="train-run-name" label="Name">
          <Input
            id="train-run-name"
            onChange={(e) => setRunName(e.target.value)}
            size="default"
            value={runName}
          />
        </Field>
      </SetupGrid>
    </Column>
  );
}
