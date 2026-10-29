import { type ReactNode, useState } from "react";

import { ApiSnippetDialog } from "@/components/inference/api-snippet-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  useDedicatedDeploymentQuery,
  useDedicatedGpusQuery,
  useStartDedicatedMutation,
  useStopDedicatedMutation,
  useUpdateDedicatedMutation,
} from "@/hooks/use-inference";
import { useCommercialBilling, useCredits } from "@/hooks/use-subscription";
import { notify } from "@/lib/notify";
import { cn, usdToOvermindCredits } from "@/lib/utils";
import type { DedicatedDeployment, DedicatedGpu, DeployedModel } from "@/openapi";

export const STATUS_LABEL: Record<string, string> = {
  deploying: "Deploying",
  failed: "Failed",
  offline: "Offline",
  online: "Online",
  shutting_down: "Shutting down",
  warming: "Warming up",
};

export const STATUS_TONE = {
  deploying: "info",
  failed: "error",
  offline: "neutral",
  online: "success",
  shutting_down: "warning",
  warming: "info",
} as const;

const ACTIVE = new Set(["deploying", "warming", "online", "shutting_down"]);

function creditsAmount(raw: string | null | undefined): string {
  if (raw == null || raw === "") return "—";
  const amount = Number(raw);
  if (!Number.isFinite(amount)) return raw;
  return amount.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

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

function digits(raw: string): string {
  return raw.replace(/\D/g, "").replace(/^0+(?=\d)/, "");
}

function whole(raw: string): number | null {
  return /^\d+$/.test(raw) ? Number(raw) : null;
}

function hourShortfall(balance: number, floor: number): string {
  const owned = balance.toLocaleString("en-US");
  const rate = floor.toLocaleString("en-US");
  return `${owned} credits is less than one hour at this minimum (${rate} credits/hour).`;
}

function Hint({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={`About ${label.toLowerCase()}`}
          className="inline-flex rounded-sm text-muted-foreground/70 outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
          type="button"
        >
          <Icon.info className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64" side="bottom">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

export function DedicatedDeploymentCard({
  model,
  projectId,
}: {
  model: DeployedModel;
  projectId?: string;
}) {
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
      {row ? <DeploymentStatus model={model} projectId={projectId} row={row} /> : null}
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

function DeploymentStatus({
  model,
  projectId,
  row,
}: {
  model: DeployedModel;
  projectId?: string;
  row: DedicatedDeployment;
}) {
  const stop = useStopDedicatedMutation(model.id);
  const [snippetOpen, setSnippetOpen] = useState(false);
  const decision = model.inferenceContract === "decision";
  const status = row.status ?? "";
  const stopping = status === "shutting_down";
  const actions = (
    <div className="flex flex-wrap gap-2">
      {status === "online" ? (
        <Button onClick={() => setSnippetOpen(true)} size="sm" variant="secondary">
          <Icon.terminal className="size-3.5" />
          Dedicated snippet
        </Button>
      ) : null}
      {ACTIVE.has(status) ? (
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
  );
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Badge size="chip" variant={STATUS_TONE[status as keyof typeof STATUS_TONE] ?? "neutral"}>
            {STATUS_LABEL[status] ?? status}
          </Badge>
          <span className="text-sm font-medium">{row.gpuType}</span>
        </div>
        {actions}
      </div>
      <div className="grid grid-cols-3 gap-4 border-t border-border/70 pt-3">
        <StatusFact detail="running" label="Replicas" value={String(row.runningReplicas ?? 0)} />
        <StatusFact
          detail="credits/hour"
          label="Rate"
          value={creditsAmount(row.creditsPerGpuHour)}
        />
        <StatusFact detail="credits" label="This run" value={creditsAmount(row.accruedCredits)} />
      </div>
      {row.ratesFetchedAt ? (
        <p className="text-xs text-muted-foreground">
          Rate fetched <DateTime value={row.ratesFetchedAt} />
        </p>
      ) : null}
      {row.errorMessage ? <p className="text-sm text-destructive">{row.errorMessage}</p> : null}
      <ApiSnippetDialog
        capabilityId={null}
        decisionDeploymentId={decision ? model.id : undefined}
        modelId={row.modelId}
        onOpenChange={setSnippetOpen}
        open={snippetOpen}
        projectId={projectId}
        trainingJobId={model.finetuningJobId}
      />
    </div>
  );
}

function StatusFact({ label, value, detail }: { label: string; value: string; detail: ReactNode }) {
  return (
    <div className="min-w-0 space-y-1">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-sm font-medium tabular-nums">{value}</p>
      <p className="text-xs text-muted-foreground">{detail}</p>
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
  const [gpuType, setGpuType] = useState(row?.gpuType || gpus[0]?.gpuType || "");
  const [minReplicas, setMinReplicas] = useState(String(row?.minReplicas ?? 1));
  const [maxReplicas, setMaxReplicas] = useState(String(row?.maxReplicas ?? 1));
  const [scaleToZero, setScaleToZero] = useState(row?.scaleToZero ?? false);
  const [idleSeconds, setIdleSeconds] = useState(String(row?.idleShutdownSeconds ?? 900));
  const [syncedId, setSyncedId] = useState(row?.id ?? null);
  if (row && syncedId !== row.id) {
    setSyncedId(row.id);
    setGpuType(row.gpuType);
    setMinReplicas(String(row.minReplicas ?? 1));
    setMaxReplicas(String(row.maxReplicas ?? 1));
    setScaleToZero(row.scaleToZero ?? false);
    setIdleSeconds(String(row.idleShutdownSeconds ?? 900));
  }
  const start = useStartDedicatedMutation(model.id);
  const update = useUpdateDedicatedMutation(model.id);
  const pending = start.isPending || update.isPending;
  const { enabled: commercialBilling } = useCommercialBilling();
  const { balanceUsd } = useCredits();

  const selected = gpus.find((gpu) => gpu.gpuType === gpuType);
  const credits = selected?.creditsPerGpuHour == null ? null : Number(selected.creditsPerGpuHour);
  const minCount = whole(minReplicas);
  const maxCount = whole(maxReplicas);
  const idleCount = whole(idleSeconds);
  const minimum = scaleToZero ? 0 : minCount;
  const body = {
    gpuType,
    idleShutdownSeconds: idleCount ?? 0,
    maxReplicas: maxCount ?? 0,
    minReplicas: minimum ?? 0,
    scaleToZero,
  };
  const invalid =
    !gpuType ||
    credits == null ||
    minimum == null ||
    maxCount == null ||
    idleCount == null ||
    minimum > maxCount ||
    maxCount > 8 ||
    idleCount < 300 ||
    idleCount > 24 * 60 * 60;
  const balanceCredits = balanceUsd == null ? null : usdToOvermindCredits(balanceUsd);
  const floorCredits = credits != null && minimum != null ? Math.round(minimum * credits) : null;
  const hourWarning =
    commercialBilling &&
    balanceCredits != null &&
    floorCredits != null &&
    minimum != null &&
    minimum > 0 &&
    floorCredits > balanceCredits
      ? hourShortfall(balanceCredits, floorCredits)
      : null;

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
      <div className="grid grid-cols-[7.5rem_auto_minmax(0,1fr)] divide-y divide-border/70 overflow-x-auto rounded-md border border-border">
        {gpus.map((gpu) => (
          <button
            className={cn(
              "col-span-3 grid grid-cols-subgrid items-baseline gap-x-6 px-3 py-2 text-left text-sm",
              gpu.gpuType === gpuType && "bg-wash-raised"
            )}
            key={gpu.gpuType}
            onClick={() => setGpuType(gpu.gpuType)}
            type="button"
          >
            <span className="font-medium">{gpu.gpuType}</span>
            <span className="whitespace-nowrap text-muted-foreground">{speed(gpu, decision)}</span>
            <span className="justify-self-end whitespace-nowrap tabular-nums">
              {price(gpu.creditsPerGpuHour)}
            </span>
          </button>
        ))}
      </div>
      {selected?.ratesFetchedAt ? (
        <p className="text-xs text-muted-foreground">
          Rate fetched <DateTime value={selected.ratesFetchedAt} />
        </p>
      ) : null}
      <TooltipProvider delayDuration={150}>
        <div className="grid grid-cols-2 items-end gap-3 sm:grid-cols-[7.5rem_7.5rem_14rem_auto]">
          <div className="space-y-1">
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <label htmlFor="dedicated-min-replicas">Min replicas</label>
              <Hint
                label="Min replicas"
                text="GPUs that stay running. The hourly floor is this count times the per-GPU rate. At most 8."
              />
            </div>
            <Input
              disabled={scaleToZero}
              id="dedicated-min-replicas"
              inputMode="numeric"
              onChange={(event) => setMinReplicas(digits(event.target.value))}
              value={scaleToZero ? "0" : minReplicas}
            />
          </div>
          <div className="space-y-1">
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <label htmlFor="dedicated-max-replicas">Max replicas</label>
              <Hint
                label="Max replicas"
                text="Most GPUs this deployment can start. Extra replicas add capacity. At most 8."
              />
            </div>
            <Input
              id="dedicated-max-replicas"
              inputMode="numeric"
              onChange={(event) => setMaxReplicas(digits(event.target.value))}
              value={maxReplicas}
            />
          </div>
          <div className="col-span-2 space-y-1 sm:col-span-1">
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <label htmlFor="dedicated-idle-seconds">Idle timeout (seconds)</label>
              <Hint
                label="Idle timeout"
                text="Replicas above the minimum shut down after this many seconds with no requests. The minimum stays running. A value above 3600 seconds still waits 1 hour."
              />
            </div>
            <Input
              id="dedicated-idle-seconds"
              inputMode="numeric"
              onChange={(event) => setIdleSeconds(digits(event.target.value))}
              value={idleSeconds}
            />
          </div>
          <div className="col-span-2 flex h-8 items-center gap-2 self-end text-sm sm:col-span-1">
            <label className="flex items-center gap-2">
              <Checkbox
                checked={scaleToZero}
                onCheckedChange={(checked) => setScaleToZero(checked === true)}
              />
              Scale to zero
            </label>
            <Hint
              label="Scale to zero"
              text="On sets the minimum to 0, so an idle deployment costs nothing. Off keeps at least 1 replica running."
            />
          </div>
        </div>
      </TooltipProvider>
      {credits == null ? (
        <p className="text-sm text-muted-foreground">Price unavailable</p>
      ) : minimum == null || maxCount == null ? (
        <p className="text-sm text-muted-foreground">Enter a replica count.</p>
      ) : minimum > maxCount ? (
        <p className="text-sm text-muted-foreground">Minimum is above the maximum.</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          {Math.round(minimum * credits)} credits/hour at the minimum.{" "}
          {Math.round(maxCount * credits)} credits/hour at the maximum. One request uses one
          replica.
        </p>
      )}
      {hourWarning ? <p className="text-sm text-warning">{hourWarning}</p> : null}
      <Button disabled={invalid || pending} onClick={submit} size="sm">
        <Icon.play className="size-3.5" />
        {pending ? "Saving" : row ? "Update" : "Deploy"}
      </Button>
    </div>
  );
}
