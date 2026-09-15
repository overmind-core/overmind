import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import apiClient from "@/client";
import { pickCapabilityNudge } from "@/components/capability-detail/pick-capability-nudge";
import { Button } from "@/components/ui/button";
import { FloatingNudge } from "@/components/ui/floating-nudge";
import { Icon } from "@/components/ui/icons";
import { useDatasetsQuery } from "@/hooks/use-datasets";
import { useDeployedModelsQuery } from "@/hooks/use-inference";
import { useNudgeDismissal } from "@/hooks/use-nudge-dismissal";
import { featureFlags } from "@/lib/feature-flags";

const DISMISS_KEY = (kind: string, capabilityId: string) =>
  `capability-nudge:${kind}:${capabilityId}`;

export function CapabilityNextStepNudge({
  capabilityId,
  projectId,
}: {
  capabilityId: string;
  projectId?: string;
}) {
  const enabled = !!projectId;

  const datasetsQuery = useDatasetsQuery(projectId, { capability: capabilityId, pageSize: 200 });
  const modelsQuery = useDeployedModelsQuery({
    capability: capabilityId,
    pageSize: 100,
    projectId,
  });
  const jobsQuery = useQuery({
    enabled: featureFlags.finetuning && enabled,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsList({
        capability: capabilityId,
        ordering: "-created_at",
        pageSize: 100,
        project: projectId,
      }),
    queryKey: ["finetuning-jobs", projectId, "capability", capabilityId],
    staleTime: 30_000,
  });
  const experimentsQuery = useQuery({
    enabled: featureFlags.evaluations && enabled,
    queryFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsList({
        capability: capabilityId,
        pageSize: 1,
      }),
    queryKey: ["capability-experiments-count", capabilityId],
    staleTime: 30_000,
  });

  const datasets = (datasetsQuery.data?.results ?? []).map((d) => ({
    contract: d.activeVersion ? (d.intent ?? null) : null,
  }));
  const nudge = pickCapabilityNudge({
    datasets,
    deployedModels: modelsQuery.data?.results ?? [],
    finetuningJobs: jobsQuery.data?.results ?? [],
    flags: {
      datasets: featureFlags.datasets,
      evaluations: featureFlags.evaluations,
      finetuning: featureFlags.finetuning,
      inference: featureFlags.inference,
    },
    optimiserExperimentCount: experimentsQuery.data?.count ?? 0,
  });

  const dismissKey = nudge ? DISMISS_KEY(nudge.kind, capabilityId) : "";
  const { dismissed, dismiss } = useNudgeDismissal(dismissKey);

  if (!projectId || !nudge || dismissed) return null;

  const copy = COPY[nudge.kind];
  return (
    <FloatingNudge
      cta={
        <Button asChild size="sm">
          {ctaLink(nudge, capabilityId, projectId)}
        </Button>
      }
      description={copy.description}
      icon={<copy.Icon className="size-5" />}
      onDismiss={dismiss}
      title={copy.title}
    />
  );
}

const COPY = {
  deploy: {
    description: "A fine-tuned model is ready to serve",
    Icon: Icon.inference,
    title: "Deploy fine-tuned model",
  },
  finetune: {
    description: "A training dataset is ready",
    Icon: Icon.finetuning,
    title: "Fine-tune a model",
  },
  optimise: {
    description: "An eval dataset is ready",
    Icon: Icon.optimiser,
    title: "Optimise your agent",
  },
  upload: {
    description: "No datasets attached to this capability",
    Icon: Icon.upload,
    title: "Upload a dataset",
  },
} as const;

function ctaLink(
  nudge: NonNullable<ReturnType<typeof pickCapabilityNudge>>,
  capabilityId: string,
  projectId: string
) {
  switch (nudge.kind) {
    case "deploy":
      if (nudge.modelId) {
        return (
          <Link params={{ modelId: nudge.modelId }} search={{ projectId }} to="/inference/$modelId">
            <Icon.inference />
            Deploy model
          </Link>
        );
      }
      return (
        <Link search={{ job: nudge.jobId, projectId }} to="/training">
          <Icon.inference />
          Deploy model
        </Link>
      );
    case "finetune":
      return (
        <Link search={{ capabilityId, projectId, train: true }} to="/training">
          <Icon.finetuning />
          Fine-tune
        </Link>
      );
    case "optimise":
      return (
        <Link search={{ capabilityId, optimize: true, projectId }} to="/optimiser">
          <Icon.optimiser />
          Optimise
        </Link>
      );
    case "upload":
      return (
        <Link search={{ create: true, ds_capability: capabilityId, projectId }} to="/datasets">
          <Icon.upload />
          Upload dataset
        </Link>
      );
  }
}
