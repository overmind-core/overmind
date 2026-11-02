import { Link } from "@tanstack/react-router";

import { pct, RLStatusBadge } from "@/components/rl/format";
import type { StageMetrics } from "@/components/rl/types";
import { DateTime } from "@/components/ui/datetime";
import { DeltaChip, fromScore } from "@/components/ui/delta-chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { QueryError } from "@/components/ui/query-error";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useRLRunsQuery } from "@/hooks/use-rl";

export function RLRunsTable({ projectId, action }: { projectId: string; action: React.ReactNode }) {
  const { data, error, isLoading, refetch } = useRLRunsQuery(projectId);
  if (error && !data)
    return <QueryError error={error} fallback="RL runs could not load" onRetry={refetch} />;
  if (isLoading) return <LoadingState label="Loading RL runs" />;
  const runs = data?.results ?? [];
  if (runs.length === 0) {
    return (
      <EmptyState
        action={action}
        description="Train a small model on a dataset of prompts, rewarded by the checks that already score your traces."
        icon={Icon.target}
        title="No RL runs yet"
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex justify-end">{action}</div>
      <div className="overflow-hidden rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Run</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Base model</TableHead>
              <TableHead className="text-right">Held-out pass rate</TableHead>
              <TableHead className="text-right">Steps</TableHead>
              <TableHead className="text-right">Started</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.map((run) => {
              const before = (run.baseline as StageMetrics | null)?.pass_rate;
              const after = (run._final as StageMetrics | null)?.pass_rate;
              return (
                <TableRow className="cursor-pointer" key={run.id}>
                  <TableCell>
                    <Link
                      className="flex flex-col"
                      params={{ runId: run.id }}
                      search={{ projectId }}
                      to="/rl-training/$runId"
                    >
                      <span className="font-medium">{run.name}</span>
                      <span className="text-xs text-muted-foreground">{run.datasetName}</span>
                    </Link>
                  </TableCell>
                  <TableCell>
                    <RLStatusBadge status={run.status} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{run.baseModel}</TableCell>
                  <TableCell className="text-right">
                    <span className="inline-flex items-center gap-2 font-mono text-sm tabular-nums">
                      {pct(before)}
                      {after != null ? (
                        <>
                          <Icon.forward className="size-3.5 text-muted-foreground" />
                          {pct(after)}
                          {before != null ? <DeltaChip value={fromScore(after - before)} /> : null}
                        </>
                      ) : null}
                    </span>
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs tabular-nums">
                    {run.stepsCompleted}/{Number(run.hyperparameters?.max_steps ?? 0)}
                  </TableCell>
                  <TableCell className="text-right text-xs text-muted-foreground">
                    <DateTime value={run.createdAt} />
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
