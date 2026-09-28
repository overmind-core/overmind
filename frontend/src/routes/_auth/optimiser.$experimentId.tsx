import { type ReactNode, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";

import apiClient from "@/client";
import { EntityRef } from "@/components/entity-ref";
import { ModelProviderChip } from "@/components/model-provider-chip";
import {
  ExperimentStatusChip,
  experimentScores,
  isExperimentLive,
  OptimizerRunTypeBadge,
  optimizerModelIds,
  optimizerRunType,
  optimizerRunTypeMeta,
  prettyStatus,
} from "@/components/optimiser/experiment-status";
import { deriveWinner } from "@/components/optimiser/experiment-winner";
import { FinetuningSuggestion } from "@/components/optimiser/finetuning-suggestion";
import { DetailErrorState } from "@/components/route-error";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { useCopy } from "@/components/ui/block-actions";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CountChip } from "@/components/ui/count-chip";
import { DateTime } from "@/components/ui/datetime";
import { DeltaChip } from "@/components/ui/delta-chip";
import { Icon } from "@/components/ui/icons";
import { lazyChart } from "@/components/ui/lazy-chart";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { QueryError } from "@/components/ui/query-error";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useElapsedSeconds } from "@/hooks/use-elapsed-seconds";
import { scoreTextClass } from "@/lib/colors";
import { featureFlags } from "@/lib/feature-flags";
import { formatElapsed } from "@/lib/formatters";
import { resolveStatus } from "@/lib/job-status";
import { notify } from "@/lib/notify";
import { projectIdSearchSchema } from "@/lib/schemas";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type {
  ModeCd6Enum,
  OptimizerCandidate,
  OptimizerCommand,
  OptimizerExperiment,
  OptimizerIteration,
} from "@/openapi";

// Lazy so recharts stays out of the initial bundle.
const OptimizerScoreChart = lazyChart(
  () =>
    import("@/components/optimiser/score-chart").then((m) => ({
      default: m.OptimizerScoreChart,
    })),
  { minHeight: 220 }
);

export const Route = createFileRoute("/_auth/optimiser/$experimentId")({
  beforeLoad: () => {
    if (!featureFlags.evaluations) throw redirect({ replace: true, to: "/" });
  },
  component: OptimiserRunPage,
  validateSearch: projectIdSearchSchema,
});

const LIVE_CANDIDATE_STATUSES = new Set([
  "pending",
  "running_commands",
  "commands_done",
  "evaluating",
]);

function iterationChipKey(status: string): string {
  if (status === "completed" || status === "evaluated") return "completed";
  if (status === "failed") return "failed";
  if (status === "pending") return "queued";
  return "running";
}

function ChildStatusChip({ status, chipKey }: { status: string; chipKey: string }) {
  const cfg = resolveStatus(chipKey, "queued");
  return (
    <Badge className="gap-1" size="chip" variant={cfg.variant}>
      {cfg.icon}
      {prettyStatus(status)}
    </Badge>
  );
}

/** Terminal runs measure to `updatedAt` — it is bumped on the terminal transition. */
function RunElapsed({ experiment }: { experiment: OptimizerExperiment }) {
  const live = isExperimentLive(experiment.status);
  const ticking = useElapsedSeconds(live ? experiment.createdAt : undefined);

  if (live) {
    if (ticking == null) return null;
    return (
      <span
        aria-label={`Running for ${formatElapsed(ticking)}`}
        className="tabular-nums text-xs text-muted-foreground"
        role="timer"
      >
        {formatElapsed(ticking)}
      </span>
    );
  }

  const totalSeconds = Math.floor(
    (experiment.updatedAt.getTime() - experiment.createdAt.getTime()) / 1000
  );
  if (totalSeconds <= 0) return null;
  return (
    <span
      aria-label={`Ran for ${formatElapsed(totalSeconds)}`}
      className="tabular-nums text-xs text-muted-foreground"
      role="img"
    >
      {formatElapsed(totalSeconds)}
    </span>
  );
}

function iterationBest(iteration: OptimizerIteration): number | null {
  return (iteration.candidates ?? []).reduce<number | null>((acc, c) => {
    if (c.status !== "evaluated") return acc;
    const s = typeof c.score === "number" && Number.isFinite(c.score) ? c.score : null;
    return s != null && (acc == null || s > acc) ? s : acc;
  }, null);
}

function iterationLabel(iteration: OptimizerIteration): string {
  if (iteration.order === 0) return "Baseline";
  return iteration.name || `Iteration ${iteration.order}`;
}

type CandidateCoverage = {
  errors?: string[];
  excluded_commands?: number;
};

function candidateCoverage(candidate: OptimizerCandidate): CandidateCoverage {
  const scores = candidate.scores as { coverage?: CandidateCoverage } | null | undefined;
  return scores?.coverage ?? {};
}

type ModelComparisonReport = {
  incumbentWins: boolean | null;
  overallWinner: string | null;
  selectedWinner: string | null;
};

/** Candidates are ordered with models cycling fastest. */
function harnessNumber(candidateIndex: number, modelCount: number): number {
  return modelCount > 0 ? Math.floor(candidateIndex / modelCount) + 1 : 1;
}

function highestScore(...scores: (number | null | undefined)[]): number | null {
  const known = scores.filter((s): s is number => typeof s === "number" && Number.isFinite(s));
  return known.length > 0 ? Math.max(...known) : null;
}

function modelComparisonReport(experiment: OptimizerExperiment): ModelComparisonReport {
  const state = experiment.state as Record<string, unknown> | null;
  const report = state?.model_comparison as Record<string, unknown> | undefined;
  return {
    incumbentWins: typeof report?.incumbent_wins === "boolean" ? report.incumbent_wins : null,
    overallWinner: typeof report?.overall_winner === "string" ? report.overall_winner : null,
    selectedWinner: typeof report?.selected_winner === "string" ? report.selected_winner : null,
  };
}

/** `state.model_optimization.overall_winner` — a model slug, or "incumbent". */
function hybridWinnerOf(experiment: OptimizerExperiment): string | null {
  const state = experiment.state as Record<string, unknown> | null;
  const winner = (state?.model_optimization as Record<string, unknown> | undefined)?.overall_winner;
  return typeof winner === "string" ? winner : null;
}

/** A command with neither candidate nor iteration is the smoke test. */
function commandPhase(command: OptimizerCommand): string {
  if (command.candidate == null && command.iteration == null) return "Smoke test";
  return `dp #${command.datapointIndex}`;
}

function HeaderStat({
  label,
  primary = false,
  children,
}: {
  label: string;
  primary?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <div
        className={cn(
          "flex items-center gap-1.5 font-mono tabular-nums leading-none",
          primary ? "text-xl font-semibold" : "text-sm font-medium"
        )}
      >
        {children}
      </div>
    </div>
  );
}

/** Hidden from the rendered diff, but kept in the copy/download text. */
const GIT_META_LINE =
  /^(index |--- |\+\+\+ |old mode |new mode |new file mode |deleted file mode |similarity index |dissimilarity index |rename from |rename to |copy from |copy to |Binary files )/;

function diffDisplayLines(lines: string[]): ({ path: string } | { line: string })[] {
  const out: ({ path: string } | { line: string })[] = [];
  for (const line of lines) {
    const fileHeader = line.match(/^diff --git a\/.* b\/(.*)$/);
    if (fileHeader) out.push({ path: fileHeader[1] });
    else if (!GIT_META_LINE.test(line)) out.push({ line });
  }
  return out;
}

function DiffViewer({ patch, filename }: { patch: string; filename: string }) {
  // git apply expects a trailing newline — normalise once for copy + download.
  const text = patch.endsWith("\n") ? patch : `${patch}\n`;
  const { copied, copy } = useCopy(text);

  const lines = patch.split("\n");
  if (lines.at(-1) === "") lines.pop();
  const added = lines.filter((l) => l.startsWith("+") && !l.startsWith("+++")).length;
  const removed = lines.filter((l) => l.startsWith("-") && !l.startsWith("---")).length;

  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "text/x-patch" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  const actionClass =
    "rounded-sm p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60";

  return (
    <div className="overflow-hidden rounded-md border border-border/60">
      <div className="flex items-center gap-2 border-b border-border/70 bg-wash-raised px-3 py-1.5">
        <Icon.diff className="size-3 shrink-0 text-muted-foreground" />
        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">{filename}</span>
        <span className="font-mono text-xs tabular-nums text-success">+{added}</span>
        <span className="font-mono text-xs tabular-nums text-destructive">−{removed}</span>
        <span className="flex-1" />
        <button
          aria-label="Copy patch to clipboard"
          className={actionClass}
          onClick={copy}
          title={copied ? "Copied!" : "Copy patch"}
          type="button"
        >
          {copied ? (
            <Icon.success className="size-3 text-success" />
          ) : (
            <Icon.clipboard className="size-3" />
          )}
        </button>
        <button
          aria-label={`Download ${filename}`}
          className={actionClass}
          onClick={download}
          title="Download patch"
          type="button"
        >
          <Icon.download className="size-3" />
        </button>
      </div>
      <div className="max-h-96 overflow-auto bg-wash-subtle">
        <pre className="pb-2 font-mono text-xs leading-relaxed">
          {diffDisplayLines(lines).map((item, i) => {
            if ("path" in item) {
              return (
                <span
                  className="mb-1 flex items-center gap-1.5 border-b border-border/60 bg-wash-raised px-3 py-1.5 font-medium text-foreground/90 [&:not(:first-child)]:mt-2 [&:not(:first-child)]:border-t"
                  key={i}
                >
                  <Icon.file className="size-3 shrink-0 text-muted-foreground" />
                  {item.path}
                </span>
              );
            }
            const { line } = item;
            const isAdd = line.startsWith("+");
            const isDel = line.startsWith("-");
            const isHunk = line.startsWith("@@");
            return (
              <span
                className={cn(
                  "block px-3",
                  isAdd && "bg-success/10 text-success",
                  isDel && "bg-destructive/10 text-destructive",
                  isHunk && "text-primary/70",
                  !isAdd && !isDel && !isHunk && "text-foreground/80"
                )}
                key={i}
              >
                {line || "\u00a0"}
              </span>
            );
          })}
        </pre>
      </div>
    </div>
  );
}

function CandidateRow({
  candidate,
  hybrid,
  modelCount,
  isBest,
  failedCommands,
}: {
  candidate: OptimizerCandidate;
  hybrid: boolean;
  modelCount: number;
  isBest: boolean;
  failedCommands: OptimizerCommand[];
}) {
  const { experimentId } = Route.useParams();
  const [showPatch, setShowPatch] = useState(false);
  const { modelName, targetModel } = candidate;
  const harnessIndex = harnessNumber(candidate.candidateIndex, modelCount);
  const label = candidate.isBaseline
    ? "Baseline"
    : hybrid
      ? `Code version ${harnessIndex}`
      : targetModel || `Candidate ${candidate.candidateIndex + 1}`;
  const patchLabel = candidate.isBaseline ? "base" : `c${candidate.candidateIndex + 1}`;
  const running = LIVE_CANDIDATE_STATUSES.has(candidate.status);
  const chipKey =
    candidate.status === "evaluated"
      ? "completed"
      : candidate.status === "failed"
        ? "failed"
        : running
          ? "running"
          : "queued";

  const score = candidate.status === "evaluated" ? candidate.score : null;
  const coverage = candidateCoverage(candidate);
  const excludedOutputs = coverage.excluded_commands ?? failedCommands.length;

  const actions = candidate.evalRun || candidate.codePath;

  return (
    <div className="flex flex-col gap-2 border-b border-border/60 px-4 py-2.5 last:border-b-0">
      <div
        className={cn(
          "grid items-start gap-x-2.5 gap-y-2",
          candidate.codePath ? "grid-cols-[minmax(0,1fr)_auto]" : "grid-cols-1"
        )}
      >
        <div className="flex min-w-0 items-center gap-2.5">
          <span
            className={cn(
              "inline-flex max-w-64 shrink-0 justify-center truncate rounded-sm border px-1.5 py-0.5 font-mono text-xs",
              candidate.isBaseline && "border-warning/40 bg-warning/10 text-warning",
              !candidate.isBaseline && isBest && "border-success/40 bg-success/10 text-success",
              !candidate.isBaseline &&
                !isBest &&
                "border-border/60 bg-wash-raised text-muted-foreground"
            )}
          >
            {label}
          </span>
          <ModelProviderChip
            compact
            model={
              modelName ||
              targetModel ||
              (candidate.isBaseline ? "Incumbent model" : "Capability model")
            }
          />
          <ChildStatusChip chipKey={chipKey} status={candidate.status} />
          {isBest && !candidate.isBaseline && (
            <Badge className="gap-1" size="chip" variant="success">
              <Icon.zap className="size-2.5" />
              Best
            </Badge>
          )}
          {score != null && (
            <span className={cn("font-mono text-xs tabular-nums", scoreTextClass(score))}>
              {score.toFixed(1)}%
            </span>
          )}
          {excludedOutputs > 0 && (
            <span className="text-xs text-destructive">
              {excludedOutputs} output{excludedOutputs === 1 ? "" : "s"} excluded from score
            </span>
          )}
          {actions && <span className="flex-1" />}
          {candidate.evalRun && (
            <div
              className={cn("shrink-0", candidate.codePath && "border-r border-border/70 pr-2.5")}
            >
              <EntityRef id={candidate.evalRun} kind="evalRun" name="Eval run" />
            </div>
          )}
        </div>

        {candidate.codePath && (
          <button
            aria-expanded={showPatch}
            className="inline-flex items-center gap-1 px-2.5 text-xs text-muted-foreground/60 transition-colors hover:text-foreground"
            onClick={() => setShowPatch((v) => !v)}
            type="button"
          >
            <Icon.diff className="size-3 text-muted-foreground" />
            {showPatch ? "Hide patch" : "View patch"}
          </button>
        )}

        {showPatch && candidate.codePath && (
          <DiffViewer filename={`${experimentId}-${patchLabel}.patch`} patch={candidate.codePath} />
        )}
      </div>

      {failedCommands.map((command) => (
        <div
          className="overflow-hidden rounded-md border border-destructive/40 bg-destructive/10"
          key={command.id}
        >
          <div className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium text-destructive">
            <Icon.failed className="size-3.5 shrink-0" />
            {commandPhase(command)} failed
          </div>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap border-t border-border/60 px-3 py-2 font-mono text-xs text-destructive">
            {command.error?.trim() || command.output?.trim() || "(no error output captured)"}
          </pre>
        </div>
      ))}
      {failedCommands.length === 0 && coverage.errors && coverage.errors.length > 0 && (
        <Alert className="flex-col bg-destructive/10 px-3 py-2 text-xs" variant="destructive">
          <p className="font-medium">Excluded output errors</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 font-mono">
            {coverage.errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </Alert>
      )}
      {candidate.status === "failed" && failedCommands.length === 0 && (
        <Alert className="bg-destructive/10 px-3 py-2 text-xs" variant="destructive">
          This candidate failed before producing a score. Its outputs were excluded from ranking.
        </Alert>
      )}
    </div>
  );
}

function IterationRow({
  comparison,
  iteration,
  baseline,
  hybrid,
  modelCount,
  comparedModels,
  showWinnerBadge,
  winnerCandidateId,
  failedCommands,
}: {
  comparison: boolean;
  iteration: OptimizerIteration;
  baseline: number | null;
  hybrid: boolean;
  modelCount: number;
  comparedModels: string[];
  showWinnerBadge: boolean;
  winnerCandidateId: string | null;
  failedCommands: OptimizerCommand[];
}) {
  const [expanded, setExpanded] = useState(false);
  const candidates = [...(iteration.candidates ?? [])].sort(
    (a, b) => a.candidateIndex - b.candidateIndex
  );
  const best = iterationBest(iteration);
  const delta = best != null && baseline != null && iteration.order > 0 ? best - baseline : null;
  const comparisonLabel = candidates
    .map((candidate) => candidate.modelName || candidate.targetModel)
    .filter((model): model is string => Boolean(model))
    .join(", ");
  // Comparison rows name their model even before its candidate exists: order i
  // maps 1:1 onto the i-th selected model.
  const rowLabel = comparison
    ? comparisonLabel || comparedModels[iteration.order - 1] || "Selected models"
    : iterationLabel(iteration);

  return (
    <>
      <TableRow
        className={cn("cursor-pointer", showWinnerBadge && "bg-success/[0.04]")}
        onClick={() => setExpanded((v) => !v)}
      >
        <TableCell className="px-4">
          <span className="flex items-center gap-2">
            <Button
              aria-expanded={expanded}
              aria-label={expanded ? "Hide candidates" : "Show candidates"}
              onClick={(e) => {
                e.stopPropagation();
                setExpanded((v) => !v);
              }}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.chevronDown
                className={cn("size-3.5 transition-transform", !expanded && "-rotate-90")}
              />
            </Button>
            {!comparison && (
              <span className="w-6 shrink-0 font-mono text-xs text-muted-foreground">
                #{iteration.order}
              </span>
            )}
            <span className="min-w-0 truncate text-sm font-medium">{rowLabel}</span>
            {showWinnerBadge && (
              <Badge className="shrink-0 gap-1" size="chip" variant="success">
                <Icon.trophy className="size-2.5" />
                Winner
              </Badge>
            )}
          </span>
        </TableCell>
        <TableCell className="px-4 text-center">
          <ChildStatusChip chipKey={iterationChipKey(iteration.status)} status={iteration.status} />
        </TableCell>
        <TableCell className="px-4 text-center font-mono text-xs tabular-nums text-muted-foreground">
          {candidates.length}
        </TableCell>
        <TableCell className="px-4 text-center font-mono text-xs tabular-nums">
          {best == null ? (
            <span className="text-muted-foreground/40">—</span>
          ) : (
            <span className="inline-flex items-center justify-center gap-1.5 text-foreground">
              <span>{best.toFixed(1)}%</span>
              {delta != null && <DeltaChip value={delta} />}
            </span>
          )}
        </TableCell>
        <TableCell className="px-4 text-center font-mono text-xs tabular-nums text-muted-foreground">
          <DateTime value={iteration.createdAt} />
        </TableCell>
      </TableRow>
      {expanded && (
        <TableRow className="bg-wash-subtle hover:bg-wash-subtle">
          <TableCell className="p-0" colSpan={5}>
            {candidates.length === 0 ? (
              <p className={cn(PROSE, "px-4 py-3 text-sm italic text-muted-foreground")}>
                {comparison
                  ? "Scores appear here once the selected models are evaluated."
                  : "Candidates appear here once the optimiser proposes them."}
              </p>
            ) : (
              <div className="flex flex-col">
                {candidates.map((c) => (
                  <CandidateRow
                    candidate={c}
                    failedCommands={failedCommands.filter((cmd) => cmd.candidate === c.id)}
                    hybrid={hybrid}
                    isBest={winnerCandidateId != null && c.id === winnerCandidateId}
                    key={c.id}
                    modelCount={modelCount}
                  />
                ))}
              </div>
            )}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

const COMMAND_STATUS_TEXT: Record<string, string> = {
  evaluated: "evaluated",
  failed: "failed",
  passed: "passed",
  pending: "queued",
  ran: "ran",
  running: "running",
};

function RunActivity({
  experiment,
  commands,
  onCancel,
  cancelPending,
  commandsError,
  onCommandsRetry,
  runType,
  hybridWinner,
  best,
  delta,
}: {
  experiment: OptimizerExperiment;
  commands: OptimizerCommand[];
  commandsError?: unknown;
  onCommandsRetry?: () => Promise<unknown> | unknown;
  onCancel: () => void;
  cancelPending: boolean;
  runType: ModeCd6Enum;
  hybridWinner: string | null;
  best: number | null;
  delta: number | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const live = isExperimentLive(experiment.status);
  const runTypeLabel = optimizerRunTypeMeta(runType).label;

  const running = commands.filter((c) => c.status === "running").length;
  const pending = commands.filter((c) => c.status === "pending").length;
  const failed = commands.filter((c) => c.status === "failed").length;

  let statusLine: string;
  if (experiment.status === "failed") {
    statusLine = experiment.failureReason || "Experiment failed";
  } else if (experiment.status === "cancelled") {
    statusLine = "Experiment cancelled";
  } else if (experiment.status === "completed") {
    if (runType === "model_comparison") {
      const report = modelComparisonReport(experiment);
      statusLine = report.overallWinner
        ? `${runTypeLabel} completed — ${report.overallWinner === "incumbent" ? "incumbent" : report.overallWinner} won`
        : `${runTypeLabel} completed`;
    } else if (runType === "hybrid") {
      const { baseline } = experimentScores(experiment);
      const winnerScore = hybridWinner === "incumbent" ? baseline : best;
      statusLine = hybridWinner
        ? `${runTypeLabel} completed — ${hybridWinner} won${winnerScore != null ? ` at ${winnerScore.toFixed(1)}` : ""}`
        : `${runTypeLabel} completed`;
    } else {
      statusLine =
        best != null
          ? `${runTypeLabel} completed — best score ${best.toFixed(1)}${delta != null ? ` (${delta >= 0 ? "up" : "down"} ${Math.abs(delta).toFixed(1)} vs baseline)` : ""}`
          : `${runTypeLabel} completed`;
    }
  } else {
    const parts = [prettyStatus(experiment.status)];
    if (running > 0 || pending > 0) {
      parts.push(`${running} command${running === 1 ? "" : "s"} running · ${pending} queued`);
    } else {
      parts.push("waiting for `/overmind optimise`");
    }
    if (failed > 0) parts.push(`${failed} failed`);
    statusLine = parts.join(" — ");
  }

  const feed = [...commands].sort((a, b) => +b.createdAt - +a.createdAt);

  return (
    <Card className="flex flex-col gap-1.5 p-4">
      <div className="flex h-7 items-center gap-2.5">
        <ExperimentStatusChip status={experiment.status} />
        {live && (
          <span aria-hidden className="size-1.5 shrink-0 animate-pulse rounded-xs bg-primary" />
        )}
        <p
          className="min-w-0 flex-1 truncate font-mono text-xs leading-none tabular-nums text-muted-foreground"
          title={statusLine}
        >
          {statusLine}
        </p>
        <div className="flex h-7 shrink-0 items-center gap-2">
          {live && (
            <ConfirmDialog
              cancelLabel="Keep running"
              confirmLabel="Cancel run"
              description="Stops this optimisation run."
              destructive
              isPending={cancelPending}
              onConfirm={onCancel}
              title="Cancel this run?"
              trigger={
                <Button size="sm" variant="secondary">
                  <Icon.close />
                  Cancel
                </Button>
              }
            />
          )}
          {feed.length > 0 && (
            <Button
              aria-expanded={expanded}
              aria-label={expanded ? "Hide command log" : "Show command log"}
              onClick={() => setExpanded((v) => !v)}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.chevronDown
                className={cn("size-3.5 transition-transform", expanded && "rotate-180")}
              />
            </Button>
          )}
        </div>
      </div>
      {expanded && feed.length > 0 && (
        <div
          aria-live="polite"
          className="flex max-h-56 flex-col overflow-y-auto rounded-md bg-wash-subtle px-2.5 py-1.5"
          role="log"
        >
          {feed.map((command) => {
            const detail =
              command.status === "failed"
                ? command.error?.trim() || command.output?.trim() || ""
                : (command.output ?? "").trim();
            return (
              <p
                className="shrink-0 truncate font-mono text-xs leading-5 text-muted-foreground"
                key={command.id}
                title={detail || command.command}
              >
                <span className="text-muted-foreground">
                  {new Date(command.createdAt).toLocaleTimeString()}{" "}
                </span>
                <span className="font-medium">{commandPhase(command)}</span>
                <span
                  className={cn(
                    "ml-1.5",
                    command.status === "failed" && "text-destructive",
                    (command.status === "evaluated" || command.status === "passed") &&
                      "text-success"
                  )}
                >
                  {COMMAND_STATUS_TEXT[command.status] ?? command.status}
                </span>
                {typeof command.score === "number" && command.status === "evaluated" && (
                  <span className="ml-1.5 tabular-nums">{command.score.toFixed(1)}</span>
                )}
                {detail && <span className="ml-1.5 text-muted-foreground/70">{detail}</span>}
              </p>
            );
          })}
        </div>
      )}
      {commandsError != null && (
        <QueryError
          error={commandsError}
          fallback="Couldn't load command activity."
          onRetry={onCommandsRetry}
        />
      )}
    </Card>
  );
}

function OptimiserRunPage() {
  const { experimentId } = Route.useParams();
  const { projectId } = Route.useSearch();
  const queryClient = useQueryClient();

  const experimentQuery = useQuery({
    queryFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsRetrieve({ id: experimentId }),
    queryKey: ["optimizer-experiment", experimentId],
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && isExperimentLive(status) ? 2_500 : false;
    },
    refetchOnWindowFocus: true,
  });
  const experiment = experimentQuery.data;
  const live = experiment ? isExperimentLive(experiment.status) : false;

  const header = (
    <PageHeader
      icon={
        <Icon.optimiserTitle
          aria-hidden
          className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
        />
      }
      title={experiment?.capabilityName ?? "Experiment"}
    />
  );

  const iterationsQuery = useQuery({
    enabled: !!experiment,
    queryFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsIterationsList({
        id: experimentId,
        pageSize: 100,
      }),
    queryKey: ["experiment-iterations", experimentId],
    refetchInterval: live ? 3_000 : false,
    staleTime: live ? 0 : 30_000,
  });

  const commandsQuery = useQuery({
    enabled: !!experiment,
    queryFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsCommandsList({
        id: experimentId,
        pageSize: 100,
      }),
    queryKey: ["experiment-commands", experimentId],
    refetchInterval: live ? 3_000 : false,
    staleTime: live ? 0 : 30_000,
  });

  const cancelMutation = useMutation({
    mutationFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsCancelCreate({ id: experimentId }),
    onError: (e) => notify.error(e, "Couldn't cancel the experiment"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["optimizer-experiment", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment-iterations", experimentId] });
      queryClient.invalidateQueries({ queryKey: ["experiment-commands", experimentId] });
    },
  });

  if (experimentQuery.error && !experiment) {
    return (
      <PageShell header={header}>
        <DetailErrorState error={experimentQuery.error} fallback="Couldn't load this experiment." />
      </PageShell>
    );
  }

  if (experimentQuery.isLoading || !experiment) {
    return (
      <PageShell header={header}>
        <div className="grid gap-4 lg:grid-cols-5">
          <Skeleton className="h-48 lg:col-span-2" />
          <Skeleton className="h-48 lg:col-span-3" />
        </div>
        <Skeleton className="h-64" />
      </PageShell>
    );
  }

  const iterations = [...(iterationsQuery.data?.results ?? [])].sort((a, b) => a.order - b.order);
  const commands = commandsQuery.data?.results ?? [];
  const failedCommands = commands.filter((c) => c.status === "failed");

  const runType = optimizerRunType(experiment);
  const comparison = runType === "model_comparison";
  const hybrid = runType === "hybrid";
  const recordedScores = experimentScores(experiment);
  const baseline = recordedScores.baseline;
  const comparedModels = optimizerModelIds(experiment);
  const comparisonReport = modelComparisonReport(experiment);
  const comparisonWinner =
    comparisonReport.overallWinner ??
    (comparisonReport.incumbentWins === true
      ? "incumbent"
      : (comparisonReport.selectedWinner ?? null));
  const hybridWinner = hybridWinnerOf(experiment);

  const allCandidates = iterations.flatMap((it) => it.candidates ?? []);
  const candidateIterationOrder = new Map(
    iterations.flatMap((it) => (it.candidates ?? []).map((c) => [c.id, it.order]))
  );
  const iterationOrderOf = (candidate: { id: string }): number =>
    candidateIterationOrder.get(candidate.id) ?? -1;
  const highestCandidate = allCandidates
    .filter(
      (candidate) =>
        !candidate.isBaseline &&
        candidate.status === "evaluated" &&
        typeof candidate.score === "number" &&
        Number.isFinite(candidate.score)
    )
    .sort((a, b) => b.score - a.score)[0];
  const best =
    comparison || hybrid
      ? (highestCandidate?.score ?? recordedScores.best)
      : highestScore(recordedScores.best, baseline, highestCandidate?.score);
  const delta = baseline != null && best != null ? best - baseline : recordedScores.delta;
  const improved = delta != null && delta > 0;

  // Tie-breaks mirror the backend: comparison crowns the earliest candidate
  // (``max()``), optimize/hybrid the latest iteration (``-score, -iteration__order``).
  const winner = deriveWinner(iterations, {
    best,
    comparison,
    winnerModel: comparison ? comparisonReport.selectedWinner : null,
  });
  const winnerIterationOrder = winner != null ? iterationOrderOf(winner) : null;
  // An incumbent win crowns no model row — the header's "Overall winner" says so.
  const showWinnerBadge =
    winnerIterationOrder != null && !(comparison && comparisonReport.incumbentWins === true);

  const chartPoints = iterations.map((it) => ({
    best: it.order === 0 ? (iterationBest(it) ?? baseline) : iterationBest(it),
    iteration: it.order,
  }));

  const evaluatedCandidates = allCandidates.filter((c) => c.status === "evaluated").length;
  const winningHarness =
    highestCandidate && comparedModels.length > 0
      ? harnessNumber(highestCandidate.candidateIndex, comparedModels.length)
      : null;

  return (
    <PageShell header={header}>
      {experiment.failureReason && (
        <Alert className="flex-col bg-destructive/10 px-3 py-2" variant="destructive">
          <p className="mb-1 inline-flex items-center gap-1.5 text-xs font-semibold text-destructive">
            <Icon.failed className="size-3" />
            Failure reason
          </p>
          <pre className="max-h-48 w-full overflow-auto whitespace-pre-wrap font-mono text-xs text-destructive/90">
            {experiment.failureReason}
          </pre>
        </Alert>
      )}

      <Card className="flex flex-col gap-4 p-4">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <EntityRef
            id={experiment.capability}
            kind="capability"
            name={experiment.capabilityName}
          />
          {experiment.dataset && (
            <EntityRef
              id={experiment.dataset}
              kind="dataset"
              name={experiment.datasetName}
              projectId={projectId}
            />
          )}
          {experiment.evalSetName && (
            <Badge
              className="inline-flex max-w-56 gap-1 font-mono text-xs font-medium"
              variant="secondary"
            >
              <Icon.listBox className="size-3 shrink-0 text-muted-foreground" />
              <span className="truncate">{experiment.evalSetName}</span>
            </Badge>
          )}
          <OptimizerRunTypeBadge experiment={experiment} showModelCount />
        </div>

        {(comparison || hybrid) && comparedModels.length > 0 && (
          <section className="flex flex-col gap-2 border-t border-border/70 pt-3">
            <h3 className="text-xs text-muted-foreground">Selected models</h3>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {comparedModels.map((modelId) => (
                <ModelProviderChip className="w-full" key={modelId} model={modelId} />
              ))}
            </div>
          </section>
        )}

        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
          <HeaderStat label="Baseline score">
            {baseline != null ? baseline.toFixed(1) : "—"}
          </HeaderStat>
          <HeaderStat
            label={
              comparison ? "Best selected model" : hybrid ? "Best tested combination" : "Best score"
            }
            primary
          >
            <span className="inline-flex items-center gap-1.5">
              <span>{best != null ? best.toFixed(1) : "—"}</span>
              {delta != null && <DeltaChip value={delta} />}
            </span>
          </HeaderStat>
          {hybrid && (
            <>
              <HeaderStat label="Best tested model">
                <ModelProviderChip
                  compact
                  model={highestCandidate?.modelName || highestCandidate?.targetModel || "—"}
                />
              </HeaderStat>
              <HeaderStat label="Best tested code version">
                {winningHarness != null ? `Code version ${winningHarness}` : "—"}
              </HeaderStat>
              <HeaderStat label="Overall winner">
                {hybridWinner === "incumbent"
                  ? `Incumbent${baseline != null ? ` (${baseline.toFixed(1)})` : ""}`
                  : (hybridWinner ?? "—")}
              </HeaderStat>
            </>
          )}
          {comparison ? (
            <>
              <HeaderStat label="Selected-model winner">
                <span className="max-w-64 truncate font-mono">
                  {comparisonReport.selectedWinner ?? "—"}
                </span>
              </HeaderStat>
              <HeaderStat label="Overall winner">
                <span className="max-w-64 truncate font-mono">
                  {comparisonWinner === "incumbent" ? "Incumbent" : (comparisonWinner ?? "—")}
                </span>
              </HeaderStat>
            </>
          ) : (
            <HeaderStat label="Iterations">
              {experiment.currentIteration}/{experiment.numIterations}
            </HeaderStat>
          )}
          <HeaderStat label="Candidates evaluated">
            {evaluatedCandidates}/{allCandidates.length || "—"}
          </HeaderStat>
        </div>
        <div
          aria-label="Iteration progress"
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={
            experiment.numIterations > 0
              ? Math.round((experiment.currentIteration / experiment.numIterations) * 100)
              : 0
          }
          className="h-1.5 overflow-hidden rounded-xs bg-muted"
          role="progressbar"
        >
          <div
            className={cn(
              "h-full rounded-xs transition-all duration-500 motion-reduce:transition-none",
              live ? "bg-primary" : "bg-success"
            )}
            style={{
              width: `${
                experiment.numIterations > 0
                  ? Math.min(100, (experiment.currentIteration / experiment.numIterations) * 100)
                  : 0
              }%`,
            }}
          />
        </div>

        <div className="flex flex-col gap-3 border-t border-border/70 pt-3">
          <div className="flex items-center gap-2">
            <Icon.chart className="size-4 shrink-0 text-muted-foreground" />
            <h3 className="text-xs leading-none">
              {comparison
                ? "Comparison scores"
                : hybrid
                  ? "Code + model scores"
                  : "Score over iterations"}
            </h3>
            <span className="truncate text-xs leading-none text-muted-foreground">
              {comparison
                ? "incumbent and best selected model · 0–100"
                : hybrid
                  ? "best code + model combination · 0–100"
                  : "best candidate per iteration · 0–100"}
            </span>
          </div>
          {chartPoints.some((p) => p.best != null) ? (
            <OptimizerScoreChart baseline={baseline} points={chartPoints} />
          ) : (
            <div className="flex items-center justify-center py-8 text-sm text-muted-foreground">
              No scores yet.
            </div>
          )}
        </div>

        <dl className="grid grid-cols-2 gap-x-8 gap-y-3 border-t border-border/70 pt-3 sm:grid-cols-3 lg:grid-cols-5">
          {!comparison && !hybrid && (
            <>
              <div className="flex flex-col gap-1">
                <dt className="text-xs leading-none text-muted-foreground">
                  Candidates / iteration
                </dt>
                <dd className="font-mono text-xs font-medium leading-none tabular-nums">
                  {experiment.numCandidatesPerIteration}
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs leading-none text-muted-foreground">Patience</dt>
                <dd className="font-mono text-xs font-medium leading-none tabular-nums">
                  {experiment.maxIterationsWithoutImprovement} stalled iterations
                </dd>
              </div>
            </>
          )}
          <div className="flex flex-col gap-1">
            <dt className="text-xs leading-none text-muted-foreground">Time elapsed</dt>
            <dd className="font-mono text-xs font-medium leading-none tabular-nums">
              <RunElapsed experiment={experiment} />
            </dd>
          </div>
          <div className="flex flex-col gap-1">
            <dt className="text-xs leading-none text-muted-foreground">Started</dt>
            <dd className="font-mono text-xs font-medium leading-none tabular-nums">
              <DateTime value={experiment.createdAt} />
            </dd>
          </div>
          {experiment.entrypoint && (
            <div className="flex min-w-0 flex-col gap-1">
              <dt className="text-xs leading-none text-muted-foreground">Entrypoint</dt>
              <dd className="truncate font-mono text-xs font-medium leading-none">
                {experiment.entrypoint}
              </dd>
            </div>
          )}
        </dl>
      </Card>

      <section className="flex flex-col gap-3">
        <h3 className={TITLE.section}>Activity</h3>
        {live && <p className="font-mono text-xs text-muted-foreground">/overmind optimise</p>}
        <RunActivity
          best={best}
          cancelPending={cancelMutation.isPending}
          commands={commands}
          commandsError={commandsQuery.error}
          delta={delta}
          experiment={experiment}
          hybridWinner={hybridWinner}
          onCancel={() => cancelMutation.mutate()}
          onCommandsRetry={commandsQuery.refetch}
          runType={runType}
        />
      </section>

      <section className="flex flex-col gap-3">
        <h3 className={cn(TITLE.section, "inline-flex items-center gap-1.5")}>
          {comparison ? "Model candidates" : hybrid ? "Code + model candidates" : "Iterations"}
          <CountChip count={iterations.length} />
        </h3>
        {iterationsQuery.isError ? (
          <QueryError
            error={iterationsQuery.error}
            fallback="Couldn't load iterations."
            onRetry={iterationsQuery.refetch}
          />
        ) : iterationsQuery.isLoading && iterations.length === 0 ? (
          <LoadingState />
        ) : iterations.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border py-12">
            <Icon.ai className="mb-3 size-10 text-muted-foreground/50" />
            <p className={cn(PROSE, "max-w-sm text-center text-sm text-muted-foreground")}>
              No iterations yet. <code className="font-mono">/overmind optimise</code> in the
              capability repo.
            </p>
          </div>
        ) : (
          <div className="overflow-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="px-4">{comparison ? "Models" : "Iteration"}</TableHead>
                  <TableHead className="w-34 px-4 text-center">Status</TableHead>
                  <TableHead className="w-26 px-4 text-center">
                    {comparison ? "Models tested" : "Candidates"}
                  </TableHead>
                  <TableHead className="w-30 px-4 text-center">Score</TableHead>
                  <TableHead className="w-38 px-4 text-center">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {iterations.map((it) => (
                  <IterationRow
                    baseline={baseline}
                    comparedModels={comparedModels}
                    comparison={comparison}
                    failedCommands={failedCommands}
                    hybrid={hybrid}
                    iteration={it}
                    key={it.id}
                    modelCount={comparedModels.length}
                    showWinnerBadge={showWinnerBadge && it.order === winnerIterationOrder}
                    winnerCandidateId={winner?.id ?? null}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </section>

      <FinetuningSuggestion
        capabilityId={experiment.capability}
        currentImproved={improved}
        projectId={projectId}
      />
    </PageShell>
  );
}
