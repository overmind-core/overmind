import { useState } from "react";

import { useNavigate, useSearch } from "@tanstack/react-router";

import { ActivityFeedRow } from "@/components/activity-feed";
import { ActivityAssetChip, ActivityTaskChip } from "@/components/activity-links";
import { JobNotificationBadge } from "@/components/job-notification-badge";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { LoadingState } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useActivity, useMarkActivitySeen } from "@/hooks/use-activity";
import { navigateToJob, useRunningJobs } from "@/hooks/use-running-jobs";
import { displayJobStatus, type RunningJobItem } from "@/lib/running-jobs";
import { cn } from "@/lib/utils";

function kindLabel(kind: RunningJobItem["kind"]): string {
  if (kind === "finetuning") return "Fine-tuning";
  if (kind === "optimizer") return "Optimiser";
  return "Eval";
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
    <div
      className={cn(
        "flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-accent/50 focus-visible:bg-accent/50 focus-visible:outline-none",
        subtle && "opacity-90"
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="text-sm leading-8 text-muted-foreground">
          <button
            aria-label={`Open ${kindLabel(job.kind)} job ${job.title}`}
            className="font-medium text-foreground hover:underline"
            onClick={() => onSelect(job)}
            type="button"
          >
            {job.title}
          </button>
          : <JobTaskLink job={job} /> is {displayJobStatus(job.status).replaceAll("_", " ")}.
        </div>
        <span className="text-xs text-muted-foreground">
          <DateTime value={job.startedAt} />
        </span>
      </div>
    </div>
  );
}

function JobTaskLink({ job }: { job: RunningJobItem }) {
  const { projectId } = useSearch({ from: "/_auth" });
  if (!projectId) return null;
  return (
    <span>
      <ActivityTaskChip
        id={job.id}
        kind={
          job.kind === "finetuning"
            ? "finetune_job"
            : job.kind === "eval"
              ? "eval_run"
              : "optimizer_experiment"
        }
        projectId={projectId}
      />
      {job.datasetId && (
        <>
          {" "}
          for{" "}
          <ActivityAssetChip
            asset={{ id: job.datasetId, kind: "dataset", label: job.datasetName || "Dataset" }}
            projectId={projectId}
          />
        </>
      )}
      {job.capabilityId && (
        <>
          {" "}
          in{" "}
          <ActivityAssetChip
            asset={{
              id: job.capabilityId,
              kind: "capability",
              label: job.capabilityName || "Capability",
            }}
            projectId={projectId}
          />
        </>
      )}
    </span>
  );
}

export function RunningJobsButton() {
  const { projectId } = useSearch({ from: "/_auth" });
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const activity = useActivity(projectId);
  const markSeen = useMarkActivitySeen();
  const activityRows = [
    ...new Map(
      (activity.data?.pages.flatMap((page) => page.results) ?? []).map((event) => [event.id, event])
    ).values(),
  ];
  const unread = activity.data?.pages[0]?.unseenCount ?? 0;
  // Snapshot taken on open, because opening clears the unseen completions.
  const [finishedInPanel, setFinishedInPanel] = useState<RunningJobItem[]>([]);
  const {
    running: runningJobs,
    unseenCompletions: completedJobs,
    clearUnseenCompletions,
    isLoading,
  } = useRunningJobs(projectId);
  const recordedTasks = new Set(activityRows.map((event) => event.taskId));
  const running = runningJobs.filter((job) => !recordedTasks.has(job.id));
  const unseenCompletions = completedJobs.filter((job) => !recordedTasks.has(job.id));

  const badgeCount =
    unread +
    (unseenCompletions.length > 0
      ? unseenCompletions.length
      : running.length > 0
        ? running.length
        : 0);
  const badgeTone = unread > 0 || unseenCompletions.length > 0 ? "success" : "running";
  const label =
    running.length > 0
      ? `${running.length} running job${running.length === 1 ? "" : "s"}`
      : unseenCompletions.length > 0
        ? `${unseenCompletions.length} finished job${unseenCompletions.length === 1 ? "" : "s"}`
        : unread > 0
          ? `${unread} unread notification${unread === 1 ? "" : "s"}`
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
      <PopoverContent
        align="end"
        className="w-[44rem] max-w-[calc(100vw-2rem)] overflow-hidden p-0"
        onClick={(event) => {
          if (event.target instanceof Element && event.target.closest("a")) setOpen(false);
        }}
        sideOffset={6}
      >
        <div className="flex items-center justify-between border-b border-border/70 py-1.5 pl-3 pr-1.5">
          <p className="text-sm font-medium text-foreground">Notifications</p>
          <Button
            disabled={markSeen.isPending || unread === 0}
            onClick={() => {
              for (const page of activity.data?.pages ?? [])
                markSeen.mutate({
                  ids: page.results.filter((row) => !row.seen).map((row) => row.id),
                  project: projectId,
                  through: page.observedAt,
                });
            }}
            size="xs"
            variant="secondary"
          >
            Mark read
          </Button>
        </div>
        {activity.error && <Alert variant="destructive">Couldn’t load activity.</Alert>}
        {markSeen.error && <Alert variant="destructive">Couldn’t mark activity read.</Alert>}

        {!projectId && activityRows.length === 0 ? (
          <p className="px-3 py-8 text-center text-sm text-muted-foreground">
            Choose a project to see running jobs.
          </p>
        ) : isLoading ? (
          <LoadingState label="Loading jobs" />
        ) : running.length === 0 &&
          finishedInPanel.length === 0 &&
          activityRows.length === 0 &&
          !activity.isLoading &&
          !activity.error ? (
          <div className="flex flex-col items-center gap-2 px-3 py-10 text-center">
            <Icon.bell className="size-5 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No notifications</p>
          </div>
        ) : (
          <div className="max-h-[420px] overflow-y-auto">
            {activity.isLoading && <LoadingState label="Loading activity" />}
            {activityRows.map((event, index) => (
              <ActivityFeedRow
                activity={event}
                connectedAbove={
                  !!event.sequenceKey && event.sequenceKey === activityRows[index - 1]?.sequenceKey
                }
                connectedBelow={
                  !!event.sequenceKey && event.sequenceKey === activityRows[index + 1]?.sequenceKey
                }
                key={event.id}
              />
            ))}
            {activity.hasNextPage && (
              <Button
                className="m-3"
                disabled={activity.isFetchingNextPage}
                onClick={() => void activity.fetchNextPage()}
                size="sm"
                variant="secondary"
              >
                Load earlier activity
              </Button>
            )}
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
