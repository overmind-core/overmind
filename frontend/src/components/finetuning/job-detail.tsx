import { Link } from "@tanstack/react-router";

import { FailureCard } from "@/components/failure-card";
import { LiveMetricsChart, LossChart } from "@/components/finetuning/finetuning-charts";
import { DefRow, Eyebrow, FtStatusBadge } from "@/components/finetuning/finetuning-chrome";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { isTerminalStatus } from "@/components/finetuning/job-snapshot";
import { TIER_META, userFacingJobError } from "@/components/finetuning/train/model-config";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CreditsAmount } from "@/components/ui/credits";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { LoadingState } from "@/components/ui/spinner";
import {
  useCancelFinetuningJobMutation,
  useFinetuningJobQuery,
  useLossCurvesQuery,
  useRetryFinetuningJobMutation,
} from "@/hooks/use-finetuning";
import { jobCostUsd } from "@/lib/finetuning-cost";
import { parseFinetuningProgress } from "@/lib/finetuning-progress";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";

export function JobDetailDrawer({
  jobId,
  onOpenChange,
  projectId,
}: {
  jobId: string | null;
  onOpenChange: (open: boolean) => void;
  projectId: string;
}) {
  return (
    <Sheet onOpenChange={onOpenChange} open={!!jobId}>
      <SheetContent size="lg">
        <SheetTitle className="sr-only">Fine-tuning job details</SheetTitle>
        <SheetDescription className="sr-only">
          Details, progress and events for this job.
        </SheetDescription>
        {jobId && <JobDetail jobId={jobId} projectId={projectId} />}
      </SheetContent>
    </Sheet>
  );
}

function JobDetail({ jobId, projectId }: { jobId: string; projectId: string }) {
  const { data: job, isLoading, error } = useFinetuningJobQuery(jobId);
  const lossCurvesQuery = useLossCurvesQuery(jobId);
  const cancelMutation = useCancelFinetuningJobMutation(projectId);
  const retryMutation = useRetryFinetuningJobMutation(projectId);

  if (error && !job) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          {errorMessage(error, "Couldn't load this job. It may have been deleted.")}
        </Alert>
      </div>
    );
  }

  if (isLoading || !job) {
    return <LoadingState className="h-40" />;
  }

  const terminal = isTerminalStatus(job.status as string);
  const events = job.events ?? [];
  const tier = job.modelTier as string;
  const lossData = lossCurvesQuery.data;
  const epochCount = lossData?.epochs?.length ?? lossData?.steps?.length ?? 0;
  const tierMeta = TIER_META[tier];
  const jobProgress = (job.progress ?? {}) as Record<string, unknown>;
  const liveProgress = parseFinetuningProgress(job.progress);
  const hasLiveHistory =
    (liveProgress?.metrics_history?.length ?? 0) > 0 ||
    (liveProgress?.eval_history?.length ?? 0) > 0;

  return (
    <>
      <SheetHeader>
        <div className="flex items-center gap-2">
          {tierMeta && (
            <Badge className={cn("border text-xs", tierMeta.color)} variant="outline">
              {tierMeta.label}
            </Badge>
          )}
          <FtStatusBadge fallback="queued" status={job.status as string} />
        </div>
        <p className="truncate font-semibold">{job.name || job.baseModel}</p>
      </SheetHeader>

      <SheetBody className="space-y-6">
        <section>
          <Eyebrow>Model</Eyebrow>
          <ModelProviderChip model={job.baseModel} />
        </section>

        {/* Richer than the epoch loss chart below, so it takes precedence. */}
        {hasLiveHistory && liveProgress && (
          <section>
            <Eyebrow>Live metrics</Eyebrow>
            <LiveMetricsChart progress={liveProgress} />
          </section>
        )}

        {!hasLiveHistory && epochCount > 0 && lossData && (
          <section>
            <Eyebrow>Loss</Eyebrow>
            {epochCount === 1 ? (
              <div className="flex flex-col gap-1 text-xs text-muted-foreground">
                {lossData.train_loss[0] != null && (
                  <span>
                    train loss{" "}
                    <span className="font-mono text-foreground">
                      {(lossData.train_loss[0] as number).toFixed(4)}
                    </span>
                  </span>
                )}
                {(lossData.valid_loss?.[0] ?? lossData.eval_loss?.[0]) != null && (
                  <span>
                    eval loss{" "}
                    <span className="font-mono text-foreground">
                      {((lossData.valid_loss?.[0] ?? lossData.eval_loss?.[0]) as number).toFixed(4)}
                    </span>
                  </span>
                )}
              </div>
            ) : (
              <LossChart data={lossData} height={180} />
            )}
          </section>
        )}

        {jobProgress.trained_steps != null || jobProgress.percent != null ? (
          <section>
            <Eyebrow>Progress</Eyebrow>
            <dl className="space-y-1.5 text-sm">
              <DefRow
                label="Steps"
                mono
                value={
                  jobProgress.trained_steps != null
                    ? `${jobProgress.trained_steps}${
                        jobProgress.total_steps != null ? ` / ${jobProgress.total_steps}` : ""
                      }`
                    : null
                }
              />
              <DefRow
                label="Percent"
                mono
                value={jobProgress.percent != null ? `${jobProgress.percent}%` : null}
              />
            </dl>
          </section>
        ) : null}

        {job.status === "failed" && job.errorMessage ? (
          <FailureCard
            alternative={
              job.dataset ? (
                <Link
                  params={{ datasetId: job.dataset }}
                  search={{ projectId }}
                  to="/datasets/$datasetId"
                >
                  Fix in workshop
                </Link>
              ) : undefined
            }
            error={userFacingJobError(job.errorMessage)}
            onRetry={() => retryMutation.mutate(job.id)}
            retryPending={retryMutation.isPending}
            title="Fine-tuning failed"
          />
        ) : job.errorMessage && job.status !== "cancelled" ? (
          <Alert variant="destructive">
            <Icon.warning className="size-4 shrink-0" />
            <div className="text-sm">{userFacingJobError(job.errorMessage)}</div>
          </Alert>
        ) : null}

        <section>
          <Eyebrow>Timing</Eyebrow>
          <dl className="space-y-1.5 text-sm">
            <DefRow label="Created" value={<DateTime value={job.createdAt} />} />
            <DefRow
              label="Started"
              value={job.startedAt ? <DateTime value={job.startedAt} /> : null}
            />
            <DefRow
              label="Completed"
              value={job.completedAt ? <DateTime value={job.completedAt} /> : null}
            />
          </dl>
        </section>

        <section>
          <Eyebrow>Credits</Eyebrow>
          <dl className="space-y-1.5 text-sm">
            <DefRow
              label="Used"
              mono
              value={jobCostUsd(job) != null ? <CreditsAmount usd={jobCostUsd(job)!} /> : null}
            />
            <DefRow
              label="Minutes"
              mono
              value={job.billedMinutes != null ? String(job.billedMinutes) : null}
            />
            <DefRow
              label="Synced"
              value={job.costSyncedAt ? <DateTime value={job.costSyncedAt} /> : null}
            />
          </dl>
        </section>

        {job.hyperparameters && Object.keys(job.hyperparameters).length > 0 && (
          <section>
            <Eyebrow>Hyperparameters</Eyebrow>
            <dl className="space-y-1.5 text-sm">
              {Object.entries(job.hyperparameters as Record<string, unknown>).map(([k, v]) => (
                <DefRow
                  key={k}
                  label={k}
                  mono
                  value={typeof v === "object" ? JSON.stringify(v) : String(v)}
                />
              ))}
            </dl>
          </section>
        )}

        {job.status === "succeeded" && job.outputModelName && (
          <section>
            <Eyebrow>Output</Eyebrow>
            <FinetuningModelChip
              deployedModelUuid={job.deployedModelId}
              model={job.outputModelName}
              projectId={projectId}
            />
          </section>
        )}

        {events.length > 0 && (
          <section>
            <Eyebrow>Timeline</Eyebrow>
            <ol className="relative ml-2 space-y-3 border-l border-border/70 pl-4">
              {events.map((evt) => (
                <li className="relative text-xs" key={evt.id}>
                  <span
                    className={cn(
                      "absolute -left-[21px] top-1.5 size-2 rounded-xs border border-border",
                      evt.eventType === "error"
                        ? "bg-destructive"
                        : evt.eventType === "status_change"
                          ? "bg-primary"
                          : "bg-muted-foreground/50"
                    )}
                  />
                  <div className="flex items-baseline gap-2">
                    <span
                      className={cn(
                        "font-mono text-xs",
                        evt.eventType === "error" ? "text-destructive" : "text-muted-foreground"
                      )}
                    >
                      {evt.eventType.replace("_", " ")}
                    </span>
                    <span className="ml-auto shrink-0 text-muted-foreground">
                      <DateTime value={evt.createdAt} />
                    </span>
                  </div>
                  <p className="mt-0.5 leading-snug">{evt.message || "—"}</p>
                </li>
              ))}
            </ol>
          </section>
        )}
      </SheetBody>

      {job.status === "succeeded" ||
      (job.status === "failed" && !job.errorMessage) ||
      job.status === "cancelled" ||
      !terminal ? (
        <SheetFooter>
          {job.status === "succeeded" && job.modelWeightsLocation && (
            <Button
              onClick={() => window.open(job.modelWeightsLocation, "_blank", "noopener,noreferrer")}
              variant="secondary"
            >
              <Icon.download />
              Download weights
            </Button>
          )}

          {job.status === "succeeded" && job.deployedModelId && (
            <Button asChild>
              <Link
                params={{ modelId: job.deployedModelId }}
                search={{ projectId }}
                to="/inference/$modelId"
              >
                <Icon.inference />
                Inference
              </Link>
            </Button>
          )}

          {/* A failure with a message already has FailureCard's own Retry above. */}
          {((job.status === "failed" && !job.errorMessage) || job.status === "cancelled") && (
            <Button disabled={retryMutation.isPending} onClick={() => retryMutation.mutate(job.id)}>
              <Icon.refresh />
              Retry
            </Button>
          )}

          {!terminal && (
            <ConfirmDialog
              cancelLabel="Keep running"
              confirmLabel="Cancel job"
              description="This stops the running fine-tuning job. Progress so far will be lost and the job cannot be resumed."
              destructive
              isPending={cancelMutation.isPending}
              onConfirm={() => cancelMutation.mutate(job.id)}
              title="Cancel this job?"
              trigger={
                <Button variant="destructive">
                  <Icon.stop />
                  Cancel job
                </Button>
              }
            />
          )}
        </SheetFooter>
      ) : null}
    </>
  );
}
