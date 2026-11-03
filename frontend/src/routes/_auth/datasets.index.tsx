import { useEffect, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";

import floppyDiskIcon from "@/assets/floppy-disk-save.svg";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { DataProjectForm } from "@/components/onboarding/data-project-form";
import { OnboardingWindow } from "@/components/onboarding/onboarding-window";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { QuickstartEmbed } from "@/components/quickstart/quickstart-embed";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useProjectDatasetsQuery } from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useProjectsList } from "@/hooks/use-projects";
import { datasetWorkspaces } from "@/lib/dataset-workspaces";
import { featureFlags } from "@/lib/feature-flags";
import { datasetsSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/datasets/")({
  beforeLoad: () => {
    if (!featureFlags.datasets) throw redirect({ replace: true, to: "/" });
  },
  component: DatasetsIndexPage,
  validateSearch: datasetsSearchSchema,
});

function DatasetsIndexPage() {
  const { projectId } = Route.useSearch();
  if (!projectId) {
    return (
      <PageShell header={null} variant="full">
        <ProjectRequiredEmptyState
          noProjects={
            <div className="grid min-w-0 flex-1 place-items-center">
              <QuickstartEmbed initialSource="data" />
            </div>
          }
        />
      </PageShell>
    );
  }
  return <ProjectDatasets key={projectId} projectId={projectId} />;
}

function ProjectDatasets({ projectId }: { projectId: string }) {
  const { create, ds_search, ds_role, ds_capability } = Route.useSearch();
  const navigate = Route.useNavigate();
  const query = useProjectDatasetsQuery(projectId);
  const capabilities = useProjectCapabilitiesQuery(projectId);
  const projects = useProjectsList();
  const project = projects.data?.projects.find((project) => project.id === projectId);
  const [createOpen, setCreateOpen] = useState(false);
  const guard = useGuestGate();
  const datasets = datasetWorkspaces(query.data ?? []);

  useEffect(() => {
    if (!create) return;
    setCreateOpen(true);
    void navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, create: undefined }),
    });
  }, [create, navigate]);

  const filters = {
    capabilityFilter: ds_capability,
    capabilityNameById: Object.fromEntries(
      (capabilities.data?.results ?? []).map((capability) => [capability.id, capability.name])
    ),
    datasets,
    roleFilter: ds_role,
    search: ds_search,
    setCapabilityFilter: (value: string) =>
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, ds_capability: value }),
      }),
    setRoleFilter: (value: string) =>
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, ds_role: value as typeof ds_role }),
      }),
    setSearch: (value: string) =>
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, ds_search: value }),
      }),
  };

  return (
    <PageShell
      header={
        <PageHeader
          actions={
            datasets.length > 0 ? (
              <Button onClick={guard(() => setCreateOpen(true))} type="button">
                <Icon.datasetAdd />
                New dataset
              </Button>
            ) : undefined
          }
          count={query.data ? datasets.length : undefined}
          description="Source data and step-by-step transformations for training and evaluation."
          icon={
            <img
              alt=""
              aria-hidden="true"
              className="size-6 shrink-0 [image-rendering:pixelated]"
              src={floppyDiskIcon}
            />
          }
          title="Datasets"
        />
      }
      variant="full"
    >
      {query.isSuccess && datasets.length === 0 && project ? (
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-y-auto py-4">
          <OnboardingWindow className="w-full max-w-2xl shrink-0" title="Get started with data">
            <DataProjectForm
              onCreated={({ projectId: createdProjectId, datasetId }) =>
                void navigate({
                  params: { datasetId },
                  search: { prepare: "training", projectId: createdProjectId },
                  to: "/datasets/$datasetId",
                })
              }
              project={project}
            />
          </OnboardingWindow>
        </div>
      ) : (
        <>
          <DatasetsToolbar {...filters} />
          <DatasetsBrowser
            {...filters}
            error={query.error}
            isLoading={
              query.isPending || (!datasets.length && projects.isPending && !query.isError)
            }
            key={JSON.stringify([ds_search, ds_role, ds_capability])}
            onRetry={() => query.refetch()}
            rowSearch={{ projectId }}
          />
        </>
      )}
      <NewDatasetDialog onOpenChange={setCreateOpen} open={createOpen} projectId={projectId} />
    </PageShell>
  );
}
