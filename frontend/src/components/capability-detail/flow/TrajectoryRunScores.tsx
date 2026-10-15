import { createContext, useContext, useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import apiClient from "@/client";
import { ScoreChip } from "@/components/evaluations/run-comparison";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useTraceDetail } from "@/hooks/use-traces";
import { cn } from "@/lib/utils";
import type { EvalScore, TraceDetail } from "@/openapi";
import { TrajectoryRunPicker } from "./TrajectoryRunPicker";
import { summarizeTrajectoryScores, type TrajectoryScoreSummary } from "./trajectoryScoreSummary";

const ScoreContext = createContext<{
  scores: Map<string, TrajectoryScoreSummary>;
  status: "idle" | "loading" | "ready" | "error";
}>({ scores: new Map(), status: "idle" });

const RunContext = createContext<{
  trace: TraceDetail | undefined;
  traceId: string | undefined;
  loading: boolean;
  error: boolean;
  highlighted: boolean;
}>({ error: false, highlighted: false, loading: false, trace: undefined, traceId: undefined });

export const useTrajectoryRun = () => useContext(RunContext);

export async function fetchTrajectoryScores(project: string, traceId: string) {
  const scores: EvalScore[] = [];
  for (let page = 1; ; page += 1) {
    const data = await apiClient.evalScores.evalScoresList({
      ordering: "-created_at",
      page,
      pageSize: 100,
      project,
      sourceType: "span",
      traceId,
    });
    scores.push(...data.results);
    if (!data.next) return scores;
  }
}

export function TrajectoryScoresProvider({
  project,
  capability,
  children,
}: {
  project: string;
  capability: string;
  children: (control: React.ReactNode, highlighted: boolean) => React.ReactNode;
}) {
  const [selected, setSelected] = useState<string>();
  const [highlighted, setHighlighted] = useState(false);
  const selectRun = (id: string) => {
    setSelected(id);
    setHighlighted(id === selected ? !highlighted : true);
  };
  const traceId = selected;
  const trace = useTraceDetail(traceId, project, true);
  const results = useQuery({
    enabled: !!traceId,
    queryFn: () => fetchTrajectoryScores(project, traceId!),
    queryKey: ["trajectory-scores", project, traceId],
    refetchInterval: 10000,
  });
  const scores = useMemo(() => summarizeTrajectoryScores(results.data ?? []), [results.data]);
  const status = results.isError
    ? "error"
    : traceId && results.isLoading
      ? "loading"
      : traceId
        ? "ready"
        : "idle";
  const control = (
    <div className="flex items-center gap-2">
      <TrajectoryRunPicker
        capability={capability}
        highlighted={highlighted}
        onSelect={selectRun}
        project={project}
        selected={selected}
      />
      {traceId && (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-label="Highlight selected run"
              aria-pressed={highlighted}
              className={cn(highlighted && "bg-info/10 text-info")}
              onClick={() => setHighlighted((value) => !value)}
              size="icon"
              variant="ghost"
            >
              <Icon.gitBranch />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {highlighted ? "Show full trajectory" : "Highlight selected run"}
          </TooltipContent>
        </Tooltip>
      )}
      <Tooltip>
        <TooltipTrigger asChild>
          <Button asChild size="icon" variant="ghost">
            <Link
              aria-label={traceId ? "Open selected run in observability" : "View capability traces"}
              {...(traceId
                ? { params: { traceId }, to: "/observability/$traceId" as const }
                : { to: "/observability" as const })}
              search={{ capability, projectId: project }}
            >
              <Icon.observability />
            </Link>
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {traceId ? "Open selected run in observability" : "View capability traces"}
        </TooltipContent>
      </Tooltip>
      {status === "error" && (
        <span className="text-xs text-destructive" role="status">
          Scores unavailable
        </span>
      )}
    </div>
  );
  return (
    <RunContext.Provider
      value={{
        error: trace.isError,
        highlighted,
        loading: !!traceId && (trace.isLoading || trace.isPlaceholderData),
        trace: trace.data,
        traceId,
      }}
    >
      <ScoreContext.Provider value={{ scores, status }}>
        {children(control, highlighted)}
      </ScoreContext.Provider>
    </RunContext.Provider>
  );
}

export function TrajectoryMetricScore({ name }: { name: string }) {
  const { scores, status } = useContext(ScoreContext);
  const value = scores.get(name);
  if (status === "idle") return null;
  if (status !== "ready" || !value)
    return (
      <span className="text-xs text-muted-foreground">
        {status === "error" ? "Unavailable" : status === "loading" ? "Loading" : "Not scored"}
      </span>
    );
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="flex shrink-0 items-center gap-1">
          {value.mean !== null && <ScoreChip value={value.mean} />}
          {!!value.errors && (
            <Badge size="chip" variant="error">
              {value.errors} error{value.errors === 1 ? "" : "s"}
            </Badge>
          )}
          {!!value.abstained && (
            <Badge size="chip" variant="neutral">
              {value.abstained} abstained
            </Badge>
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        Mean across {value.scored} scored invocation{value.scored === 1 ? "" : "s"} in this run.{" "}
        {value.errors} judge errors; {value.abstained} abstentions.
      </TooltipContent>
    </Tooltip>
  );
}

export function TrajectoryScoredCount({ names }: { names: string[] }) {
  const { scores, status } = useContext(ScoreContext);
  if (status !== "ready") return null;
  const measured = names.filter((name) => (scores.get(name)?.scored ?? 0) > 0).length;
  const values = names.flatMap((name) => {
    const score = scores.get(name);
    return score ? [score] : [];
  });
  const samples = values.reduce((sum, value) => sum + value.scored, 0);
  const errors = values.reduce((sum, value) => sum + value.errors, 0);
  const mean = samples
    ? values.reduce((sum, value) => sum + (value.mean ?? 0) * value.scored, 0) / samples
    : null;
  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>
        {measured}/{names.length} scored
      </span>
      {mean !== null && <ScoreChip value={mean} />}
      {!!errors && (
        <Badge size="chip" variant="error">
          {errors} error{errors === 1 ? "" : "s"}
        </Badge>
      )}
    </span>
  );
}
