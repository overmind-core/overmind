import { FineTunedModelIdentity } from "@/components/inference/model-identity";
import { DeployedModelStatusBadge } from "@/components/inference/status-badge";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useDeployedModelQuery } from "@/hooks/use-inference";
import { flowModels } from "@/lib/capability-flow";
import { cn } from "@/lib/utils";
import type { Capability } from "@/openapi";

function Fact({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 px-4 py-3", className)}>
      <p className="pixel-label mb-1.5 text-xs text-muted-foreground">{label}</p>
      {children}
    </div>
  );
}

/**
 * Once `overmind/<capability-id>` is routed, the served deployment *is* the capability's
 * model and the code-level value drops to the tooltip. `active_model` is a bare
 * UUID, hence the fetch by id.
 */
function ModelValue({ capability }: { capability: Capability }) {
  const models = flowModels(capability.flow);
  const { data, isLoading } = useDeployedModelQuery(capability.activeModel ?? undefined, {
    poll: false,
  });

  if (capability.activeModel && isLoading) return <Skeleton className="h-6 w-32" />;

  if (capability.activeModel && data) {
    const detail =
      models.length > 0
        ? `Serving ${data.modelId} — the code names ${models.join(", ")}`
        : `Serving ${data.modelId}`;
    return (
      <span className="flex h-6 min-w-0 items-center gap-1.5">
        {/* A real trigger, not a `title` on a non-interactive span: it belongs in
            the tab order with an accessible name. */}
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              aria-label={detail}
              className="flex min-w-0 items-center rounded-sm text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
              type="button"
            >
              <FineTunedModelIdentity
                baseModelId={data.baseModelId}
                capabilityName={data.capabilityName}
                displayName={data.finetuningJobName}
                modelId={data.modelId}
              />
            </button>
          </TooltipTrigger>
          <TooltipContent>{detail}</TooltipContent>
        </Tooltip>
        {/* A live model that stopped being READY means the alias is 503-ing. */}
        {data.status === "ready" ? null : <DeployedModelStatusBadge status={data.status} />}
      </span>
    );
  }

  // Also the fallback when a routed deployment can't be read: what the code names
  // is stale but true, which beats an "unavailable" dead end.
  if (models.length === 0) {
    return (
      <span className="flex h-6 items-center text-sm text-muted-foreground">No model set</span>
    );
  }
  return (
    <span className="flex h-6 flex-wrap items-center gap-1.5">
      {models.map((model) => (
        <ModelProviderChip key={model} model={model} />
      ))}
    </span>
  );
}

export function CapabilityFactsBar({ capability }: { capability: Capability }) {
  return (
    <TooltipProvider>
      <div className="flex flex-col divide-y divide-border/70 overflow-hidden rounded-md border border-border bg-card sm:flex-row sm:divide-x sm:divide-y-0">
        {/* A routed cell caps its width — a fine-tune name is long enough to eat
            the row. */}
        <Fact
          className={cn("px-3 py-2 sm:shrink-0", capability.activeModel && "sm:max-w-[18rem]")}
          label={capability.activeModel ? "Serving" : "Model"}
        >
          <ModelValue capability={capability} />
        </Fact>

        {capability.sourcePath ? (
          <Fact className="sm:min-w-0 sm:flex-1" label="Source">
            <span
              className="flex h-6 min-w-0 items-center font-mono text-sm"
              title={capability.sourcePath}
            >
              <span className="truncate">{capability.sourcePath}</span>
            </span>
          </Fact>
        ) : null}
      </div>
    </TooltipProvider>
  );
}
