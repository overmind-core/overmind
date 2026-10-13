import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import apiClient from "@/client";
import { MetricBars } from "@/components/evaluations/metric-bars";
import { ModelOptionLabel } from "@/components/model-option-label";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Failure, WorkflowState } from "@/components/model-workflows/common";
import {
  ComparisonMetrics,
  type Comparisons,
  formatNativeMetric,
  nativeMetricLabel,
  nativePercentageMetrics,
  PairedMetrics,
} from "@/components/model-workflows/comparison-metrics";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { Progress } from "@/components/ui/progress";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState, Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { TITLE } from "@/lib/typography";
import type {
  NativeBenchmarkProgress,
  NativeCallProgress,
  NativeEvaluation,
  NativeEvaluationsMonitorRetrieveOutcomeEnum,
  NativeModelProgress,
  NativeMonitor,
  NativeSample,
} from "@/openapi";

type Role = "final" | "calibration";
const outcomeLabels: Record<NativeEvaluationsMonitorRetrieveOutcomeEnum, string> = {
  all: "All outcomes",
  correct: "Correct",
  incorrect: "Incorrect",
  missing: "Missing output",
  unscored: "Unscored",
};
const terminal = new Set([
  "completed",
  "failed",
  "paused",
  "submission_unknown",
  "draft",
  "prepared",
]);
const preferredMetrics = ["accuracy", "cross_entropy", "brier", "expected_score_mae", "rps"];
const phases: Record<string, string> = {
  completed: "Predictions collected",
  configuring_adapters: "Loading adapter",
  initialising: "Initialising worker",
  initializing_training_runtime: "Initialising runtime",
  loading_model: "Loading model",
  predicting: "Predicting",
  preparing_inputs: "Preparing inputs",
  verifying_training_tokenizer: "Verifying tokenizer",
  waiting: "Waiting",
  waiting_for_worker: "Waiting for worker",
};
const percent = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";
const text = (value: unknown) =>
  typeof value === "string" ? value : (JSON.stringify(value, null, 2) ?? "—");

function metricsFor(arms: NativeModelProgress[]) {
  const found = new Set(
    arms.flatMap((arm) => arm.benchmarks.flatMap((benchmark) => Object.keys(benchmark.raw)))
  );
  const primary = preferredMetrics.filter((name) => found.has(name));
  return primary.length
    ? primary
    : ["mode_agreement", "top1_reference_mass"].filter((name) => found.has(name));
}

function mean(
  benchmarks: NativeBenchmarkProgress[],
  metric: string,
  mode: "raw" | "calibrated" = "raw"
) {
  const values = benchmarks.flatMap((b) => (b[mode][metric] ? [b[mode][metric].mean] : []));
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : undefined;
}

function LiveMetric({
  benchmarks,
  metric,
}: {
  benchmarks: NativeBenchmarkProgress[];
  metric: string;
}) {
  const adjusted = mean(benchmarks, metric, "calibrated");
  const count = benchmarks.reduce(
    (sum, benchmark) => sum + (benchmark.raw[metric]?.decisions ?? 0),
    0
  );
  return (
    <div
      className="font-mono text-xs tabular-nums"
      title={`${count} scored decisions; average of measured benchmark means`}
    >
      {formatNativeMetric(mean(benchmarks, metric), metric)}
      {adjusted !== undefined && (
        <div className="mt-1 text-muted-foreground">
          {formatNativeMetric(adjusted, metric)} adjusted
        </div>
      )}
    </div>
  );
}

function Stage({ call, stopped }: { call: NativeCallProgress; stopped: boolean }) {
  const active = !stopped && ["running", "submitting"].includes(call.state);
  const sourceAt =
    typeof call.progress?.source_at === "number" ? new Date(call.progress.source_at * 1000) : null;
  const stale = active && sourceAt && Date.now() - sourceAt.getTime() > 60_000;
  return (
    <div className="space-y-1 text-xs">
      <div className="flex items-center gap-2">
        {active && <Spinner className="size-3.5" />}
        {call.error ? (
          <WorkflowState state={call.state} />
        ) : (
          <span>{phases[call.stage] ?? call.stage.replaceAll("_", " ")}</span>
        )}
      </div>
      {sourceAt && (
        <div className="text-muted-foreground">
          {stale ? "Last worker update" : "Worker update"} <DateTime value={sourceAt} />
        </div>
      )}
      {call.progressError && (
        <p className="max-w-64 whitespace-normal text-warning">{call.progressError}</p>
      )}
      {call.error && <p className="max-w-64 whitespace-normal text-destructive">{call.error}</p>}
    </div>
  );
}

function Execution({ data, plan }: { data: NativeMonitor; plan: NativeEvaluation }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const metrics = metricsFor(data.arms);
  const stage =
    plan.calls?.score?.state === "running"
      ? "Scoring final results"
      : plan.calls?.fit_calibration?.state === "running"
        ? "Fitting calibration"
        : plan.calls?.verify_inputs?.state !== "completed"
          ? "Verifying inputs"
          : data.role === "calibration"
            ? "Calibration"
            : "Final benchmark";
  return (
    <section aria-label="Benchmark execution" className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className={TITLE.card}>{stage}</h3>
        <div aria-live="polite" className="flex items-center gap-2 text-xs text-muted-foreground">
          <WorkflowState state={plan.state} />
          <span>Provisional metrics</span>
        </div>
      </div>
      {metrics.length > 0 && (
        <div className="order-last grid gap-4 xl:grid-cols-2">
          {metrics.slice(0, 4).map((metric) => (
            <MetricBars
              benchmark="Provisional"
              key={metric}
              percentage={nativePercentageMetrics.has(metric)}
              rows={data.arms.flatMap((arm, colorIndex) => {
                const value = mean(arm.benchmarks, metric);
                return value === undefined
                  ? []
                  : [{ colorIndex, key: arm.key, name: arm.name, value }];
              })}
              title={nativeMetricLabel(metric)}
            />
          ))}
        </div>
      )}
      <div className="overflow-hidden rounded-md border border-border">
        <Table aria-label="Model execution progress">
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead>Stage</TableHead>
              <TableHead>Scored</TableHead>
              {metrics.map((metric) => (
                <TableHead key={metric}>{nativeMetricLabel(metric)}</TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.calls.map((call) => {
              const arm = data.arms.find((row) => row.key === call.key);
              const expected =
                arm?.expected ??
                (data.role === "final" ? plan.finalSuite.rows : plan.calibrationSuite?.rows) ??
                0;
              return (
                <Fragment key={call.key}>
                  <TableRow>
                    <TableCell>
                      <div className="flex max-w-96 items-center gap-2">
                        {(arm?.benchmarks.length ?? 0) > 1 && (
                          <Button
                            aria-expanded={expanded.has(call.key)}
                            aria-label={`Benchmarks for ${call.name}`}
                            onClick={() =>
                              setExpanded((previous) => {
                                const next = new Set(previous);
                                if (next.has(call.key)) next.delete(call.key);
                                else next.add(call.key);
                                return next;
                              })
                            }
                            size="icon-sm"
                            variant="ghost"
                          >
                            {expanded.has(call.key) ? <Icon.chevronDown /> : <Icon.chevronRight />}
                          </Button>
                        )}
                        <ModelProviderChip model={call.name} />
                      </div>
                    </TableCell>
                    <TableCell>
                      <Stage call={call} stopped={terminal.has(plan.state)} />
                    </TableCell>
                    <TableCell>
                      <div className="w-36 space-y-2 py-1 text-xs tabular-nums">
                        <span>
                          {arm?.scored ?? 0} / {expected}
                        </span>
                        <Progress
                          label={`${call.name} scored decisions`}
                          percent={expected ? ((arm?.scored ?? 0) / expected) * 100 : null}
                        />
                        {!!((arm?.invalid ?? 0) + (arm?.incompatible ?? 0)) && (
                          <span className="text-warning">
                            {arm?.invalid ?? 0} invalid · {arm?.incompatible ?? 0} incompatible
                          </span>
                        )}
                      </div>
                    </TableCell>
                    {metrics.map((metric) => (
                      <TableCell key={metric}>
                        <LiveMetric benchmarks={arm?.benchmarks ?? []} metric={metric} />
                      </TableCell>
                    ))}
                  </TableRow>
                  {expanded.has(call.key) &&
                    arm?.benchmarks.map((benchmark) => (
                      <TableRow className="bg-wash-subtle" key={benchmark.name}>
                        <TableCell className="pl-10">{benchmark.name}</TableCell>
                        <TableCell />
                        <TableCell>
                          {benchmark.scored} / {benchmark.expected}
                        </TableCell>
                        {metrics.map((metric) => (
                          <TableCell key={metric}>
                            <LiveMetric benchmarks={[benchmark]} metric={metric} />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </section>
  );
}

function ReferenceSummary({ sample }: { sample: NativeSample }) {
  if (sample.error)
    return (
      <Badge className="gap-1" size="chip" title={sample.error} variant="warning">
        <Icon.warning aria-hidden className="size-3" /> Unscored
      </Badge>
    );
  const reference = sample.reference;
  const probabilities = reference?.probabilities as number[] | undefined;
  const hardIndex = probabilities?.indexOf(1) ?? -1;
  const label =
    hardIndex >= 0
      ? text(sample.input?.options?.[hardIndex])
      : probabilities
        ? "Soft target"
        : typeof reference?.mean === "number"
          ? `Mean ${reference.mean}`
          : "No reference";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge
          aria-label={`Reference: ${label}`}
          className="max-w-full gap-1.5"
          size="chip"
          variant="neutral"
        >
          <Icon.target aria-hidden className="size-3" />
          <span className="truncate">{label}</span>
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-80">
        {probabilities ? (
          <dl className="space-y-1">
            {probabilities.map((value, index) => (
              <div className="flex justify-between gap-4" key={index}>
                <dt>{text(sample.input?.options?.[index])}</dt>
                <dd className="font-mono tabular-nums">{percent(value)}</dd>
              </div>
            ))}
          </dl>
        ) : (
          label
        )}
      </TooltipContent>
    </Tooltip>
  );
}

function PredictionSummary({
  sample,
  arm,
  stopped,
}: {
  sample: NativeSample;
  arm: string;
  stopped: boolean;
}) {
  const output = sample.predictions[arm];
  if (sample.error || output?.error)
    return (
      <Badge className="gap-1" size="chip" title={sample.error || output?.error} variant="warning">
        <Icon.warning aria-hidden className="size-3" />
        Unscored
      </Badge>
    );
  const p = output?.raw?.probabilities as number[] | undefined;
  if (!p?.length)
    return (
      <Badge className="gap-1" size="chip" variant="neutral">
        <Icon.job aria-hidden className="size-3" />
        {stopped ? "No output" : "Pending"}
      </Badge>
    );
  const index = p.indexOf(Math.max(...p));
  const option = text(sample.input?.options?.[index]);
  const accuracy = output.metrics.accuracy;
  const outcome = accuracy === 1 ? "Correct" : accuracy === 0 ? "Incorrect" : "Prediction";
  const variant = accuracy === 1 ? "success" : accuracy === 0 ? "error" : "neutral";
  const OutcomeIcon = accuracy === 1 ? Icon.success : accuracy === 0 ? Icon.failed : Icon.model;
  const tied = p.filter((value) => value === p[index]).length > 1;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          aria-label={`${outcome}: ${option}. Probability ${percent(p[index])}${tied ? ". Tied maximum" : ""}`}
          className="flex min-w-0 items-center justify-between gap-2 text-xs"
          role="group"
        >
          <Badge className="min-w-0 gap-1.5" size="chip" variant={variant}>
            <OutcomeIcon aria-hidden className="size-3" />
            <span className="truncate">{option}</span>
          </Badge>
          <div aria-hidden className="w-12 shrink-0 space-y-1">
            <div className="text-right font-mono tabular-nums">{percent(p[index])}</div>
            <div className="h-1 overflow-hidden rounded-sm bg-muted">
              <div className="h-full bg-muted-foreground" style={{ width: `${p[index] * 100}%` }} />
            </div>
          </div>
        </div>
      </TooltipTrigger>
      <TooltipContent className="max-w-80 space-y-1">
        <p className="font-medium">
          {outcome}: {option}
        </p>
        <p>Prediction probability: {percent(p[index])}</p>
        {tied && <p>Tied maximum; the first option in the recorded order is scored.</p>}
        {accuracy !== 0 && accuracy !== 1 && <p>No single-answer correctness score.</p>}
      </TooltipContent>
    </Tooltip>
  );
}

function SampleModelOutput({
  sample,
  model,
  stopped,
}: {
  sample: NativeSample;
  model: NativeCallProgress;
  stopped: boolean;
}) {
  const output = sample.predictions[model.key];
  const options = (sample.input?.options ?? []) as unknown[];
  const reference = sample.reference;
  const raw = output?.error ? null : (output?.raw?.probabilities as number[] | undefined);
  const adjusted = output?.error
    ? null
    : (output?.calibrated?.probabilities as number[] | undefined);
  const numericColumns =
    1 + Number(!!adjusted) + Number(!!reference?.probabilities) + Number(!!reference?.values);
  return (
    <section aria-label={`Output for ${model.name}`} className="min-w-0 divide-y divide-border/70">
      <div className="px-4 py-3">
        <ModelProviderChip model={model.name} />
      </div>
      {output?.error && <p className="px-4 py-3 text-xs text-warning">{output.error}</p>}
      {!output && (
        <p className="px-4 py-3 text-xs text-muted-foreground">
          {stopped ? "No output recorded for this model." : "Waiting for this model's prediction."}
        </p>
      )}
      {typeof reference?.mean === "number" && (
        <div className="flex flex-wrap gap-x-6 gap-y-2 px-4 py-3 text-xs">
          <span>
            Reference mean <span className="font-mono">{reference.mean}</span>
          </span>
          <span>
            Predicted mean{" "}
            <span className="font-mono">{output?.metrics.expected_score?.toFixed(4) ?? "—"}</span>
          </span>
        </div>
      )}
      <Table
        aria-label="Option probabilities"
        className="table-fixed"
        style={{ minWidth: `${7 + numericColumns * 5.5}rem` }}
      >
        <TableHeader>
          <TableRow>
            <TableHead className="pl-4" scope="col">
              Option
            </TableHead>
            {reference?.values && (
              <TableHead className="w-22" scope="col">
                Value
              </TableHead>
            )}
            <TableHead className="w-22" scope="col">
              Probability
            </TableHead>
            {adjusted && (
              <TableHead className="w-22" scope="col">
                Adjusted
              </TableHead>
            )}
            {reference?.probabilities && (
              <TableHead className="w-22" scope="col">
                Reference
              </TableHead>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {options.map((option, index) => (
            <TableRow key={index}>
              <TableCell className="whitespace-normal break-words pl-4 text-xs">
                {text(option)}
              </TableCell>
              {reference?.values && (
                <TableCell className="font-mono text-xs tabular-nums">
                  {reference.values[index]}
                </TableCell>
              )}
              <TableCell className="font-mono text-xs tabular-nums">
                {percent(raw?.[index])}
              </TableCell>
              {adjusted && (
                <TableCell className="font-mono text-xs tabular-nums">
                  {percent(adjusted[index])}
                </TableCell>
              )}
              {reference?.probabilities && (
                <TableCell className="font-mono text-xs tabular-nums text-muted-foreground">
                  {percent(reference.probabilities[index])}
                </TableCell>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {!!output && (
        <dl aria-label="Evaluation scores" className="divide-y divide-border/70">
          {preferredMetrics
            .filter((metric) => output.metrics[metric] !== undefined)
            .map((metric) => (
              <div
                className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-4 py-3 text-xs"
                key={metric}
              >
                <dt className="text-muted-foreground">{nativeMetricLabel(metric)}</dt>
                <dd className="ml-auto font-mono tabular-nums">
                  {formatNativeMetric(output.metrics[metric], metric)}
                  {output.calibratedMetrics[metric] !== undefined && (
                    <span className="ml-3 text-muted-foreground">
                      {formatNativeMetric(output.calibratedMetrics[metric], metric)} adjusted
                    </span>
                  )}
                </dd>
              </div>
            ))}
        </dl>
      )}
    </section>
  );
}

function SampleInspector({
  sample,
  calls,
  stopped,
}: {
  sample: NativeSample;
  calls: NativeCallProgress[];
  stopped: boolean;
}) {
  return (
    <>
      <DialogHeader>
        <DialogTitle>Datapoint {sample.index}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex min-h-0 flex-1 flex-col p-0">
        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-5 lg:overflow-hidden">
          <section
            aria-label="Decision input"
            className="flex min-w-0 flex-col border-b border-border lg:col-span-2 lg:min-h-0 lg:border-r lg:border-b-0"
          >
            <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border px-5">
              <Icon.dataset className="size-3.5 text-muted-foreground" />
              <h3 className="text-xs font-medium text-muted-foreground">Input</h3>
            </div>
            <div className="divide-y divide-border/70 lg:min-h-0 lg:flex-1 lg:overflow-y-auto">
              <div className="space-y-1.5 px-4 py-3">
                <h4 className="text-xs font-semibold text-muted-foreground">State</h4>
                <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                  {text(sample.input?.state)}
                </pre>
              </div>
              <div className="space-y-1.5 px-4 py-3">
                <h4 className="text-xs font-semibold text-muted-foreground">Question</h4>
                <p className="whitespace-pre-wrap break-words text-xs">
                  {text(sample.input?.question)}
                </p>
              </div>
              <div className="break-words px-4 py-3 text-xs text-muted-foreground">
                {sample.benchmark} · {sample.input?.kind ?? "Incompatible input"}
              </div>
              {sample.error && <p className="px-4 py-3 text-xs text-warning">{sample.error}</p>}
            </div>
          </section>
          <section
            aria-label="Decision output and evaluation"
            className="flex min-w-0 flex-col lg:col-span-3 lg:min-h-0"
          >
            <div className="flex h-10 shrink-0 items-center border-b border-border px-5">
              <h3 className="text-xs font-medium text-muted-foreground">Output and evaluation</h3>
            </div>
            <div
              aria-label="Model outputs"
              className="divide-y divide-border outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset lg:min-h-0 lg:flex-1 lg:overflow-y-auto"
              role="region"
              // biome-ignore lint/a11y/noNoninteractiveTabindex: Keyboard users need to scroll the model outputs independently of the input pane.
              tabIndex={0}
            >
              {calls.map((model) => (
                <SampleModelOutput
                  key={model.key}
                  model={model}
                  sample={sample}
                  stopped={stopped}
                />
              ))}
            </div>
          </section>
        </div>
      </DialogBody>
    </>
  );
}

export function ProbabilityEvaluationMonitor({ plan }: { plan: NativeEvaluation }) {
  const datapointsRef = useRef<HTMLElement>(null);
  const [chosenRole, setRole] = useState<Role | null>(null);
  const role =
    chosenRole ??
    (plan.calibrationSuite && !plan.calibration?.arms && !terminal.has(plan.state)
      ? "calibration"
      : "final");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search.trim(), 400);
  const [filters, setFilters] = useState({
    outcome: "all" as NativeEvaluationsMonitorRetrieveOutcomeEnum,
    page: 1,
    participant: "all",
    search: "",
  });
  const page = filters.page;
  const setPage = (page: number) => setFilters((current) => ({ ...current, page }));
  const [pageSize, setPageSize] = useState(25);
  const [selected, setSelected] = useState<string | null>(null);
  const changeFilters = (next: Partial<typeof filters>) => {
    setFilters((current) => ({ ...current, ...next, page: 1 }));
    setSelected(null);
  };
  const clearFilters = () => {
    setSearch("");
    changeFilters({ outcome: "all", participant: "all", search: "" });
  };
  const hasActiveFilters =
    !!search.trim() || filters.participant !== "all" || filters.outcome !== "all";
  useEffect(() => {
    setFilters((current) =>
      current.search === debouncedSearch
        ? current
        : {
            ...current,
            page: 1,
            search: debouncedSearch,
          }
    );
  }, [debouncedSearch]);
  const query = useQuery({
    queryFn: () =>
      apiClient.nativeEvaluations.nativeEvaluationsMonitorRetrieve({
        id: plan.id,
        limit: pageSize,
        offset: (page - 1) * pageSize,
        outcome: filters.outcome,
        participant: filters.participant === "all" ? undefined : filters.participant,
        role,
        search: filters.search || undefined,
      }),
    queryKey: ["native-monitor", plan.id, role, filters, pageSize, plan.state],
    refetchInterval: (query) =>
      !terminal.has(plan.state) ||
      (query.state.data?.state === "pending" && plan.calls?.verify_inputs?.state === "completed")
        ? 5000
        : false,
  });
  useEffect(() => {
    if (query.data && page > 1 && (page - 1) * pageSize >= query.data.count) {
      const lastPage = Math.max(1, Math.ceil(query.data.count / pageSize));
      setFilters((current) => ({ ...current, page: lastPage }));
    }
  }, [query.data, page, pageSize]);
  const detail = useQuery({
    enabled: selected !== null,
    queryFn: () =>
      apiClient.nativeEvaluations.nativeEvaluationsMonitorRetrieve({
        id: plan.id,
        key: selected!,
        limit: 1,
        role,
      }),
    queryKey: ["native-sample", plan.id, role, selected],
    refetchInterval: (query) =>
      !terminal.has(plan.state) ||
      query.state.data?.state === "pending" ||
      (query.state.data !== undefined && query.state.data.evaluationState !== plan.state)
        ? 5000
        : false,
  });
  const data = query.data;
  const sample = detail.data?.samples[0];
  const comparisons =
    role === "final" ? plan.results?.comparisons : plan.results?.calibration?.comparisons;
  const names = Object.fromEntries(
    ((plan.config?.participants ?? []) as { key: string; name: string }[]).map((participant) => [
      participant.key,
      participant.name,
    ])
  );
  const stopped = terminal.has(plan.state);
  const columns = useMemo<ColumnDef<NativeSample>[]>(
    () => [
      {
        cell: ({ row }) => (
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {row.original.index}
          </span>
        ),
        enableResizing: false,
        header: "#",
        id: "index",
        size: 48,
      },
      {
        cell: ({ row }) => (
          <span
            className="block truncate text-xs text-muted-foreground"
            title={`${text(row.original.input?.question)}\n${text(row.original.input?.state)}`}
          >
            {text(row.original.input?.state ?? row.original.input?.question)}
          </span>
        ),
        header: "Input",
        id: "input",
        size: 320,
      },
      {
        cell: ({ row }) => <ReferenceSummary sample={row.original} />,
        header: "Reference",
        id: "reference",
        size: 160,
      },
      ...((plan.config?.participants ?? []) as { key: string; name: string }[]).map(
        (participant): ColumnDef<NativeSample> => ({
          cell: ({ row }) => (
            <PredictionSummary arm={participant.key} sample={row.original} stopped={stopped} />
          ),
          header: () => <ModelProviderChip compact model={participant.name} />,
          id: `prediction:${participant.key}`,
          size: 224,
        })
      ),
    ],
    [plan.config?.participants, stopped]
  );
  return (
    <section className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {plan.calibrationSuite ? (
          <Tabs
            className="w-auto"
            onValueChange={(value) => {
              setRole(value as Role);
              setPage(1);
              setSelected(null);
            }}
            value={role}
          >
            <TabsList aria-label="Benchmark suite">
              <TabsTrigger value="final">Final benchmark</TabsTrigger>
              <TabsTrigger value="calibration">Calibration</TabsTrigger>
            </TabsList>
          </Tabs>
        ) : (
          <h3 className={TITLE.card}>Benchmark samples</h3>
        )}
        <span className="text-xs text-muted-foreground">
          {role === "calibration" ? "Calibration · in-sample" : "Held-out final data"}
          {data?.updatedAt && (
            <>
              {" "}
              · Updated <DateTime value={data.updatedAt} />
            </>
          )}
        </span>
      </div>
      <Failure error={data?.samples.length ? (query.error ?? data.error) : undefined} />
      {plan.state === "completed" && (
        <>
          <ComparisonMetrics
            baselineKey={plan.config?.baseline}
            comparisons={(comparisons ?? {}) as Comparisons}
            names={names}
          />
          {role === "final" && !comparisons && plan.results?.raw?.benchmarks && (
            <PairedMetrics benchmarks={plan.results.raw.benchmarks} />
          )}
        </>
      )}
      {data && plan.state !== "completed" && <Execution data={data} plan={plan} />}
      <section aria-label="Benchmark datapoints" className="min-w-0" ref={datapointsRef}>
        <DataTable
          className="min-w-0"
          columns={columns}
          columnVisibility={Object.fromEntries(
            Object.keys(names).map((key) => [
              `prediction:${key}`,
              filters.participant === "all" || filters.participant === key,
            ])
          )}
          data={data ? { count: data.count, results: data.samples } : undefined}
          emptyState={
            data?.state === "pending" ? (
              <LoadingState className="py-8" label="Preparing sample view from retained inputs" />
            ) : (
              <EmptyState icon={Icon.dataset} size="section" title="No samples in this suite" />
            )
          }
          error={query.error ?? (data?.error || undefined)}
          getRowId={(sample) => sample.key}
          hasActiveFilters={hasActiveFilters && data?.state !== "pending"}
          isLoading={query.isPending}
          isRowActive={(sample) => sample.key === selected}
          onClearFilters={clearFilters}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          onRowClick={(sample) => setSelected(sample.key)}
          page={page}
          pageSize={pageSize}
          storageKey={`native-datapoints:${plan.id}:columns`}
          toolbar={
            <>
              <h3 className={TITLE.card}>
                Datapoints{" "}
                <span className="ml-2 text-xs tabular-nums text-muted-foreground">
                  {data?.count.toLocaleString() ?? ""}
                </span>
              </h3>
              <div
                aria-label="Prediction key"
                className="ml-auto flex flex-wrap items-center gap-2"
              >
                <Badge className="gap-1" variant="success">
                  <Icon.success aria-hidden className="size-3" /> Correct
                </Badge>
                <Badge className="gap-1" variant="error">
                  <Icon.failed aria-hidden className="size-3" /> Incorrect
                </Badge>
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span aria-hidden className="h-1 w-5 rounded-sm bg-muted-foreground" />
                  Probability
                </span>
              </div>
              <ListToolbar
                filters={
                  <>
                    <Select
                      onValueChange={(participant) => changeFilters({ participant })}
                      value={filters.participant}
                    >
                      <SelectTrigger aria-label="Filter by model" className="w-56 max-w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All models</SelectItem>
                        {Object.entries(names).map(([key, name]) => (
                          <SelectItem key={key} value={key}>
                            <ModelOptionLabel model={name} name={name} />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select
                      onValueChange={(outcome) =>
                        changeFilters({
                          outcome: outcome as NativeEvaluationsMonitorRetrieveOutcomeEnum,
                        })
                      }
                      value={filters.outcome}
                    >
                      <SelectTrigger aria-label="Filter by outcome" className="w-40 max-w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(outcomeLabels).map(([value, label]) => (
                          <SelectItem key={value} value={value}>
                            {label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </>
                }
                hasActiveFilters={hasActiveFilters}
                onClearFilters={clearFilters}
                search={
                  <SearchInput
                    className="w-64 max-w-full"
                    label="Search datapoints"
                    maxLength={256}
                    onChange={(event) => {
                      setSearch(event.target.value);
                      setSelected(null);
                    }}
                    onClear={() => {
                      setSearch("");
                      changeFilters({ search: "" });
                    }}
                    placeholder="Search datapoints..."
                    value={search}
                  />
                }
              />
            </>
          }
        />
      </section>
      <Dialog
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        open={selected !== null}
      >
        <DialogContent
          aria-describedby={undefined}
          onCloseAutoFocus={(event) => {
            const row =
              datapointsRef.current?.querySelector<HTMLTableRowElement>('tr[tabindex="0"]');
            if (row) {
              event.preventDefault();
              row.focus();
            }
          }}
          size="full"
        >
          {sample && data ? (
            <SampleInspector
              calls={data.calls}
              key={`${role}:${sample.key}`}
              sample={sample}
              stopped={terminal.has(plan.state)}
            />
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Datapoint {selected}</DialogTitle>
              </DialogHeader>
              <DialogBody>
                <Failure error={detail.error} />
                {detail.isPending ? (
                  <LoadingState label="Loading datapoint" />
                ) : (
                  <p>Sample unavailable.</p>
                )}
              </DialogBody>
            </>
          )}
        </DialogContent>
      </Dialog>
    </section>
  );
}
