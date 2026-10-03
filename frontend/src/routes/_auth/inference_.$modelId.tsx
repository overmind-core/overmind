import { useState } from "react";

import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";

import { ModelLiveAction } from "@/components/finetuning/model-live-action";
import { ApiSnippetDialog } from "@/components/inference/api-snippet-dialog";
import {
  BaseModelCell,
  CapabilityChip,
  TrainingJobChip,
} from "@/components/inference/model-identity";
import { ModelPerformance } from "@/components/inference/model-performance";
import { ServingStatusBadge } from "@/components/inference/serving-status";
import { DetailErrorState } from "@/components/route-error";
import {
  AlertDialog,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Skeleton } from "@/components/ui/skeleton";
import { useFinetuningJobsQuery } from "@/hooks/use-finetuning";
import {
  downloadModelWeightsZip,
  useDeleteModelMutation,
  useDeployedModelQuery,
  useModelCheckpointsQuery,
  useModelLiveQuery,
  useRetryModelMutation,
} from "@/hooks/use-inference";
import { notify } from "@/lib/notify";
import { inferenceSearchSchema } from "@/lib/schemas";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { DeployedModel } from "@/openapi";

export const Route = createFileRoute("/_auth/inference_/$modelId")({
  component: ModelDetailPage,
  // The list's schema, not the bare project one: zod strips undeclared keys, so
  // the filters and page must be declared here to survive the round trip out.
  validateSearch: inferenceSearchSchema,
});

function ModelDetailPage() {
  const { modelId } = Route.useParams();
  const { projectId } = Route.useSearch();
  const [snippetOpen, setSnippetOpen] = useState(false);

  const { data: model, isLoading, error, refetch } = useDeployedModelQuery(modelId);
  const [weightsOpen, setWeightsOpen] = useState(false);
  const checkpoints = useModelCheckpointsQuery(modelId, weightsOpen);
  const { data: live } = useModelLiveQuery(modelId, true);

  // The copy-prompt path operates on the originating finetuning job.
  const { data: ftJobs } = useFinetuningJobsQuery(projectId);
  const jobs = ftJobs?.results?.filter((j) => j.id === model?.finetuningJobId) ?? [];

  const header = (
    <PageHeader
      actions={
        model ? (
          <Button onClick={() => setSnippetOpen(true)} size="sm" variant="secondary">
            <Icon.terminal className="size-3.5" />
            View API snippet
          </Button>
        ) : null
      }
      className="flex-wrap"
      icon={
        <Icon.inferenceTitle
          aria-hidden
          className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
        />
      }
      title={
        model ? (
          <span className="flex min-w-0 items-center gap-2.5">
            <span className="min-w-0 truncate">{model.modelId}</span>
            <CopyModelIdButton modelId={model.modelId} />
            <ServingStatusBadge live={live} status={model.status} />
          </span>
        ) : (
          "Model"
        )
      }
    />
  );

  return (
    <PageShell header={header} variant="scroll">
      {isLoading && <Skeleton className="h-64 w-full rounded-md" />}
      {error && (
        <DetailErrorState
          action={
            <Button onClick={() => void refetch()} size="sm" variant="secondary">
              <Icon.refresh />
              Retry
            </Button>
          }
          error={error}
          fallback="Couldn't load this model."
        />
      )}

      {model && (
        <div className="space-y-6">
          {(model.status === "failed" || model.status === "deleted") && model.finetuningJobId ? (
            <RedeployBanner model={model} />
          ) : null}
          <ServingCard
            actions={
              projectId ? (
                <ModelLiveAction
                  capabilityId={model.capabilityId}
                  jobs={jobs}
                  model={model}
                  projectId={projectId}
                  promote
                >
                  <Button asChild size="sm" variant="secondary">
                    <Link
                      search={{ model: model.modelId, projectId, view: "roots" }}
                      to="/observability"
                    >
                      <Icon.observability />
                      View traces
                    </Link>
                  </Button>
                </ModelLiveAction>
              ) : null
            }
            model={model}
          />
          <ModelPerformance modelId={model.id} onConnect={() => setSnippetOpen(true)} />
          <Disclosure onOpenChange={setWeightsOpen} title="Deployment details">
            <div className="space-y-5">
              <div className="grid grid-cols-2 gap-x-8 gap-y-4 lg:grid-cols-4">
                <LineageField label="Base model">
                  <BaseModelCell baseModelId={model.baseModelId} />
                </LineageField>
                <LineageField label="Training job">
                  {model.finetuningJobId ? (
                    <TrainingJobChip
                      baseModelId={model.baseModelId}
                      capabilityName={model.capabilityName}
                      jobId={model.finetuningJobId}
                      jobName={model.finetuningJobName}
                      projectId={projectId}
                    />
                  ) : (
                    "—"
                  )}
                </LineageField>
                <LineageField label="Context window">
                  {model.maxModelLen.toLocaleString()} tokens
                </LineageField>
                <LineageField label="GPU">{model.gpuType || "—"}</LineageField>
                <LineageField label="Precision">{model.quantization || "—"}</LineageField>
                <LineageField label="Deployed">
                  <DateTime value={model.deployedAt ?? model.createdAt} />
                </LineageField>
              </div>
              <div className="space-y-3 border-t border-border/70 pt-4">
                <p className="text-sm font-medium">Weights & checkpoints</p>
                <WeightsContent
                  checkpoints={checkpoints}
                  deployedId={model.id}
                  modelId={model.modelId}
                />
              </div>
              <DangerZone id={model.id} modelId={model.modelId} />
            </div>
          </Disclosure>
          <ApiSnippetDialog
            capabilityId={model.capabilityId}
            modelId={model.modelId}
            onOpenChange={setSnippetOpen}
            open={snippetOpen}
            projectId={projectId}
          />
        </div>
      )}
    </PageShell>
  );
}

/** Retry restarts from the checkpoint download and costs a full re-quantise,
 * so it stays an explicit action rather than an automatic one. */
function RedeployBanner({ model }: { model: DeployedModel }) {
  const retry = useRetryModelMutation();
  const failed = model.status === "failed";

  const redeploy = () =>
    retry.mutate(model.id, {
      onError: (e) => notify.error(e, failed ? "Couldn't retry deploy" : "Couldn't deploy model"),
      onSuccess: () =>
        notify.success("Deployment started", `${model.modelId} is being deployed again.`),
    });

  return (
    <div
      className={cn(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-md border px-4 py-3",
        failed ? "border-destructive/40 bg-destructive/10" : "border-border bg-wash-raised"
      )}
    >
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm font-semibold", failed && "text-destructive")}>
          {failed ? "Deployment failed" : "Not deployed"}
        </p>
        <p className="text-sm leading-snug break-words text-muted-foreground">
          {failed
            ? model.errorMessage || "The deploy pipeline did not finish."
            : "Weights are preserved. Deploy again to bring this model back online."}
        </p>
      </div>
      <Button className="shrink-0" disabled={retry.isPending} onClick={redeploy} size="sm">
        <Icon.refresh />
        {retry.isPending ? "Starting…" : failed ? "Retry" : "Deploy"}
      </Button>
    </div>
  );
}

function ServingCard({ model, actions }: { model: DeployedModel; actions?: React.ReactNode }) {
  const reference = model.capabilityId ? `overmind/${model.capabilityId}` : model.modelId;
  return (
    <Card className="flex flex-wrap items-start justify-between gap-4 p-4">
      <div className="min-w-0 space-y-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{model.capabilityId ? "Capability alias" : "Model ID"}</span>
          {model.capabilityId ? (
            <CapabilityChip
              capabilityId={model.capabilityId}
              capabilityName={model.capabilityName}
            />
          ) : null}
        </div>
        <div className="flex min-w-0 items-center gap-2">
          <code className="break-all font-mono text-xs">{reference}</code>
          <CopyModelIdButton modelId={reference} />
        </div>
      </div>
      {actions ? <div className="ml-auto min-w-0 max-w-full">{actions}</div> : null}
    </Card>
  );
}

function CopyModelIdButton({ modelId }: { modelId: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    void navigator.clipboard.writeText(modelId).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
        notify.success("Copied model id");
      },
      () => notify.error("Could not copy")
    );
  };
  return (
    <button
      aria-label="Copy model id"
      className="shrink-0 rounded-sm p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      onClick={copy}
      title={`Copy ${modelId}`}
      type="button"
    >
      {copied ? <Icon.success className="size-4 text-success" /> : <Icon.copy className="size-4" />}
    </button>
  );
}

function LineageField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function WeightsContent({
  checkpoints,
  deployedId,
  modelId,
}: {
  checkpoints: ReturnType<typeof useModelCheckpointsQuery>;
  deployedId: string;
  modelId: string;
}) {
  const [zipping, setZipping] = useState(false);
  const files = checkpoints.data?.files ?? [];

  if (checkpoints.isLoading) return <Skeleton className="h-24 w-full" />;
  if (checkpoints.error) {
    return (
      <EmptyState
        description={(checkpoints.error as Error).message}
        icon={Icon.download}
        size="section"
        title="Checkpoints unavailable"
      />
    );
  }
  if (!files.length) {
    return (
      <EmptyState
        description="No weight files available for this model."
        icon={Icon.download}
        size="section"
        title="No weights"
      />
    );
  }

  const handleZip = async () => {
    if (zipping) return;
    setZipping(true);
    try {
      const safe = modelId.replaceAll("/", "-");
      await downloadModelWeightsZip(deployedId, `${safe}-weights.zip`);
      notify.success("Weights download started");
    } catch (e) {
      notify.error(e, "Couldn't download weights");
    } finally {
      setZipping(false);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button disabled={zipping} onClick={() => void handleZip()} size="sm">
        <Icon.download />
        {zipping ? "Preparing zip…" : "Download weights"}
      </Button>
      <span className="text-xs text-muted-foreground">
        Zip of adapter, config, and tokenizer files
      </span>
    </div>
  );
}

function Disclosure({
  title,
  defaultOpen = false,
  onOpenChange,
  children,
}: {
  title: string;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };
  return (
    <Collapsible
      className="overflow-hidden rounded-md border border-border"
      onOpenChange={handleOpenChange}
      open={open}
    >
      <CollapsibleTrigger className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-wash-raised">
        <span className="text-sm font-semibold">{title}</span>
        <Icon.chevronDown
          className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")}
        />
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-border/70 px-4 py-3">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}

function DangerZone({ id, modelId }: { id: string; modelId: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 border-t border-border/70 pt-4">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">Delete deployment</p>
        <p className={cn(PROSE, "text-xs leading-snug text-muted-foreground")}>
          Permanently delete this fine-tuned model and undeploy it. This cannot be undone.
        </p>
      </div>
      <DeleteModelButton id={id} modelId={modelId} />
    </div>
  );
}

function DeleteModelButton({ id, modelId }: { id: string; modelId: string }) {
  const [open, setOpen] = useState(false);
  const [confirm, setConfirm] = useState("");
  const navigate = useNavigate();
  const del = useDeleteModelMutation();
  const canDelete = confirm.trim() === modelId && !del.isPending;

  const handleDelete = () => {
    if (!canDelete) return;
    del.mutate(id, {
      onError: (e) => notify.error(e, "Couldn't delete model"),
      onSuccess: () => {
        setOpen(false);
        notify.success("Model deletion started", `${modelId} is being undeployed.`);
        navigate({ search: (prev) => prev, to: "/inference" });
      },
    });
  };

  return (
    <AlertDialog
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setConfirm("");
      }}
      open={open}
    >
      <Button
        className="shrink-0 text-destructive"
        onClick={() => setOpen(true)}
        size="sm"
        variant="secondary"
      >
        <Icon.delete className="size-3.5" />
        Delete model
      </Button>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this model?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes your fine-tuned model and undeploys it.{" "}
            <span className="font-medium text-foreground">This cannot be undone.</span> In-flight
            requests to this model will start failing.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogBody className="space-y-1.5">
          <p className="flex flex-wrap items-center gap-1.5 text-sm text-muted-foreground">
            Type
            <span className="font-mono text-xs text-foreground">{modelId}</span>
            <CopyModelIdButton modelId={modelId} />
            to confirm.
          </p>
          <Input
            autoComplete="off"
            onChange={(e) => setConfirm(e.target.value)}
            placeholder={modelId}
            value={confirm}
          />
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <Button disabled={!canDelete} onClick={handleDelete} variant="destructive">
            {del.isPending ? "Deleting…" : "Delete model"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
