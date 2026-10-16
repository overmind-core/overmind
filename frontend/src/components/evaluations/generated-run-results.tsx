import { useMemo, useRef, useState } from "react";

import { useQueries } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { DatapointInspector } from "@/components/evaluations/datapoint-inspector";
import { MetricBars } from "@/components/evaluations/metric-bars";
import { MetricList } from "@/components/evaluations/metric-list";
import { formatPayload, renderPayload } from "@/components/evaluations/payload-format";
import { asRunSummary, RunComparison, ScoreChip } from "@/components/evaluations/run-comparison";
import { ModelOptionLabel } from "@/components/model-option-label";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { fetchEvalRunRows, isActiveRunStatus } from "@/hooks/use-evaluations";
import { TITLE } from "@/lib/typography";
import type { EvalRun, EvalRunRow } from "@/openapi";

export interface RunDatapoint {
  key: string;
  label: string;
  samples: Record<string, EvalRunRow>;
}

export function alignRunDatapoints(groups: { run: EvalRun; rows: EvalRunRow[] }[]) {
  const points = new Map<string, RunDatapoint>();
  for (const { run, rows } of groups) {
    for (const row of rows) {
      // Migrated rows can have a synthetic display index; only the retained source index aligns.
      const legacy = row.metadata?.legacy_sample;
      const index = legacy && Object.hasOwn(legacy, "row_index") ? legacy.row_index : row.rowIndex;
      const identity =
        index != null && run.cell
          ? `cell:${run.cell}:row:${index}`
          : row.sourceTraceId
            ? `trace:${row.sourceTraceId}`
            : `sample:${row.id}`;
      // A conflicting input/reference or duplicate in one run must remain a separate datapoint.
      let key = JSON.stringify([identity, row.input, row.reference]);
      if (points.get(key)?.samples[run.id]) key += `:${row.id}`;
      const point = points.get(key) ?? {
        key,
        label: String(index ?? (row.sourceTraceId || row.id)),
        samples: {},
      };
      point.samples[run.id] = row;
      points.set(key, point);
    }
  }
  return [...points.values()];
}

const label = (run: EvalRun) => run.label || run.modelName || run.name;
const preview = (value: unknown) =>
  value == null ? "Not captured" : formatPayload(value) || JSON.stringify(value);

export function matchesDatapoint(
  point: RunDatapoint,
  search: string,
  participant: string,
  outcome: string
) {
  const samples =
    participant === "all"
      ? Object.values(point.samples)
      : [point.samples[participant]].filter(Boolean);
  if (!samples.length) return false;
  const matching = samples.filter((sample) => {
    if (outcome === "errored") return !!sample.error || sample.degraded;
    if (outcome === "failing")
      return !sample.error && !sample.degraded && sample.score != null && sample.score < 0.5;
    if (outcome === "unscored") return sample.score == null && !sample.error && !sample.degraded;
    return true;
  });
  const query = search.trim().toLowerCase();
  return matching.some(
    (sample) =>
      !query ||
      [
        point.label,
        sample.input,
        sample.output,
        sample.reference,
        sample.error,
        sample.metrics,
      ].some((value) => JSON.stringify(value)?.toLowerCase().includes(query))
  );
}

function Payload({ title, value }: { title: string; value: unknown }) {
  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h3 className="text-xs text-muted-foreground">{title}</h3>
      <pre className="whitespace-pre-wrap break-words text-sm">
        {value == null
          ? "Not captured"
          : renderPayload(typeof value === "string" ? value : JSON.stringify(value), "formatted")}
      </pre>
    </section>
  );
}

export function GeneratedRunResults({ runs }: { runs: EvalRun[] }) {
  const datapointsRef = useRef<HTMLElement>(null);
  const queries = useQueries({
    queries: runs.map((run) => ({
      queryFn: () => fetchEvalRunRows(run.id),
      queryKey: ["eval-run-rows", run.id],
      refetchInterval: isActiveRunStatus(run.status) ? 4000 : (false as const),
    })),
  });
  const points = alignRunDatapoints(
    runs.map((run, i) => ({ rows: queries[i].data?.results ?? [], run }))
  );
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [participant, setParticipant] = useState("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const visible = points.filter((point) => matchesDatapoint(point, search, participant, filter));
  const hasActiveFilters = !!search || participant !== "all" || filter !== "all";
  const clearFilters = () => {
    setSearch("");
    setParticipant("all");
    setFilter("all");
    setPage(1);
  };
  const currentPage = Math.min(page, Math.max(1, Math.ceil(visible.length / pageSize)));
  const point = points.find((item) => item.key === selected);
  const metrics = [
    ...new Set(runs.flatMap((run) => Object.keys(asRunSummary(run.summary).metrics ?? {}))),
  ].sort();
  const columns = useMemo<ColumnDef<RunDatapoint>[]>(
    () => [
      { accessorKey: "label", header: "Datapoint", size: 90 },
      {
        cell: ({ row }) => (
          <span className="block truncate text-xs">
            {preview(Object.values(row.original.samples)[0]?.input)}
          </span>
        ),
        header: "Input",
        id: "input",
        size: 240,
      },
      ...runs.map((run) => ({
        cell: ({ row }: { row: { original: RunDatapoint } }) => {
          const result = row.original.samples[run.id];
          if (!result) return <span className="text-xs text-muted-foreground">No result</span>;
          return (
            <button
              aria-label={`Open ${label(run)} output for datapoint ${row.original.label}`}
              className="flex w-full min-w-0 items-center gap-2 rounded-sm text-left text-xs hover:bg-wash-subtle focus-visible:outline focus-visible:outline-ring"
              onClick={(event) => {
                event.stopPropagation();
                setSelected(row.original.key);
              }}
              type="button"
            >
              <span className="min-w-0 flex-1 truncate">
                {result.error || preview(result.output)}
              </span>
              <ScoreChip value={result.score} />
            </button>
          );
        },
        header: label(run),
        id: run.id,
        size: 220,
      })),
    ],
    [runs]
  );
  return (
    <>
      {runs
        .filter((run) => run.error)
        .map((run) => (
          <p className="text-sm text-destructive" key={run.id}>
            {label(run)}: {run.error}
          </p>
        ))}
      <section
        aria-label="Run comparison"
        className={metrics.length > 1 ? "grid gap-4 xl:grid-cols-2" : "grid gap-4"}
      >
        {metrics.map((metric) => (
          <MetricBars
            benchmark="All runs"
            description="Mean score by run · Higher is better."
            emptyHint="No scored results yet."
            key={metric}
            percentage
            rows={runs.flatMap((run, colorIndex) => {
              const value = asRunSummary(run.summary).metrics?.[metric]?.mean;
              return value == null ? [] : [{ colorIndex, key: run.id, name: label(run), value }];
            })}
            title={metric}
          />
        ))}
      </section>
      {metrics.length > 0 && (
        <details className="rounded-md border border-border p-4">
          <summary className="cursor-pointer text-sm">Evaluator details</summary>
          <div className="overflow-auto pt-3">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Model</TableHead>
                  <TableHead>Evaluator</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Score</TableHead>
                  <TableHead>Scored</TableHead>
                  <TableHead>Failed</TableHead>
                  <TableHead>Errors</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.flatMap((run) => {
                  const summary = asRunSummary(run.summary);
                  return Object.entries(summary.metrics ?? {}).map(([name, metric]) => (
                    <TableRow key={`${run.id}:${name}`}>
                      <TableCell>{label(run)}</TableCell>
                      <TableCell>{name}</TableCell>
                      <TableCell>
                        {metric.type === "llm"
                          ? "Model judge"
                          : metric.type === "deterministic"
                            ? "Code check"
                            : "Historical"}
                      </TableCell>
                      <TableCell>
                        <ScoreChip value={metric.mean} />
                      </TableCell>
                      <TableCell>{metric.n}</TableCell>
                      <TableCell>{metric.failed ?? (summary.migrated ? "—" : 0)}</TableCell>
                      <TableCell>{metric.errors ?? (summary.migrated ? "—" : 0)}</TableCell>
                    </TableRow>
                  ));
                })}
              </TableBody>
            </Table>
          </div>
        </details>
      )}
      {runs.some((run) => run.isBaseline) &&
        runs
          .filter((run) => !run.isBaseline)
          .map((run) => (
            <details className="rounded-md border border-border p-4" key={run.id}>
              <summary className="cursor-pointer text-sm">
                {label(run)} · Baseline comparison
              </summary>
              <div className="pt-4">
                <RunComparison active={isActiveRunStatus(run.status)} hasBaseline runId={run.id} />
              </div>
            </details>
          ))}
      <section aria-label="Datapoints" className="flex min-h-96 flex-col gap-3" ref={datapointsRef}>
        <DataTable
          columns={columns}
          columnVisibility={Object.fromEntries(
            runs.map((run) => [run.id, participant === "all" || participant === run.id])
          )}
          data={{
            count: visible.length,
            results: visible.slice((currentPage - 1) * pageSize, currentPage * pageSize),
          }}
          emptyState={
            <EmptyState
              icon={Icon.dataset}
              size="section"
              title={hasActiveFilters ? "No matching datapoints" : "No datapoints captured"}
            />
          }
          error={queries.find((query) => query.error)?.error}
          getRowId={(row) => row.key}
          hasActiveFilters={hasActiveFilters}
          isLoading={queries.some((query) => query.isPending)}
          isRowActive={(row) => row.key === selected}
          onClearFilters={clearFilters}
          onPageChange={setPage}
          onPageSizeChange={(size) => {
            setPageSize(size);
            setPage(1);
          }}
          onRowClick={(row) => setSelected(row.key)}
          page={currentPage}
          pageSize={pageSize}
          storageKey="evaluation-group-datapoints"
          toolbar={
            <>
              <h3 className={TITLE.card}>
                Datapoints{" "}
                <span className="ml-2 text-xs tabular-nums text-muted-foreground">
                  {visible.length.toLocaleString()}
                </span>
              </h3>
              <ListToolbar
                filters={
                  <>
                    <Select
                      onValueChange={(value) => {
                        setParticipant(value);
                        setPage(1);
                      }}
                      value={participant}
                    >
                      <SelectTrigger aria-label="Filter by model" className="w-56 max-w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All models</SelectItem>
                        {runs.map((run) => (
                          <SelectItem key={run.id} value={run.id}>
                            <ModelOptionLabel
                              model={run.resolvedModel || run.modelName || label(run)}
                              name={label(run)}
                            />
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Select
                      onValueChange={(value) => {
                        setFilter(value);
                        setPage(1);
                      }}
                      value={filter}
                    >
                      <SelectTrigger aria-label="Filter by outcome" className="w-40 max-w-full">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="all">All outcomes</SelectItem>
                        <SelectItem value="failing">Failing</SelectItem>
                        <SelectItem value="errored">Errored or degraded</SelectItem>
                        <SelectItem value="unscored">Not scored</SelectItem>
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
                    onChange={(event) => {
                      setSearch(event.target.value);
                      setPage(1);
                    }}
                    onClear={() => {
                      setSearch("");
                      setPage(1);
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
            const row = datapointsRef.current?.querySelector<HTMLTableRowElement>(
              'tr[data-state="selected"], tr[tabindex="0"]'
            );
            if (row) {
              event.preventDefault();
              row.focus();
            }
          }}
          size="full"
        >
          <DialogHeader>
            <DialogTitle>Datapoint {point?.label}</DialogTitle>
          </DialogHeader>
          <DatapointInspector
            input={
              <div className="px-5 py-4">
                <Payload
                  title="Dataset input"
                  value={point ? Object.values(point.samples)[0]?.input : null}
                />
                {point && Object.values(point.samples)[0]?.metadata?.legacy_sample && (
                  <p className="mt-3 text-xs text-muted-foreground">
                    Historical dataset input; the exact model request was not captured.
                  </p>
                )}
              </div>
            }
          >
            {runs.map((run) => {
              const sample = point?.samples[run.id];
              return (
                <section className="flex flex-col gap-4 px-5 py-4" key={run.id}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <ModelProviderChip
                      model={run.resolvedModel || run.modelName || label(run)}
                      showModelId={false}
                    >
                      <span className="min-w-0 truncate">{label(run)}</span>
                    </ModelProviderChip>
                    {sample && <ScoreChip value={sample.score} />}
                  </div>
                  {sample ? (
                    <>
                      {sample.error && <p className="text-sm text-destructive">{sample.error}</p>}
                      {sample.degraded && (
                        <p className="text-sm text-warning">
                          {sample.degradedReason || "Degraded result"}
                        </p>
                      )}
                      <Payload title="Output" value={sample.output} />
                      <Payload title="Grading reference" value={sample.reference} />
                      <MetricList metrics={sample.metrics} />
                      {sample.sourceTraceId && (
                        <EntityRef
                          id={sample.sourceTraceId}
                          kind="trace"
                          name={sample.sourceTraceId.slice(0, 12)}
                          projectId={run.project}
                        />
                      )}
                      {sample.finishedAt && (
                        <span className="text-xs text-muted-foreground">
                          <DateTime value={sample.finishedAt} />
                        </span>
                      )}
                    </>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No result recorded for this model on this datapoint.
                    </p>
                  )}
                </section>
              );
            })}
          </DatapointInspector>
        </DialogContent>
      </Dialog>
    </>
  );
}
