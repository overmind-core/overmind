import { useEffect, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";
import type { RowSelectionState } from "@tanstack/react-table";

import { ConnectionBackground } from "@/components/connection-scene";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { WorkshopComposer } from "@/components/datasets/workshop-composer";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { QuickstartEmbed } from "@/components/quickstart/quickstart-embed";
import { Button } from "@/components/ui/button";
import { CountChip } from "@/components/ui/count-chip";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useProjectDatasetsQuery } from "@/hooks/use-datasets";
import { useProjectCapabilitiesQuery } from "@/hooks/use-evaluations";
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
  const [createOpen, setCreateOpen] = useState(false);
  const [tableFocused, setTableFocused] = useState(false);
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const [composerLocked, setComposerLocked] = useState(false);
  const datasets = datasetWorkspaces(query.data ?? []);
  const selectedDatasets = Object.keys(rowSelection)
    .filter((id) => rowSelection[id])
    .flatMap((id) => datasets.find((dataset) => dataset.id === id) ?? []);

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
    <PageShell className="workshop-stage relative isolate gap-0" header={null} variant="full">
      <section
        aria-hidden={tableFocused}
        aria-label="Data Workshop"
        className="workshop-hero absolute inset-0 overflow-hidden"
        id="workshop-composer"
        inert={tableFocused}
      >
        <ConnectionBackground />
        <div className="workshop-hero-content relative flex min-h-0 flex-col items-center justify-center gap-5 px-6 pb-6 pt-4 sm:gap-7 sm:px-8 sm:pb-10">
          <PageHeader
            className="justify-center [&_h1]:text-4xl [&_h1]:leading-none sm:[&_h1]:text-5xl"
            title={
              <span className="inline-flex items-center gap-2">
                Ready to train
                <span
                  aria-hidden="true"
                  className="workshop-cursor h-[0.8em] w-[0.24em] bg-foreground"
                />
              </span>
            }
          />
          <div className="w-full max-w-3xl">
            <WorkshopComposer
              datasets={selectedDatasets}
              onLockedChange={setComposerLocked}
              onRemoveDataset={(id) =>
                setRowSelection((selected) => {
                  const next = { ...selected };
                  delete next[id];
                  return next;
                })
              }
              onStarted={(datasetId) =>
                void navigate({
                  params: { datasetId },
                  search: { projectId },
                  to: "/datasets/$datasetId",
                  viewTransition: !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
                })
              }
              projectId={projectId}
            />
          </div>
        </div>
      </section>
      <section
        aria-label="Available datasets"
        className="workshop-datasets absolute inset-x-0 bottom-0 z-10 flex min-h-0 flex-col border-t border-border bg-card"
        data-expanded={tableFocused}
        id="workshop-datasets"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-controls="workshop-datasets"
              aria-expanded={tableFocused}
              aria-label={tableFocused ? "Show composer" : "Focus datasets"}
              className="workshop-dataset-handle absolute left-1/2 z-10 w-12 border border-border bg-card hover:bg-control"
              onClick={() => setTableFocused((focused) => !focused)}
              size="icon-xs"
              variant="ghost"
            >
              <Icon.chevronUp
                className={`size-3 transition-transform duration-300 motion-reduce:transition-none ${tableFocused ? "rotate-180" : ""}`}
              />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{tableFocused ? "Show composer" : "Focus datasets"}</TooltipContent>
        </Tooltip>
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pb-4 pt-6 md:px-6 md:pb-6">
          <div className="flex shrink-0 items-center gap-2">
            <Icon.dataset className="size-4 text-muted-foreground" />
            <h2 className="text-sm font-medium">Datasets</h2>
            {query.data && <CountChip count={datasets.length} />}
            {selectedDatasets.length > 0 && (
              <Button
                className="ml-auto text-muted-foreground"
                onClick={() => {
                  setTableFocused(false);
                  requestAnimationFrame(() =>
                    document.getElementById("workshop-intent")?.focus({ preventScroll: true })
                  );
                }}
                size="xs"
                variant="secondary"
              >
                {selectedDatasets.length} attached
                <Icon.chevronUp />
              </Button>
            )}
          </div>
          <DatasetsToolbar {...filters} />
          <DatasetsBrowser
            {...filters}
            error={query.error}
            isLoading={query.isPending}
            key={JSON.stringify([ds_search, ds_role, ds_capability])}
            onRetry={() => query.refetch()}
            onRowSelectionChange={setRowSelection}
            rowSearch={{ projectId }}
            rowSelection={rowSelection}
            selectionDisabled={composerLocked}
          />
        </div>
      </section>
      <NewDatasetDialog onOpenChange={setCreateOpen} open={createOpen} projectId={projectId} />
    </PageShell>
  );
}
