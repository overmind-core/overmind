import { useState } from "react";

import { ApiSnippetDialog } from "@/components/inference/api-snippet-dialog";
import { DedicatedStartupProgress } from "@/components/inference/dedicated-startup";
import { EndpointStatus } from "@/components/inference/serving-status";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
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
import { usdToOvermindCredits } from "@/lib/utils";
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
  deploying: "default",
  failed: "error",
  offline: "neutral",
  online: "success",
  shutting_down: "warning",
  warming: "default",
} as const;

const ACTIVE = new Set(["deploying", "warming", "online", "shutting_down"]);
const STARTING = new Set(["deploying", "warming"]);

export function DedicatedStatusBadge({ status }: { status: string }) {
  return (
    <Badge
      className="gap-1.5"
      size="chip"
      variant={STATUS_TONE[status as keyof typeof STATUS_TONE] ?? "neutral"}
    >
      {STARTING.has(status) ? <Spinner className="text-current" size="sm" /> : null}
      {STATUS_LABEL[status] ?? status}
    </Badge>
  );
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
  return credits == null
    ? "Price unavailable"
    : `${Number(credits).toLocaleString(undefined, { maximumFractionDigits: 2 })} credits/hour per GPU`;
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

export function DedicatedDeploymentSection({
  model,
  projectId,
}: {
  model: DeployedModel;
  projectId?: string;
}) {
  const [open, setOpen] = useState(false);
  const [snippetOpen, setSnippetOpen] = useState(false);
  const [confirmStart, setConfirmStart] = useState(false);
  const start = useStartDedicatedMutation(model.id);
  const stop = useStopDedicatedMutation(model.id);
  const ready = model.status === "ready";
  const deployment = useDedicatedDeploymentQuery(model.id, ready);
  const gpus = useDedicatedGpusQuery(model.id, ready && (open || confirmStart));
  const row = deployment.data ?? null;
  const startRate = gpus.data?.find((gpu) => gpu.gpuType === row?.gpuType)?.creditsPerGpuHour;
  const active = row != null && ACTIVE.has(row.status ?? "");
  const stopping = row?.status === "shutting_down";
  const pending = start.isPending || stop.isPending;
  const toggleDeployment = () => {
    if (!row) return;
    if (active) {
      stop.mutate(undefined, {
        onError: (error) => notify.error(error, "Couldn't stop the deployment"),
      });
    } else {
      setConfirmStart(true);
    }
  };
  const startDeployment = () => {
    if (!row) return;
    start.mutate(
      {
        gpuType: row.gpuType,
        idleShutdownSeconds: row.idleShutdownSeconds ?? 900,
        maxReplicas: row.maxReplicas ?? 1,
        minReplicas: row.scaleToZero ? 0 : Math.max(1, row.minReplicas ?? 1),
        scaleToZero: row.scaleToZero ?? false,
      },
      { onError: (error) => notify.error(error, "Couldn't start the deployment") }
    );
  };
  if (!ready) return null;
  return (
    <>
      <Dialog onOpenChange={setOpen} open={open}>
        <Card aria-label="Dedicated endpoint" className="overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-border/70 px-4 py-2">
            <div className="flex shrink-0 items-center gap-3">
              <h2 className="text-sm font-medium">Dedicated endpoint</h2>
              {row && STARTING.has(row.status ?? "") ? null : (
                <EndpointStatus
                  label={
                    deployment.isLoading
                      ? "Loading"
                      : deployment.error
                        ? "Unavailable"
                        : row
                          ? STATUS_LABEL[row.status ?? "offline"]
                          : "Not deployed"
                  }
                  state={
                    deployment.error || row?.status === "failed"
                      ? "error"
                      : deployment.isLoading || (active && row?.status !== "online")
                        ? "busy"
                        : row?.status === "online"
                          ? "online"
                          : "offline"
                  }
                />
              )}
            </div>
            {row && STARTING.has(row.status ?? "") ? <DedicatedStartupProgress row={row} /> : null}
            <div className="inline-flex shrink-0 items-center gap-2">
              {row ? (
                <Button
                  aria-label="Dedicated API snippet"
                  onClick={() => setSnippetOpen(true)}
                  size="sm"
                  variant="secondary"
                >
                  <Icon.terminal />
                  <span className="hidden lg:inline">Dedicated API snippet</span>
                </Button>
              ) : null}
              <div
                aria-label="GPU controls"
                className="inline-flex shrink-0 items-center"
                role="group"
              >
                <DialogTrigger asChild>
                  <Button
                    className={row ? "rounded-r-none" : undefined}
                    disabled={stopping || pending || deployment.isLoading || !!deployment.error}
                    size="sm"
                    variant="secondary"
                  >
                    Configure GPU
                  </Button>
                </DialogTrigger>
                {row ? (
                  <Button
                    className="rounded-l-none border-l-border"
                    disabled={stopping || pending || !!deployment.error}
                    onClick={toggleDeployment}
                    size="sm"
                    variant="secondary"
                  >
                    {pending || stopping ? (
                      <Spinner size="sm" />
                    ) : active ? (
                      <Icon.stop />
                    ) : (
                      <Icon.play />
                    )}
                    {stopping || stop.isPending
                      ? "Stopping"
                      : start.isPending
                        ? "Starting"
                        : active
                          ? "Stop"
                          : "Start"}
                  </Button>
                ) : null}
              </div>
            </div>
          </div>
          <div className="px-4 py-3">
            {deployment.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading deployment</p>
            ) : deployment.error ? (
              <p className="text-sm text-destructive">Could not load deployment</p>
            ) : row ? (
              <DeploymentStatus row={row} />
            ) : (
              <p className="text-sm text-muted-foreground">No dedicated GPU deployed</p>
            )}
          </div>
        </Card>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>Dedicated GPU</DialogTitle>
            <DialogDescription>{model.modelId}</DialogDescription>
          </DialogHeader>
          <DialogBody className="space-y-5">
            {gpus.isLoading || deployment.isLoading ? (
              <p className="text-sm text-muted-foreground">Loading GPUs</p>
            ) : null}
            {gpus.error ? (
              <p className="text-sm text-destructive">GPU options could not load</p>
            ) : null}
            {deployment.isFetched && gpus.data && row?.status !== "shutting_down" ? (
              <GpuMenu
                decision={model.inferenceContract === "decision"}
                gpus={gpus.data}
                model={model}
                row={row}
              />
            ) : null}
          </DialogBody>
        </DialogContent>
      </Dialog>
      {row ? (
        <ConfirmDialog
          confirmDisabled={startRate == null}
          confirmLabel="Start"
          description={
            gpus.isLoading
              ? `${row.gpuType} · Loading the current rate`
              : startRate == null
                ? `${row.gpuType} · The current rate is unavailable, so the endpoint cannot start.`
                : `${row.gpuType} · ${price(startRate)} while replicas run.`
          }
          error={gpus.error}
          isPending={gpus.isLoading}
          onConfirm={startDeployment}
          onOpenChange={setConfirmStart}
          open={confirmStart}
          title="Start dedicated endpoint"
        />
      ) : null}
      {row ? (
        <ApiSnippetDialog
          capabilityId={null}
          decisionDeploymentId={model.inferenceContract === "decision" ? model.id : undefined}
          modelId={row.modelId}
          onOpenChange={setSnippetOpen}
          open={snippetOpen}
          projectId={projectId}
          trainingJobId={model.finetuningJobId}
        />
      ) : null}
    </>
  );
}

function DeploymentStatus({ row }: { row: DedicatedDeployment }) {
  return (
    <div className="space-y-3">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 lg:grid-cols-4">
        {[
          ["GPU", row.gpuType],
          [
            "Replicas",
            `${row.runningReplicas ?? "—"} running · ${row.minReplicas ?? "—"}–${row.maxReplicas ?? "—"} configured`,
          ],
          [
            "Idle timeout",
            `${row.idleShutdownSeconds == null ? "—" : `${row.idleShutdownSeconds}s`}${row.scaleToZero ? " · scales to zero" : ""}`,
          ],
          ["Rate", price(row.creditsPerGpuHour)],
        ].map(([label, value]) => (
          <div className="space-y-1" key={label}>
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-sm">{value}</dd>
          </div>
        ))}
      </dl>
      {row.errorMessage ? <p className="text-sm text-destructive">{row.errorMessage}</p> : null}
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
  const [minReplicas, setMinReplicas] = useState(String(Math.max(1, row?.minReplicas ?? 1)));
  const [maxReplicas, setMaxReplicas] = useState(String(row?.maxReplicas ?? 1));
  const [scaleToZero, setScaleToZero] = useState(row?.scaleToZero ?? false);
  const [idleSeconds, setIdleSeconds] = useState(String(row?.idleShutdownSeconds ?? 900));
  const [syncedId, setSyncedId] = useState(row?.id ?? null);
  if (row && syncedId !== row.id) {
    setSyncedId(row.id);
    setGpuType(row.gpuType);
    setMinReplicas(String(Math.max(1, row.minReplicas ?? 1)));
    setMaxReplicas(String(row.maxReplicas ?? 1));
    setScaleToZero(row.scaleToZero ?? false);
    setIdleSeconds(String(row.idleShutdownSeconds ?? 900));
  }
  const start = useStartDedicatedMutation(model.id);
  const update = useUpdateDedicatedMutation(model.id);
  const pending = start.isPending || update.isPending;
  const active = row != null && ACTIVE.has(row.status ?? "");
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
      notify.error(
        error,
        active ? "Couldn't update the deployment" : "Couldn't start the deployment"
      );
    if (active) update.mutate(body, { onError });
    else start.mutate(body, { onError });
  };

  if (gpus.length === 0) {
    return <p className="text-sm text-muted-foreground">No GPU fits this model.</p>;
  }

  return (
    <div className="space-y-5">
      <div className="space-y-2">
        <label className="text-xs text-muted-foreground" htmlFor="dedicated-gpu">
          GPU
        </label>
        <Select onValueChange={setGpuType} value={gpuType}>
          <SelectTrigger aria-label="GPU" className="w-full" id="dedicated-gpu">
            <SelectValue>{gpuType}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {gpus.map((gpu) => (
              <SelectItem key={gpu.gpuType} value={gpu.gpuType}>
                {gpu.gpuType} · {price(gpu.creditsPerGpuHour)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selected && speed(selected, decision) !== "Unmeasured" ? (
          <p className="text-xs text-muted-foreground">{speed(selected, decision)}</p>
        ) : null}
      </div>
      <TooltipProvider delayDuration={150}>
        <div className="grid grid-cols-2 items-end gap-3 sm:grid-cols-2">
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
          <div className="space-y-1">
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
          <div className="flex h-8 items-center gap-2 self-end text-sm ">
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
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/70 pt-4">
        <span className="text-sm tabular-nums">
          {credits != null && minimum != null && maxCount != null
            ? `${(minimum * credits).toLocaleString(undefined, { maximumFractionDigits: 2 })}${minimum !== maxCount ? `–${(maxCount * credits).toLocaleString(undefined, { maximumFractionDigits: 2 })}` : ""} credits/hour`
            : "Price unavailable"}
        </span>
        <Button disabled={invalid || pending} onClick={submit} size="sm">
          <Icon.play />
          {pending ? "Saving" : active ? "Update" : "Deploy"}
        </Button>
      </div>
      {minimum != null && maxCount != null && minimum > maxCount ? (
        <p className="text-xs text-destructive">Minimum replicas exceeds maximum</p>
      ) : null}
      {hourWarning ? <p className="text-sm text-warning">{hourWarning}</p> : null}
    </div>
  );
}
