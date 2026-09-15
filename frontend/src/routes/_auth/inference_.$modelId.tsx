import { useState } from "react";

import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import {
  CartesianGrid,
  Line,
  LineChart,
  Tooltip as RechartsTooltip,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import { ModelLiveAction } from "@/components/finetuning/model-live-action";
import { ApiSnippetDialog } from "@/components/inference/api-snippet-dialog";
import {
  BaseModelCell,
  CapabilityChip,
  TrainingJobChip,
} from "@/components/inference/model-identity";
import { ServingStatusBadge } from "@/components/inference/serving-status";
import { DetailErrorState } from "@/components/route-error";
import {
  AlertDialog,
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
  useModelActivityQuery,
  useModelCheckpointsQuery,
  useModelLiveQuery,
  useModelMetricsQuery,
  useRetryModelMutation,
} from "@/hooks/use-inference";
import { notify } from "@/lib/notify";
import { inferenceSearchSchema } from "@/lib/schemas";
import { PROSE, TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type {
  DeployedModel,
  InferenceActivity,
  InferenceActivityPoint,
  InferenceMetrics,
} from "@/openapi";

export const Route = createFileRoute("/_auth/inference_/$modelId")({
  component: ModelDetailPage,
  // The list's schema, not the bare project one: zod strips undeclared keys, so
  // the filters and page must be declared here to survive the round trip out.
  validateSearch: inferenceSearchSchema,
});

const fmtInt = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: 0 });
const fmtMs = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(0)} ms`);
const fmtTps = (n: number | null | undefined) => (n == null ? "—" : `${n.toFixed(0)} tok/s`);

const CHART_HEIGHT = 224; // h-56

type Granularity = "minute" | "hour" | "day";

function ModelDetailPage() {
  const { modelId } = Route.useParams();
  const { projectId } = Route.useSearch();
  const [granularity, setGranularity] = useState<Granularity>("minute");
  const [snippetOpen, setSnippetOpen] = useState(false);

  const { data: model, isLoading, error, refetch } = useDeployedModelQuery(modelId);
  const { data: metrics } = useModelMetricsQuery(modelId);
  const { data: activity } = useModelActivityQuery(modelId, granularity);
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
          <OverviewCard
            actions={
              projectId ? (
                <>
                  <Button asChild size="sm" variant="secondary">
                    <Link
                      search={{ model: model.modelId, projectId, view: "roots" }}
                      to="/observability"
                    >
                      <Icon.observability />
                      View traces
                    </Link>
                  </Button>
                  <ModelLiveAction
                    allowPin
                    capabilityId={model.capabilityId}
                    jobs={jobs}
                    model={model}
                    projectId={projectId}
                    promote
                  />
                </>
              ) : null
            }
            metrics={metrics}
            model={model}
            projectId={projectId}
          />
          <ChartsSection
            activity={activity}
            granularity={granularity}
            onGranularityChange={setGranularity}
          />
          <Disclosure onOpenChange={setWeightsOpen} title="Weights & checkpoints">
            <WeightsContent
              checkpoints={checkpoints}
              deployedId={model.id}
              modelId={model.modelId}
            />
          </Disclosure>
          <DangerZone id={model.id} modelId={model.modelId} />
          <ApiSnippetDialog
            modelId={model.modelId}
            onOpenChange={setSnippetOpen}
            open={snippetOpen}
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

function OverviewCard({
  model,
  metrics,
  projectId,
  actions,
}: {
  model: DeployedModel;
  metrics: InferenceMetrics | undefined;
  projectId?: string;
  actions?: React.ReactNode;
}) {
  const meta = [
    {
      label: "Context",
      value: model.maxModelLen ? `${model.maxModelLen.toLocaleString()} tok` : "—",
    },
    ...(model.gpuType ? [{ label: "GPU", value: model.gpuType }] : []),
    { label: "Deployed", value: <DateTime value={model.deployedAt ?? model.createdAt} /> },
  ];

  return (
    <Card className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-wrap items-start gap-x-8 gap-y-3">
          {model.baseModelId ? (
            <LineageField label="Base model">
              <BaseModelCell baseModelId={model.baseModelId} />
            </LineageField>
          ) : null}
          {model.capabilityId ? (
            <LineageField label="Capability">
              <CapabilityChip
                capabilityId={model.capabilityId}
                capabilityName={model.capabilityName}
              />
            </LineageField>
          ) : null}
          {model.finetuningJobId ? (
            <LineageField label="Training job">
              <TrainingJobChip
                baseModelId={model.baseModelId}
                capabilityName={model.capabilityName}
                jobId={model.finetuningJobId}
                jobName={model.finetuningJobName}
                projectId={projectId}
              />
            </LineageField>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>

      <div className="flex flex-wrap items-start gap-x-10 gap-y-4 border-t border-border/70 pt-3.5">
        <HeaderStat label="Requests">{fmtInt(metrics?.requestCount)}</HeaderStat>
        <HeaderStat label="Tokens">{fmtInt(metrics?.totalTokens)}</HeaderStat>
        <HeaderStat label="Throughput">{fmtTps(metrics?.avgTokensPerSecond)}</HeaderStat>
        <HeaderStat label="Latency">{fmtMs(metrics?.avgLatencyMs)}</HeaderStat>
        <div className="ml-auto flex flex-wrap items-start gap-x-8 gap-y-2">
          {meta.map((m) => (
            <div className="flex flex-col gap-1.5" key={m.label}>
              <span className="text-xs font-medium text-muted-foreground">{m.label}</span>
              <span className="text-sm font-medium leading-none">{m.value}</span>
            </div>
          ))}
        </div>
      </div>
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

function HeaderStat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <span className="font-mono text-lg font-semibold tabular-nums leading-none">{children}</span>
    </div>
  );
}

const bucketLabel = (bucket: Date | string, granularity: Granularity) => {
  const opts: Intl.DateTimeFormatOptions =
    granularity === "day"
      ? { day: "numeric", month: "short" }
      : granularity === "hour"
        ? { day: "numeric", hour: "2-digit", month: "short" }
        : { hour: "2-digit", minute: "2-digit" };
  return new Date(bucket).toLocaleString(undefined, opts);
};

const compactNum = (v: number, fractionDigits: number) =>
  v.toLocaleString(undefined, { maximumFractionDigits: fractionDigits, notation: "compact" });

/** Linear-interpolated percentile; q in [0,1]. */
function pctl(sorted: number[], q: number): number {
  if (sorted.length === 1) return sorted[0];
  const i = q * (sorted.length - 1);
  const lo = Math.floor(i);
  const hi = Math.ceil(i);
  return lo === hi ? sorted[lo] : sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
}

/** Tukey upper fence (Q3 + 1.5·IQR) — robust outlier cutoff. */
function tukeyCap(values: number[]): number | null {
  if (values.length < 8) return null;
  const s = [...values].sort((a, b) => a - b);
  const q1 = pctl(s, 0.25);
  const q3 = pctl(s, 0.75);
  const fence = q3 + 1.5 * (q3 - q1);
  const max = s[s.length - 1];
  // Only meaningful when there really is a tall tail to tame.
  return fence < max ? fence : null;
}

/** Null buckets are dropped rather than plotted, so a gap doesn't read as zero. */
function TrendChart({
  points,
  name,
  color,
  pick,
  granularity,
  clampOutliers = false,
  integerTicks = false,
}: {
  points: InferenceActivityPoint[];
  name: string;
  color: string;
  pick: (p: InferenceActivityPoint) => number | null;
  granularity: Granularity;
  /** Clips spikes to a Tukey fence; the tooltip still shows the true value. */
  clampOutliers?: boolean;
  integerTicks?: boolean;
}) {
  const raw = points
    .map((p) => ({ label: bucketLabel(p.bucket, granularity), value: pick(p) }))
    .filter((d): d is { label: string; value: number } => d.value != null);
  if (!raw.length) {
    return (
      <p className="flex h-full items-center justify-center text-center text-sm text-muted-foreground">
        No data recorded yet.
      </p>
    );
  }
  const cap = clampOutliers ? tukeyCap(raw.map((d) => d.value)) : null;
  const data = raw.map((d) => ({
    clipped: cap != null && d.value > cap,
    label: d.label,
    raw: d.value,
    value: cap != null ? Math.min(d.value, cap) : d.value,
  }));
  const frac = integerTicks ? 0 : 1;
  return (
    <div className="h-full w-full [&_*]:outline-none" role="img">
      <ResponsiveContainer height="100%" width="100%">
        <LineChart data={data} margin={{ bottom: 4, left: -8, right: 8, top: 4 }}>
          <CartesianGrid className="stroke-border/60" strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            interval="preserveStartEnd"
            minTickGap={40}
            tick={{ fontSize: 10 }}
            tickLine={false}
          />
          <YAxis
            allowDecimals={!integerTicks}
            domain={cap != null ? [0, Math.ceil(cap * 1.08)] : undefined}
            tick={{ fontSize: 10 }}
            tickFormatter={(v) => (typeof v === "number" ? compactNum(v, frac) : String(v))}
            tickLine={false}
            width={40}
          />
          {cap != null && (
            <ReferenceLine
              label={{
                fill: "var(--muted-foreground)",
                fontSize: 9,
                position: "insideTopRight",
                value: "clipped",
              }}
              stroke="var(--muted-foreground)"
              strokeDasharray="3 3"
              strokeOpacity={0.5}
              y={cap}
            />
          )}
          <RechartsTooltip content={<ChartTooltip color={color} name={name} />} />
          <Line
            connectNulls
            dataKey="value"
            dot={data.length <= 12}
            isAnimationActive={false}
            name={name}
            stroke={color}
            strokeWidth={1.5}
            type="monotone"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChartTooltip({
  active,
  payload,
  label,
  color,
  name,
}: {
  active?: boolean;
  payload?: Array<{ value: number | null; payload?: { raw?: number; clipped?: boolean } }>;
  label?: string;
  color: string;
  name: string;
}) {
  if (!active || !payload?.length) return null;
  const entry = payload[0];
  // Prefer the true value over the clipped one shown on the line.
  const v = entry?.payload?.raw ?? entry?.value;
  const clipped = entry?.payload?.clipped;
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2 text-xs">
      <p className="mb-1 font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2">
        <span className="inline-block size-2 rounded-xs" style={{ background: color }} />
        <span className="text-muted-foreground">{name}</span>
        <span className="ml-auto pl-3 font-mono tabular-nums">
          {typeof v === "number" ? Math.round(v).toLocaleString() : "—"}
          {clipped && <span className="ml-1 text-warning">▲</span>}
        </span>
      </div>
    </div>
  );
}

const GRANULARITIES: Array<{ value: Granularity; label: string }> = [
  { label: "Min", value: "minute" },
  { label: "Hour", value: "hour" },
  { label: "Day", value: "day" },
];

function GranularityToggle({
  value,
  onChange,
}: {
  value: Granularity;
  onChange: (g: Granularity) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-border p-0.5">
      {GRANULARITIES.map((g) => (
        <button
          className={cn(
            "rounded-sm px-2 py-0.5 text-xs font-medium transition-colors",
            value === g.value
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
          key={g.value}
          onClick={() => onChange(g.value)}
          type="button"
        >
          {g.label}
        </button>
      ))}
    </div>
  );
}

function ChartsSection({
  activity,
  granularity,
  onGranularityChange,
}: {
  activity: InferenceActivity | undefined;
  granularity: Granularity;
  onGranularityChange: (g: Granularity) => void;
}) {
  const points = activity?.points ?? [];
  const hasData = points.length > 0;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className={TITLE.section}>Metrics</h2>
        <GranularityToggle onChange={onGranularityChange} value={granularity} />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
        <ChartBlock subtitle="requests over time" title="Activity">
          {hasData ? (
            <TrendChart
              color="var(--chart-1)"
              granularity={granularity}
              integerTicks
              name="Requests"
              pick={(p) => p.requestCount}
              points={points}
            />
          ) : null}
        </ChartBlock>
        <ChartBlock subtitle="tokens per second" title="Throughput">
          {hasData ? (
            <TrendChart
              color="var(--chart-2)"
              granularity={granularity}
              integerTicks
              name="tok/s"
              pick={(p) => p.avgTokensPerSecond}
              points={points}
            />
          ) : null}
        </ChartBlock>
        <ChartBlock subtitle="model inference, ms" title="Latency">
          {hasData ? (
            <TrendChart
              clampOutliers
              color="var(--chart-3)"
              granularity={granularity}
              integerTicks
              name="ms"
              pick={(p) => p.avgLatencyMs}
              points={points}
            />
          ) : null}
        </ChartBlock>
      </div>
    </div>
  );
}

function ChartBlock({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  const hasContent = children != null && children !== false;
  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex h-4 items-center gap-2">
        <h3 className="text-xs font-medium leading-none">{title}</h3>
        {subtitle && (
          <span className="truncate text-xs leading-none text-muted-foreground">{subtitle}</span>
        )}
      </div>
      <div className="min-h-0 w-full" style={{ height: CHART_HEIGHT }}>
        {hasContent ? (
          children
        ) : (
          <div className="flex h-full items-center justify-center text-center text-sm text-muted-foreground">
            No inference calls recorded yet.
          </div>
        )}
      </div>
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
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3">
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-destructive">Danger zone</p>
        <p className={cn(PROSE, "text-sm leading-snug text-destructive/90")}>
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
      <Button className="shrink-0" onClick={() => setOpen(true)} size="sm" variant="destructive">
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
        <div className="space-y-1.5">
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
        </div>
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
