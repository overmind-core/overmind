import { createFileRoute, redirect } from "@tanstack/react-router";

import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { NewRLRunDialog } from "@/components/rl/new-run-dialog";
import { RLRunsTable } from "@/components/rl/runs-table";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { featureFlags } from "@/lib/feature-flags";
import { rlTrainingSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/rl-training/")({
  beforeLoad: () => {
    if (!featureFlags.finetuning) throw redirect({ replace: true, to: "/" });
  },
  component: RLTrainingPage,
  validateSearch: rlTrainingSearchSchema,
});

const header = (
  <PageHeader
    description="Train a small model with GRPO, rewarded by your deterministic checks."
    icon={<Icon.target aria-hidden className="size-6 shrink-0" />}
    title="RL training"
  />
);

function RLTrainingPage() {
  const { projectId, newRun } = Route.useSearch();
  const navigate = Route.useNavigate();
  const setDialog = (open: boolean) =>
    navigate({ search: (prev) => ({ ...prev, newRun: open || undefined }) });

  if (!projectId) {
    return (
      <PageShell header={header} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell header={header} variant="scroll">
      <RLRunsTable
        action={
          <Button onClick={() => setDialog(true)}>
            <Icon.play /> Train from checks
          </Button>
        }
        projectId={projectId}
      />
      <NewRLRunDialog onOpenChange={setDialog} open={newRun === true} projectId={projectId} />
    </PageShell>
  );
}
