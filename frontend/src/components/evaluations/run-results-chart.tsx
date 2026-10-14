import { useMemo } from "react";

import { useQueries } from "@tanstack/react-query";

import apiClient from "@/client";
import { metricResults } from "@/components/evaluations/metric-list";
import {
  type DistributionScore,
  ScoreDistributions,
} from "@/components/evaluations/score-distributions";
import { backoffPolling } from "@/lib/poll";
import type { EvalRun, EvalRunDetail, EvalRunRow } from "@/openapi";

const targetLabel = (run: Pick<EvalRun, "label" | "mode">) => run.label || run.mode;

function matrixNames(matrix: EvalRunDetail["matrix"]): string[] {
  if (!Array.isArray(matrix)) return [];
  return matrix.flatMap((row) =>
    row && typeof row === "object" && "name" in row && typeof row.name === "string"
      ? [row.name]
      : []
  );
}

export function scoresFromRows(rows: EvalRunRow[], variant: string): DistributionScore[] {
  return rows.flatMap((row) =>
    metricResults(row.metrics).flatMap(([name, metric]) =>
      typeof metric.score === "number" ? [{ name, value: metric.score, variant }] : []
    )
  );
}

export function replayMisses(rows: EvalRunRow[]): number {
  return rows.reduce((total, row) => {
    const metadata = row.metadata;
    const misses =
      metadata && typeof metadata === "object" && "replay_misses" in metadata
        ? metadata.replay_misses
        : 0;
    return total + (typeof misses === "number" ? misses : 0);
  }, 0);
}

/** One lane per test, or per model and test when the group replayed more than one target. */
export function ResultsChart({
  run,
  runs,
  active,
}: {
  run: EvalRunDetail;
  runs: EvalRun[];
  active: boolean;
}) {
  const targets = runs.length > 0 ? runs : [run];
  const queries = useQueries({
    queries: targets.map((target) => ({
      queryFn: () =>
        apiClient.evalRunRows.evalRunRowsList({
          ordering: "row_index",
          pageSize: 1000,
          run: target.id,
        }),
      queryKey: ["eval-run-rows", target.id],
      refetchInterval: (query: { state: { fetchFailureCount: number } }) =>
        backoffPolling(query, active ? 4_000 : false),
    })),
  });

  const scores = useMemo(
    () =>
      queries.flatMap((query, index) =>
        scoresFromRows(query.data?.results ?? [], targets[index].id)
      ),
    [queries, targets]
  );
  const metrics = useMemo(() => {
    const ordered = matrixNames(run.matrix);
    const seen = new Set(ordered);
    const extra = [...new Set(scores.flatMap((score) => (score.name ? [score.name] : [])))].filter(
      (name) => !seen.has(name)
    );
    extra.sort();
    return [...ordered, ...extra];
  }, [run.matrix, scores]);
  const misses = queries.reduce(
    (total, query) => total + replayMisses(query.data?.results ?? []),
    0
  );

  if (queries.some((query) => query.isLoading) && scores.length === 0) return null;
  if (scores.length === 0) return null;

  return (
    <section aria-label="Results" className="flex flex-col gap-3">
      <div>
        <h3 className="text-sm font-medium">Results</h3>
        <p className="text-xs text-muted-foreground">Scores by model, evaluator and sample</p>
      </div>
      {misses > 0 && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
          {misses} recorded tool result{misses === 1 ? "" : "s"} unmatched in replay
        </p>
      )}
      <ScoreDistributions
        metrics={metrics}
        scores={scores}
        variants={targets.map((target, index) => ({
          id: target.id,
          label: targetLabel(target),
          order: index,
        }))}
      />
    </section>
  );
}
