import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import apiClient from "@/client";
import { asNumber } from "@/components/optimiser/experiment-status";
import { isCrossExperimentPlateau } from "@/components/optimiser/plateau";
import { Button } from "@/components/ui/button";
import { FloatingNudge } from "@/components/ui/floating-nudge";
import { Icon } from "@/components/ui/icons";
import { useNudgeDismissalCount } from "@/hooks/use-nudge-dismissal";
import { featureFlags } from "@/lib/feature-flags";

const DISMISS_KEY = (capabilityId: string) =>
  `optimizer-finetune-suggest-dismissed:${capabilityId}`;

/** Re-appears after a dismiss only once another experiment completes. */
export function FinetuningSuggestion({
  capabilityId,
  projectId,
  currentImproved = false,
}: {
  capabilityId: string;
  projectId?: string;
  currentImproved?: boolean;
}) {
  const { data } = useQuery({
    enabled: featureFlags.finetuning && !!projectId && !currentImproved,
    queryFn: () =>
      apiClient.optimizerExperiments.optimizerExperimentsList({
        capability: capabilityId,
        pageSize: 100,
      }),
    queryKey: ["capability-experiments", capabilityId],
    staleTime: 30_000,
  });
  const experiments = data?.results ?? [];

  const completedWithScore = experiments.filter(
    (e) =>
      e.status === "completed" &&
      asNumber((e.scores as Record<string, unknown> | null)?.best) != null
  ).length;

  const { dismissed, dismiss } = useNudgeDismissalCount(
    DISMISS_KEY(capabilityId),
    completedWithScore
  );

  const show =
    featureFlags.finetuning &&
    projectId != null &&
    !currentImproved &&
    isCrossExperimentPlateau(experiments) &&
    !dismissed;

  if (!show) return null;

  return (
    <FloatingNudge
      cta={
        <Button asChild size="sm">
          <Link search={{ capabilityId, projectId, train: true }} to="/training">
            <Icon.finetuning />
            Try fine-tuning
          </Link>
        </Button>
      }
      description="Optimisation has plateaued — fine-tuning a model may help"
      icon={<Icon.ai className="size-5" />}
      onDismiss={dismiss}
      title="Scores aren't improving"
    />
  );
}
