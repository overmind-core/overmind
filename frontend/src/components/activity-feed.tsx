import { ActivityAssetChip, ActivityTaskChip } from "@/components/activity-links";
import { CodingAgentIcon } from "@/components/coding-agent-icon";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { useActivityHighlights } from "@/hooks/use-activity";
import { StatusBadge } from "@/lib/job-status";
import { cn } from "@/lib/utils";
import type { Activity } from "@/openapi";

export function ActivityFeedRow({
  activity,
  connectedAbove = false,
  connectedBelow = false,
}: {
  activity: Activity;
  connectedAbove?: boolean;
  connectedBelow?: boolean;
}) {
  const highlights = useActivityHighlights();
  const evaluationChange = ["evaluation_added", "evaluation_deleted"].includes(activity.kind);
  const datasetAction = {
    "Published dataset preparation": "published transformed data to",
    "Requested dataset": "created",
    "Saved dataset pipeline": "saved a transformation for",
    "Source ready": "finished importing data into",
    "Uploaded source": "uploaded data to",
  }[activity.action];
  const completedAction =
    activity.status === "completed"
      ? {
          "Requested a transformation of": "transformed",
          "Requested a transformation preview for": "previewed a transformation of",
          "Requested preparation of training data": "prepared training data",
          "Requested training data preparation for": "prepared training data from",
          "Started training on": "finished training on",
          "Started training run": "completed training run",
        }[activity.action]
      : undefined;
  const runningAction =
    activity.status === "running"
      ? {
          "Requested a transformation of": "is transforming",
          "Requested a transformation preview for": "is previewing a transformation of",
          "Requested preparation of training data": "is preparing training data",
          "Requested training data preparation for": "is preparing training data from",
          "Started training on": "is training on",
          "Started training run": "is running",
        }[activity.action]
      : undefined;
  const action = evaluationChange
    ? activity.kind === "evaluation_added"
      ? "added"
      : "deleted"
    : (runningAction ??
      completedAction ??
      datasetAction ??
      activity.action.charAt(0).toLowerCase() + activity.action.slice(1));
  const assets = evaluationChange
    ? [...activity.assets].sort(
        (a, b) => Number(b.kind === "evaluation") - Number(a.kind === "evaluation")
      )
    : activity.assets;
  return (
    <div
      className={cn(
        "relative flex items-center gap-3 px-3 py-3 last:border-b-0",
        !connectedBelow && "border-b border-border/70",
        highlights[`activity:${activity.id}`] && "activity-highlight",
        (connectedAbove || connectedBelow) && "pl-7"
      )}
      data-activity-id={activity.id}
      data-activity-status={activity.status}
      data-sequence-key={activity.sequenceKey || undefined}
    >
      {(connectedAbove || connectedBelow) && (
        <span aria-hidden="true" className="pointer-events-none absolute inset-y-0 left-3 w-3">
          {connectedAbove && (
            <span className="sidebar-elbow sidebar-elbow-thin absolute inset-y-0 left-2.5 w-3 [--elbow-h:50%]" />
          )}
          {!connectedAbove && connectedBelow && (
            <span className="absolute top-1/2 left-0 h-px w-3 bg-[var(--sidebar-elbow)]" />
          )}
          {connectedBelow && (
            <span className="absolute bottom-0 left-0 h-1/2 w-px bg-[var(--sidebar-elbow)]" />
          )}
        </span>
      )}
      <div className="flex min-w-0 flex-1 items-center gap-1.5 whitespace-nowrap text-sm text-muted-foreground">
        <span className="min-w-0 max-w-[60%] shrink-0 truncate">
          <span className="font-medium text-foreground">
            <span className="mr-1.5 inline-flex align-middle">
              <CodingAgentIcon kind={activity.actorKind} />
            </span>
            {activity.actorName}
          </span>{" "}
          {action}
        </span>
        {activity.taskId && activity.project && assets.length === 0 && (
          <>
            <ActivityTaskChip
              id={activity.taskId}
              kind={activity.taskKind}
              projectId={activity.project}
            />{" "}
          </>
        )}
        {activity.project &&
          assets.map((asset, index) => (
            <span
              className="flex min-w-0 max-w-64 items-center gap-1.5"
              key={`${asset.kind}:${asset.id}`}
            >
              {index > 0
                ? evaluationChange
                  ? activity.kind === "evaluation_added"
                    ? " to "
                    : " from "
                  : " · "
                : ""}
              <ActivityAssetChip asset={asset} projectId={activity.project!} />
            </span>
          ))}
      </div>
      <div className="flex shrink-0 items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
        {activity.taskId && (
          <StatusBadge
            actorKind={activity.actorKind}
            className="shrink-0"
            fallback="queued"
            status={activity.status === "accepted" ? "queued" : activity.status}
          />
        )}
        <DateTime compact value={activity.updatedAt} />
        {!activity.seen && (
          <span aria-label="Unread" className="inline-flex text-success" role="img">
            <Icon.notify aria-hidden className="size-3 shrink-0" />
          </span>
        )}
      </div>
    </div>
  );
}
