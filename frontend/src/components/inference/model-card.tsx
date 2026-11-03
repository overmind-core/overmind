import { useState } from "react";

import { Link } from "@tanstack/react-router";
import { Line, LineChart, ResponsiveContainer, Tooltip } from "recharts";

import { ApiSnippetDialog } from "@/components/inference/api-snippet-dialog";
import {
  DedicatedDeploymentSection,
  DedicatedStatusBadge,
} from "@/components/inference/dedicated-deployment";
import { FineTunedModelIdentity } from "@/components/inference/model-identity";
import { ServingStatusIndicator } from "@/components/inference/serving-status";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { Icon } from "@/components/ui/icons";
import {
  useModelActivityQuery,
  useModelLiveQuery,
  useModelMetricsQuery,
} from "@/hooks/use-inference";
import { cn } from "@/lib/utils";
import type { DeployedModel } from "@/openapi";

export function InferenceModelCard({
  model,
  projectId,
}: {
  model: DeployedModel;
  projectId?: string;
}) {
  const [snippet, setSnippet] = useState<{ modelId: string; dedicated: boolean } | null>(null);
  const { data: live } = useModelLiveQuery(model.id, model.status === "ready");
  const deleted = model.status === "deleting" || model.status === "deleted";
  const identity = (
    <FineTunedModelIdentity
      baseModelId={model.baseModelId}
      capabilityName={model.capabilityName}
      displayName={model.finetuningJobName}
      modelId={model.modelId}
    />
  );
  return (
    <>
      <DedicatedDeploymentSection
        model={model}
        projectId={projectId}
        render={(controls, deployment, loading, error) => (
          <Card
            aria-label={model.modelId}
            className={cn("overflow-hidden", deleted && "opacity-60")}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border/70 px-4 py-2">
              <div className="flex min-w-0 flex-wrap items-center gap-3">
                {deleted ? (
                  identity
                ) : (
                  <Link
                    className="min-w-0 hover:underline"
                    params={{ modelId: model.id }}
                    search={(prev) => ({ ...prev, projectId })}
                    to="/inference/$modelId"
                  >
                    {identity}
                  </Link>
                )}
                <ModelProviderChip compact model={model.baseModelId} />
                <ServingStatusIndicator live={live} status={model.status} />
                {deployment && <DedicatedStatusBadge status={deployment.status ?? "offline"} />}
                <Badge size="chip" variant="neutral">
                  {loading
                    ? "Loading deployment"
                    : error
                      ? "Deployment unavailable"
                      : deployment
                        ? "Dedicated"
                        : "Serverless"}
                </Badge>
              </div>
              <div
                aria-label="Inference controls"
                className="inline-flex shrink-0 items-center"
                role="group"
              >
                <Button
                  aria-label={deployment ? "Dedicated API snippet" : "Serverless API snippet"}
                  className="rounded-r-none"
                  disabled={deleted && !deployment}
                  onClick={() =>
                    setSnippet({
                      dedicated: !!deployment,
                      modelId: deployment?.modelId ?? model.modelId,
                    })
                  }
                  size="sm"
                  variant="secondary"
                >
                  <Icon.terminal />
                  API snippet
                </Button>
                {deployment && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        aria-label="Choose API snippet endpoint"
                        className="rounded-none border-l-border"
                        size="icon-sm"
                        variant="secondary"
                      >
                        <Icon.chevronDown />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        onSelect={() =>
                          setSnippet({ dedicated: true, modelId: deployment.modelId })
                        }
                      >
                        Dedicated API snippet
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={deleted}
                        onSelect={() => setSnippet({ dedicated: false, modelId: model.modelId })}
                      >
                        Serverless API snippet
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                {controls}
              </div>
            </div>
            <QuickMetrics modelId={model.id} />
          </Card>
        )}
      />
      <ApiSnippetDialog
        capabilityId={
          snippet?.dedicated || model.inferenceContract === "decision" ? null : model.capabilityId
        }
        decisionDeploymentId={model.inferenceContract === "decision" ? model.id : undefined}
        modelId={snippet?.modelId ?? model.modelId}
        onOpenChange={(open) => {
          if (!open) setSnippet(null);
        }}
        open={snippet !== null}
        projectId={projectId}
        trainingJobId={model.finetuningJobId}
      />
    </>
  );
}

const integer = (value: number) => value.toLocaleString();
const percent = (value: number) => `${value.toFixed(1)}%`;
const latency = (value: number) => `${Math.round(value)} ms`;
const speed = (value: number) => `${value.toFixed(1)} tok/s`;

// shortcut: Two metric requests per card; batch by page when large-page traffic is material.
function QuickMetrics({ modelId }: { modelId: string }) {
  const metrics = useModelMetricsQuery(modelId, "24h", "application");
  const activity = useModelActivityQuery(modelId, "hour", "24h", "application");
  const data = metrics.data;
  const points =
    activity.data?.points.map((point) => ({
      ...point,
      successRate: point.requestCount
        ? ((point.requestCount - point.failedRequestCount) / point.requestCount) * 100
        : null,
      time: point.bucket.getTime(),
    })) ?? [];
  const items = [
    { format: integer, key: "requestCount" as const, label: "Requests", value: data?.requestCount },
    {
      format: percent,
      key: "successRate" as const,
      label: "Success rate",
      value: data?.requestCount
        ? ((data.requestCount - data.failedRequestCount) / data.requestCount) * 100
        : null,
    },
    {
      format: latency,
      key: "endToEndP50Ms" as const,
      label: "Latency · p50",
      value: data?.endToEndP50Ms,
    },
    {
      format: speed,
      key: "avgTokensPerSecond" as const,
      label: "Generation speed",
      value: data?.avgTokensPerSecond,
    },
  ];
  return (
    <div
      aria-label="Application traffic · last 24 hours"
      className="grid grid-cols-2 divide-x divide-border/70 md:grid-cols-4"
    >
      {items.map((item) => (
        <div className="min-w-0 px-4 py-3" key={item.key}>
          <div className="flex items-baseline justify-between gap-2 text-xs text-muted-foreground">
            <span>{item.label}</span>
            <span>24h</span>
          </div>
          <div className="mt-2 flex items-center gap-3">
            <span className="shrink-0 text-sm tabular-nums">
              {item.value == null ? "—" : item.format(item.value)}
            </span>
            <div aria-label={`${item.label} trend`} className="h-8 min-w-0 flex-1" role="img">
              {points.some((point) => point[item.key] != null) ? (
                <ResponsiveContainer height="100%" width="100%">
                  <LineChart data={points} margin={{ bottom: 3, left: 2, right: 2, top: 3 }}>
                    <Tooltip
                      content={({ active, payload }) => (
                        <GraphTooltip
                          active={active}
                          payload={payload}
                          valueFormatter={(value) => (value == null ? "—" : item.format(value))}
                        />
                      )}
                    />
                    <Line
                      connectNulls={false}
                      dataKey={item.key}
                      dot={false}
                      isAnimationActive={false}
                      name={item.label}
                      stroke="var(--chart-1)"
                      strokeWidth={1.5}
                      type="linear"
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {activity.isPending
                    ? "Loading"
                    : activity.error
                      ? "Unavailable"
                      : "No measurements"}
                </span>
              )}
            </div>
          </div>
          {metrics.error && (
            <p className="mt-1 text-xs text-muted-foreground">
              {data ? "Refresh unavailable" : "Metrics unavailable"}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
