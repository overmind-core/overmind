import { useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";

import { JobDetailDrawer } from "@/components/finetuning/job-detail";
import { JobsHistory } from "@/components/finetuning/jobs-history";
import { TrainWizard } from "@/components/finetuning/train/train-wizard";
import { WizardMonitor } from "@/components/finetuning/training-monitor";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { TooltipProvider } from "@/components/ui/tooltip";
import { featureFlags } from "@/lib/feature-flags";
import { type TrainingSearch, trainingSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/training")({
  beforeLoad: () => {
    if (!featureFlags.finetuning) throw redirect({ replace: true, to: "/" });
  },
  component: TrainingPage,
  validateSearch: trainingSearchSchema,
});

const TRAINING_HEADER = (
  <PageHeader
    description="Fine-tune models on your own datasets."
    icon={<Icon.finetuning className="[image-rendering:pixelated] dark:invert" />}
    title="Training"
  />
);

function TrainingPage() {
  const search = Route.useSearch();
  const {
    projectId,
    groupId: monitorGroupId,
    job: peekJobId,
    jobId: monitorJobId,
    page,
    page_size: pageSize,
    train: trainParam,
    capabilityId: trainCapabilityId,
    datasetId: trainDatasetId,
    evalDatasetId: trainEvalDatasetId,
  } = search;
  const navigate = Route.useNavigate();
  // URL state, not local: the wizard is deep-linkable and the breadcrumb pops
  // back out of it.
  const creating = trainParam === true;
  const [createGroupId, setCreateGroupId] = useState(() => crypto.randomUUID());

  const patchSearch = (updates: Partial<TrainingSearch>) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, ...updates }),
    });

  const openTrainWizard = () => {
    setCreateGroupId(crypto.randomUUID());
    navigate({
      search: (prev) => ({ ...prev, train: true }),
    });
  };

  if (!projectId) {
    return (
      <PageShell header={TRAINING_HEADER} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  // Closing must clear the deep-link params too, or a later back/refresh
  // resurrects the wizard.
  const createWizard = (
    <TrainWizard
      groupId={createGroupId}
      initialCapabilityId={trainCapabilityId}
      initialDatasetId={trainDatasetId}
      initialEvalDatasetId={trainEvalDatasetId}
      onCancel={() =>
        navigate({
          search: (prev) => ({
            ...prev,
            capabilityId: undefined,
            datasetId: undefined,
            evalDatasetId: undefined,
            groupId: undefined,
            jobId: undefined,
            train: undefined,
          }),
        })
      }
      onLaunched={(gId) => {
        navigate({
          search: (prev) => ({
            ...prev,
            capabilityId: undefined,
            datasetId: undefined,
            evalDatasetId: undefined,
            groupId: gId,
            jobId: undefined,
            train: undefined,
          }),
        });
      }}
      open={creating}
      projectId={projectId}
    />
  );

  // Fragments, not a wrapper div: a div between the route outlet's flex column
  // and PageShell's `h-full min-h-0 flex-1` breaks the height chain, and the
  // page scrolls instead of the table.
  if (monitorGroupId) {
    return (
      <>
        <WizardMonitor
          groupId={monitorGroupId}
          jobId={monitorJobId ?? null}
          onFocusJob={(id) => patchSearch({ jobId: id ?? undefined })}
          projectId={projectId}
        />
        {createWizard}
      </>
    );
  }

  return (
    <>
      <TooltipProvider>
        <PageShell header={TRAINING_HEADER} variant="full">
          <div className="flex min-h-0 flex-1 flex-col gap-3">
            <JobsHistory
              action={
                <Button onClick={openTrainWizard}>
                  <Icon.ai /> Train model
                </Button>
              }
              filters={search}
              onMonitorRun={(run) =>
                navigate({
                  search: (prev) => ({
                    ...prev,
                    groupId: run.runId,
                    // Multi-experiment runs open in "All" mode; the run header's
                    // Experiments chips take it from there.
                    jobId: run.jobs.length === 1 ? run.jobs[0].id : undefined,
                  }),
                })
              }
              onPeekJob={(id) => patchSearch({ job: id })}
              onSearchChange={patchSearch}
              page={page}
              pageSize={pageSize}
              projectId={projectId}
            />
          </div>

          <JobDetailDrawer
            jobId={peekJobId ?? null}
            onOpenChange={(o) =>
              !o && navigate({ replace: true, search: (prev) => ({ ...prev, job: undefined }) })
            }
            projectId={projectId}
          />
        </PageShell>
      </TooltipProvider>
      {createWizard}
    </>
  );
}
