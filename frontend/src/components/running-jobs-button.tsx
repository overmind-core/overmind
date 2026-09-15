import { useState } from "react";

import { useNavigate, useSearch } from "@tanstack/react-router";

import { JobNotificationBadge } from "@/components/job-notification-badge";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { navigateToJob, useRunningJobs } from "@/hooks/use-running-jobs";
import { StatusBadge } from "@/lib/job-status";
import { displayJobStatus, type RunningJobItem } from "@/lib/running-jobs";
import { cn } from "@/lib/utils";

function kindLabel(kind: RunningJobItem["kind"]): string {
  if (kind === "finetuning") return "Fine-tuning";
  if (kind === "optimizer") return "Optimiser";
  return "Eval";
}

/** Each glyph must match the title icon of the page the row navigates to. */
function JobKindIcon({ kind }: { kind: RunningJobItem["kind"] }) {
  // Two-tone header glyphs need pixelated + dark:invert (see PageHeader call sites).
  const titleIconClass = "size-4 shrink-0 [image-rendering:pixelated] dark:invert";
  if (kind === "finetuning") {
    return <Icon.finetuning className={titleIconClass} />;
  }
  if (kind === "optimizer") {
    return <Icon.optimiser className={titleIconClass} />;
  }
  return <Icon.evaluations className={titleIconClass} />;
}

function JobRow({
  job,
  onSelect,
  subtle,
}: {
  job: RunningJobItem;
  onSelect: (job: RunningJobItem) => void;
  subtle?: boolean;
}) {
  return (
    <button
      aria-label={`Open ${kindLabel(job.kind)} job ${job.title}`}
      className={cn(
        "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-accent/50 focus-visible:bg-accent/50 focus-visible:outline-none",
        subtle && "opacity-90"
      )}
      onClick={() => onSelect(job)}
      type="button"
    >
      <JobKindIcon kind={job.kind} />
      <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
        {job.title}
      </span>
      <StatusBadge fallback="running" solidProgress status={displayJobStatus(job.status)} />
      <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
        <DateTime value={job.startedAt} />
      </span>
      <Icon.chevronRight className="size-3.5 shrink-0 text-muted-foreground" />
    </button>
  );
}

export function RunningJobsButton() {
  const { projectId } = useSearch({ from: "/_auth" });
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  // Snapshot taken on open, because opening clears the unseen completions.
  const [finishedInPanel, setFinishedInPanel] = useState<RunningJobItem[]>([]);
  const { running, unseenCompletions, clearUnseenCompletions, isLoading } =
    useRunningJobs(projectId);

  const badgeCount =
    unseenCompletions.length > 0
      ? unseenCompletions.length
      : running.length > 0
        ? running.length
        : 0;
  const badgeTone = unseenCompletions.length > 0 ? "success" : "running";
  const label =
    running.length > 0
      ? `${running.length} running job${running.length === 1 ? "" : "s"}`
      : unseenCompletions.length > 0
        ? `${unseenCompletions.length} finished job${unseenCompletions.length === 1 ? "" : "s"}`
        : "Notifications";

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) {
      setFinishedInPanel(unseenCompletions);
      clearUnseenCompletions();
      return;
    }
    setFinishedInPanel([]);
  };

  const handleSelect = (job: RunningJobItem) => {
    setOpen(false);
    if (!projectId) return;
    navigateToJob(navigate, job, projectId);
  };

  return (
    <Popover onOpenChange={handleOpenChange} open={open}>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button aria-label={label} className="relative" size="icon" variant="secondary">
                <Icon.bell />
                <JobNotificationBadge
                  className="absolute -right-0.5 -top-0.5"
                  count={badgeCount}
                  tone={badgeTone}
                />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="bottom" sideOffset={6}>
            {label}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent align="end" className="w-96 overflow-hidden p-0" sideOffset={6}>
        <div className="flex items-center justify-between border-b border-border/70 py-1.5 pl-3 pr-1.5">
          <p className="text-sm font-medium text-foreground">Notifications</p>
        </div>

        {!projectId ? (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">
            Choose a project to see running jobs.
          </p>
        ) : isLoading ? (
          <div className="flex items-center justify-center gap-2 px-3 py-8 text-sm text-muted-foreground">
            <Spinner size="sm" />
            Loading…
          </div>
        ) : running.length === 0 && finishedInPanel.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-3 py-10 text-center">
            <Icon.bell className="size-5 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No notifications</p>
          </div>
        ) : (
          <div className="max-h-[420px] overflow-y-auto">
            {finishedInPanel.length > 0 && (
              <div className="border-b border-border/70">
                <p className="px-3 py-2 text-xs font-semibold text-muted-foreground">
                  Just finished
                </p>
                {finishedInPanel.map((job) => (
                  <JobRow job={job} key={`done-${job.key}`} onSelect={handleSelect} subtle />
                ))}
              </div>
            )}
            {running.length > 0 ? (
              <div>
                {finishedInPanel.length > 0 && (
                  <p className="px-3 py-2 text-xs font-semibold text-muted-foreground">Running</p>
                )}
                {running.map((job) => (
                  <JobRow job={job} key={job.key} onSelect={handleSelect} />
                ))}
              </div>
            ) : null}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
