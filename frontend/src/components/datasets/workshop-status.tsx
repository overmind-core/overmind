import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatSmart } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useDatasetExecutionsQuery } from "@/hooks/use-datasets";
import { domainStatus, TONE_BADGE_VARIANT } from "@/lib/colors";
import { cn } from "@/lib/utils";
import type { Dataset, Execution } from "@/openapi";

type WorkshopState = "working" | "complete" | "error" | "stopped" | "idle";

export const workshopStateOf = (
  dataset: Pick<Dataset, "state" | "running" | "activeVersion">
): WorkshopState =>
  dataset.state === "landing" || (dataset.running ?? 0) > 0
    ? "working"
    : dataset.state === "error" || dataset.state === "stopped"
      ? dataset.state
      : dataset.activeVersion
        ? "complete"
        : "idle";

const STATE_LABEL: Record<WorkshopState, string> = {
  complete: "Ready",
  error: "Failed",
  idle: "Idle",
  stopped: "Import stopped",
  working: "Working",
};

export function WorkshopStatusIcon({
  state,
  className,
}: {
  state: WorkshopState;
  className?: string;
}) {
  const previous = useRef(state);
  const [finished, setFinished] = useState(false);
  useEffect(() => {
    const was = previous.current;
    previous.current = state;
    if (was !== "working" || state !== "complete") return;
    setFinished(true);
    const timer = setTimeout(() => setFinished(false), 700);
    return () => clearTimeout(timer);
  }, [state]);
  const label = STATE_LABEL[state];
  const glyph =
    state === "working" ? (
      <Icon.loader className="workshop-working text-info" />
    ) : state === "error" ? (
      <Icon.warning className="text-destructive" />
    ) : state === "stopped" ? (
      <Icon.stop className="text-muted-foreground" />
    ) : state === "complete" ? (
      <Icon.success className={cn("text-success", finished && "workshop-finished")} />
    ) : (
      <Icon.dataset className="text-muted-foreground" />
    );
  return (
    <span
      aria-label={label}
      className={cn("inline-flex size-3.5 shrink-0 [&>svg]:size-3.5", className)}
      role="img"
    >
      {glyph}
    </span>
  );
}

const MODE_LABEL: Record<Execution["mode"], string> = {
  cell: "Version",
  probe: "Query",
  synthetic: "Rows",
  try: "Preview",
};

const STATE_TEXT: Record<Exclude<Execution["state"], "ok">, string> = {
  cancelled: "Stopped",
  failed: "Failed",
  queued: "Waiting",
  running: "Running",
};

const elapsed = (seconds: number, live: boolean) =>
  seconds < 60
    ? live
      ? `${Math.floor(seconds)}s`
      : `${seconds.toFixed(1)}s`
    : `${Math.floor(seconds / 60)}m ${String(Math.floor(seconds % 60)).padStart(2, "0")}s`;

function useNow(ticking: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!ticking) return;
    setNow(Date.now());
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);
  return now;
}

function RunLine({
  run,
  onShowVersion,
  onCancel,
}: {
  run: Execution;
  onShowVersion: (label: string) => void;
  onCancel?: () => void;
}) {
  const live = run.state === "queued" || run.state === "running";
  const now = useNow(run.state === "running");
  const end = run.finishedAt?.getTime() ?? (run.state === "running" ? now : null);
  const seconds =
    run.runSeconds ??
    (run.startedAt && end != null ? Math.max(0, (end - run.startedAt.getTime()) / 1000) : null);
  // A traceback ends with the exception; its first line only names the frame.
  const failure =
    run.state === "failed"
      ? (run.error
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)
          .at(-1) ?? "")
      : "";
  return (
    <li className="flex flex-col gap-0.5 border-b border-border/70 px-3 py-2 last:border-b-0">
      <div className="flex items-center gap-2 text-xs">
        <span className="pixel-label w-16 shrink-0 text-muted-foreground">
          {MODE_LABEL[run.mode]}
        </span>
        <span
          className="min-w-0 flex-1 truncate font-mono text-foreground"
          title={run.title || run.scriptHead}
        >
          {run.title || run.scriptHead}
        </span>
        {run.state !== "ok" && (
          <Badge
            size="chip"
            variant={
              run.state === "cancelled" ? "neutral" : TONE_BADGE_VARIANT[domainStatus(run.state)]
            }
          >
            {STATE_TEXT[run.state as keyof typeof STATE_TEXT]}
          </Badge>
        )}
        {live && onCancel && (
          <Button
            aria-label={`Stop ${run.title || run.scriptHead}`}
            onClick={onCancel}
            size="xs"
            variant="secondary"
          >
            <Icon.stop />
            Stop
          </Button>
        )}
        {run.cell && (
          <Button
            aria-label={`Show ${run.cell}`}
            className="font-mono"
            onClick={() => onShowVersion(run.cell!)}
            size="xs"
            variant="secondary"
          >
            {run.cell}
          </Button>
        )}
      </div>
      <div className="flex items-center gap-2 pl-18 text-xs text-muted-foreground">
        <span className="tabular-nums">{seconds != null ? elapsed(seconds, live) : "—"}</span>
        <span>{formatSmart(run.queuedAt)}</span>
        {run.parent && <span className="font-mono">from {run.parent}</span>}
        {run.attachment && <span className="truncate">with {run.attachment}</span>}
      </div>
      {failure && <p className="truncate pl-18 text-xs text-destructive">{failure}</p>}
    </li>
  );
}

export function WorkshopStatus({
  dataset,
  onShowVersion,
  onCancelRun,
}: {
  dataset: Dataset;
  onShowVersion: (label: string) => void;
  onCancelRun?: (executionId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const runs = useDatasetExecutionsQuery(dataset.id, open);
  const running = dataset.running ?? 0;
  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button size="xs" variant="secondary">
          {running > 0 ? <Icon.loader className="workshop-working" /> : <Icon.history />}
          {running > 0 ? `Running ${running} ${running === 1 ? "script" : "scripts"}` : "Activity"}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[32rem] p-0">
        <p className="pixel-label border-b border-border/70 px-3 py-2 text-xs text-muted-foreground">
          Activity
        </p>
        {runs.isError ? (
          <p className="px-3 py-3 text-xs text-destructive">Couldn't load activity.</p>
        ) : !runs.data ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">Loading…</p>
        ) : runs.data.length === 0 ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            Nothing has run yet. Scripts your coding agent runs appear here.
          </p>
        ) : (
          <ul aria-label="Activity" className="max-h-96 overflow-y-auto">
            {runs.data.map((run) => (
              <RunLine
                key={run.id}
                onCancel={onCancelRun ? () => onCancelRun(run.id) : undefined}
                onShowVersion={(label) => {
                  setOpen(false);
                  onShowVersion(label);
                }}
                run={run}
              />
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
