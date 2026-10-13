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

type WorkshopState = "working" | "complete" | "error" | "idle";

export const workshopStateOf = (dataset: Pick<Dataset, "state" | "running" | "activeVersion">) =>
  dataset.state === "landing" || (dataset.running ?? 0) > 0
    ? "working"
    : dataset.state === "error"
      ? "error"
      : dataset.activeVersion
        ? "complete"
        : "idle";

const STATE_LABEL: Record<WorkshopState, string> = {
  complete: "Ready",
  error: "Failed",
  idle: "Idle",
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
  failed: "Failed",
  queued: "Waiting",
  running: "Running",
};

function RunLine({
  run,
  onShowVersion,
}: {
  run: Execution;
  onShowVersion: (label: string) => void;
}) {
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
          <Badge size="chip" variant={TONE_BADGE_VARIANT[domainStatus(run.state)]}>
            {STATE_TEXT[run.state as keyof typeof STATE_TEXT]}
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-2 pl-18 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {run.runSeconds != null ? `${run.runSeconds.toFixed(1)}s` : "—"}
        </span>
        <span>{formatSmart(run.queuedAt)}</span>
        {run.parent && <span className="font-mono">from {run.parent}</span>}
        {run.cell && (
          <Button
            aria-label={`Show ${run.cell}`}
            className="ml-auto"
            onClick={() => onShowVersion(run.cell!)}
            size="xs"
            variant="secondary"
          >
            {run.cell}
          </Button>
        )}
      </div>
      {failure && <p className="truncate pl-18 text-xs text-destructive">{failure}</p>}
    </li>
  );
}

export function WorkshopStatus({
  dataset,
  onShowVersion,
}: {
  dataset: Dataset;
  onShowVersion: (label: string) => void;
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
