import { CopyModelSwapPromptButton } from "@/components/finetuning/copy-model-swap-prompt-button";
import {
  LiveModelBadge,
  SetActiveModelButton,
} from "@/components/finetuning/set-active-model-button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDeployedModelsQuery } from "@/hooks/use-inference";
import { useCapabilityDetailQuery } from "@/hooks/use-query";
import type { DeployedModel, FinetuningJobList } from "@/openapi";

export function ModelLiveAction({
  capabilityId,
  allowPin = false,
  jobs,
  model,
  projectId,
  promote = false,
}: {
  capabilityId: string | null | undefined;
  allowPin?: boolean;
  jobs: FinetuningJobList[];
  /** Passed by the model detail page, which already holds it. */
  model?: DeployedModel;
  projectId: string;
  /** Primary face — only the model detail page, where a 404ing alias is the
      page's most important action. */
  promote?: boolean;
}) {
  const capabilityQuery = useCapabilityDetailQuery(capabilityId ?? "");
  // Same key as the capability's Models tab, so this shares that cache instead of
  // adding a request.
  const deployedQuery = useDeployedModelsQuery({
    capability: capabilityId || undefined,
    pageSize: 100,
    projectId,
  });

  const capability = capabilityQuery.data;
  const models = deployedQuery.data?.results ?? [];
  const jobId = jobs[0]?.id;
  const candidate = model ?? (jobId ? models.find((m) => m.finetuningJobId === jobId) : undefined);
  // What the alias answers with today; null when it answers nothing.
  const incumbent = models.find((m) => m.id === capability?.activeModel) ?? null;
  const isLive = !!candidate && capability?.activeModel === candidate.id;

  const control = (() => {
    if (!capabilityId) {
      return <CopyModelSwapPromptButton allowPin={allowPin} jobs={jobs} />;
    }
    if (!capability && capabilityQuery.isPending)
      return <Skeleton className="h-7 w-28 rounded-sm" />;

    const prompt = <CopyModelSwapPromptButton allowPin={allowPin} jobs={jobs} />;

    if (!candidate && deployedQuery.isPending) {
      return (
        <span className="inline-flex items-center gap-2">
          {prompt}
          <Skeleton className="h-7 w-24 rounded-sm" />
        </span>
      );
    }
    if (isLive) {
      return (
        <span className="inline-flex items-center gap-2">
          {prompt}
          <LiveModelBadge />
        </span>
      );
    }

    return (
      <span className="inline-flex items-center gap-2">
        {prompt}
        <SetActiveModelButton
          candidate={candidate}
          capabilityId={capabilityId}
          incumbent={incumbent}
          promote={promote && !incumbent}
        />
      </span>
    );
  })();

  return control;
}
