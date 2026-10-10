import { useEffect, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";

import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { useProjectDatasetsQuery } from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
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
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }
  return <ProjectDatasets key={projectId} projectId={projectId} />;
}

function ProjectDatasets({ projectId }: { projectId: string }) {
  const { create, ds_search, ds_intent, ds_capability } = Route.useSearch();
  const navigate = Route.useNavigate();
  const query = useProjectDatasetsQuery(projectId);
  const capabilities = useProjectCapabilitiesQuery(projectId);
  const [createOpen, setCreateOpen] = useState(false);
  const datasets = query.data ?? [];

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
    intentFilter: ds_intent,
    search: ds_search,
    setCapabilityFilter: (value: string) =>
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, ds_capability: value }),
      }),
    setIntentFilter: (value: string) =>
      void navigate({
        replace: true,
        resetScroll: false,
        search: (prev) => ({ ...prev, ds_intent: value as typeof ds_intent }),
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
          count={query.data?.length}
          description="Source data and step-by-step transformations for training and evaluation."
          title="Datasets"
        />
      }
      variant="full"
    >
      <DatasetsToolbar {...filters} />
      <DatasetsBrowser
        {...filters}
        error={query.error}
        isLoading={query.isPending}
        key={JSON.stringify([ds_search, ds_intent, ds_capability])}
        rowSearch={{ projectId }}
      />
      <NewDatasetDialog onOpenChange={setCreateOpen} open={createOpen} projectId={projectId} />
    </PageShell>
  );
}
