import { useState } from "react";

import { ApiSnippetDialog } from "@/components/inference/api-snippet-dialog";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import {
  useDedicatedDeploymentQuery,
  useDedicatedGpusQuery,
  useStartDedicatedMutation,
  useStopDedicatedMutation,
  useUpdateDedicatedMutation,
} from "@/hooks/use-inference";
import { notify } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { DedicatedDeployment, DedicatedGpu, DeployedModel } from "@/openapi";

const STATUS_LABEL: Record<string, string> = {
  deploying: "Deploying",
  failed: "Failed",
  offline: "Offline",
  online: "Online",
  shutting_down: "Shutting down",
  warming: "Warming up",
};

const ACTIVE = new Set(["deploying", "warming", "online", "shutting_down"]);

function speed(gpu: DedicatedGpu, decision: boolean): string {
  if (decision) {
    return gpu.latencyMsP50 == null ? "Unmeasured" : `${Math.round(gpu.latencyMsP50)} ms`;
  }
  if (gpu.ttftMsP50 == null || gpu.tokensPerSecondP50 == null) return "Unmeasured";
  const first = Math.round(gpu.ttftMsP50);
  const rate = Math.round(gpu.tokensPerSecondP50);
  return `${first} ms to first token, ${rate} tokens/sec`;
}

function price(credits: string | null | undefined): string {
  return credits == null ? "Price unavailable" : `${credits} credits/hour per GPU`;
}

export function DedicatedDeploymentCard({ model }: { model: DeployedModel }) {
  const ready = model.status === "ready";
  const deployment = useDedicatedDeploymentQuery(model.id, ready);
  const gpus = useDedicatedGpusQuery(model.id, ready);
  const row = deployment.data ?? null;
  const active = row != null && ACTIVE.has(row.status ?? "");
  if (!ready) return null;
  return (
    <Card className="space-y-4 p-4">
      <div className="flex items-center gap-2">
        <Icon.server className="size-4" />
        <p className="text-sm font-medium">Dedicated GPU</p>
      </div>
      {row ? <DeploymentStatus model={model} row={row} /> : null}
      {gpus.isLoading || deployment.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading GPUs</p>
      ) : null}
      {deployment.isFetched && gpus.data && row?.status !== "shutting_down" ? (
        <GpuMenu
          decision={model.inferenceContract === "decision"}
          gpus={gpus.data}
          model={model}
          row={active ? row : null}
        />
      ) : null}
    </Card>
  );
}

function DeploymentStatus({ model, row }: { model: DeployedModel; row: DedicatedDeployment }) {
  const stop = useStopDedicatedMutation(model.id);
  const [snippetOpen, setSnippetOpen] = useState(false);
  const decision = model.inferenceContract === "decision";
  const stopping = row.status === "shutting_down";
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
        <span className="font-medium">{STATUS_LABEL[row.status ?? ""] ?? row.status}</span>
        <span>{row.gpuType}</span>
        <span>{row.runningReplicas ?? 0} replicas running</span>
        <span>{price(row.creditsPerGpuHour)}</span>
        <span>{row.accruedCredits ?? "0"} credits this run</span>
      </div>
      {row.ratesFetchedAt ? (
        <p className="text-xs text-muted-foreground">
          Rate fetched <DateTime value={row.ratesFetchedAt} />
        </p>
      ) : null}
      {row.errorMessage ? <p className="text-sm text-destructive">{row.errorMessage}</p> : null}
      <div className="flex flex-wrap gap-2">
        {row.status === "online" ? (
          <Button onClick={() => setSnippetOpen(true)} size="sm" variant="secondary">
            <Icon.terminal className="size-3.5" />
            Dedicated snippet
          </Button>
        ) : null}
        {ACTIVE.has(row.status ?? "") ? (
          <Button
            disabled={stop.isPending || stopping}
            onClick={() =>
              stop.mutate(undefined, {
                onError: (error) => notify.error(error, "Couldn't stop the deployment"),
              })
            }
            size="sm"
            variant="secondary"
          >
            <Icon.stop className="size-3.5" />
            {stopping ? "Shutting down" : "Stop"}
          </Button>
        ) : null}
      </div>
      <ApiSnippetDialog
        capabilityId={null}
        decisionDeploymentId={decision ? model.id : undefined}
        modelId={row.modelId}
        onOpenChange={setSnippetOpen}
        open={snippetOpen}
        trainingJobId={model.finetuningJobId}
      />
    </div>
  );
}

function GpuMenu({
  decision,
  gpus,
  model,
  row,
}: {
  decision: boolean;
  gpus: DedicatedGpu[];
  model: DeployedModel;
  row: DedicatedDeployment | null;
}) {
  const [gpuType, setGpuType] = useState(row?.gpuType ?? "");
  const [minReplicas, setMinReplicas] = useState(row?.minReplicas ?? 1);
  const [maxReplicas, setMaxReplicas] = useState(row?.maxReplicas ?? 1);
  const [scaleToZero, setScaleToZero] = useState(row?.scaleToZero ?? false);
  const [idleMinutes, setIdleMinutes] = useState(
    Math.round((row?.idleShutdownSeconds ?? 900) / 60)
  );
  const [syncedId, setSyncedId] = useState(row?.id ?? null);
  if (row && syncedId !== row.id) {
    setSyncedId(row.id);
    setGpuType(row.gpuType);
    setMinReplicas(row.minReplicas ?? 1);
    setMaxReplicas(row.maxReplicas ?? 1);
    setScaleToZero(row.scaleToZero ?? false);
    setIdleMinutes(Math.round((row.idleShutdownSeconds ?? 900) / 60));
  }
  const start = useStartDedicatedMutation(model.id);
  const update = useUpdateDedicatedMutation(model.id);
  const pending = start.isPending || update.isPending;

  const selected = gpus.find((gpu) => gpu.gpuType === gpuType);
  const credits = selected?.creditsPerGpuHour == null ? null : Number(selected.creditsPerGpuHour);
  const minimum = scaleToZero ? 0 : minReplicas;
  const body = {
    gpuType,
    idleShutdownSeconds: idleMinutes * 60,
    maxReplicas,
    minReplicas: minimum,
    scaleToZero,
  };
  const invalid =
    !gpuType ||
    credits == null ||
    minimum > maxReplicas ||
    maxReplicas > 8 ||
    idleMinutes < 5 ||
    idleMinutes > 24 * 60;

  const submit = () => {
    const onError = (error: unknown) =>
      notify.error(error, row ? "Couldn't update the deployment" : "Couldn't start the deployment");
    if (row) update.mutate(body, { onError });
    else start.mutate(body, { onError });
  };

  if (gpus.length === 0) {
    return <p className="text-sm text-muted-foreground">No GPU fits this model.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="divide-y divide-border/70 rounded-md border border-border">
        {gpus.map((gpu) => (
          <button
            className={cn(
              "flex w-full flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-3 py-2 text-left text-sm",
              gpu.gpuType === gpuType && "bg-wash-raised"
            )}
            key={gpu.gpuType}
            onClick={() => setGpuType(gpu.gpuType)}
            type="button"
          >
            <span className="font-medium">{gpu.gpuType}</span>
            <span className="text-muted-foreground">{speed(gpu, decision)}</span>
            <span>{price(gpu.creditsPerGpuHour)}</span>
          </button>
        ))}
      </div>
      {selected?.ratesFetchedAt ? (
        <p className="text-xs text-muted-foreground">Rate fetched {selected.ratesFetchedAt}</p>
      ) : null}
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-xs text-muted-foreground">
          Min replicas
          <Input
            className="w-24"
            disabled={scaleToZero}
            max={8}
            min={scaleToZero ? 0 : 1}
            onChange={(event) => setMinReplicas(Number(event.target.value))}
            type="number"
            value={minimum}
          />
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          Max replicas
          <Input
            className="w-24"
            max={8}
            min={1}
            onChange={(event) => setMaxReplicas(Number(event.target.value))}
            type="number"
            value={maxReplicas}
          />
        </label>
        <label className="space-y-1 text-xs text-muted-foreground">
          Idle timeout (minutes)
          <Input
            className="w-28"
            max={24 * 60}
            min={5}
            onChange={(event) => setIdleMinutes(Number(event.target.value))}
            type="number"
            value={idleMinutes}
          />
        </label>
        <label className="flex items-center gap-2 pb-2 text-sm">
          <Checkbox
            checked={scaleToZero}
            onCheckedChange={(checked) => setScaleToZero(checked === true)}
          />
          Scale to zero
        </label>
      </div>
      {credits != null ? (
        <p className="text-sm text-muted-foreground">
          {Math.round(minimum * credits)} credits/hour at the minimum.{" "}
          {Math.round(maxReplicas * credits)} credits/hour at the maximum. One request uses one
          replica.
        </p>
      ) : (
        <p className="text-sm text-muted-foreground">Price unavailable</p>
      )}
      <Button disabled={invalid || pending} onClick={submit} size="sm">
        <Icon.play className="size-3.5" />
        {pending ? "Saving" : row ? "Update" : "Deploy"}
      </Button>
    </div>
  );
}
