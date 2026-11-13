import { useEffect, useState } from "react";

import { createFileRoute, redirect } from "@tanstack/react-router";
import type { RowSelectionState } from "@tanstack/react-table";

import { AssistantComposer } from "@/components/assistant/assistant-composer";
import { ConnectionBackground } from "@/components/connection-scene";
import { DatasetsBrowser, DatasetsToolbar } from "@/components/datasets/datasets-table";
import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
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
import { useGuestGate } from "@/hooks/use-guest-gate";
import { datasetWorkspaces } from "@/lib/dataset-workspaces";
import { featureFlags } from "@/lib/feature-flags";
import { datasetsSearchSchema } from "@/lib/schemas";
import { cn } from "@/lib/utils";

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
  // Selection survives filtering and paging: it is keyed by dataset id.
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  const guard = useGuestGate();
  const datasets = datasetWorkspaces(query.data ?? []);
  const attached = datasets.filter((dataset) => rowSelection[dataset.id]);

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
    <PageShell className="assistant-stage relative isolate gap-0" header={null} variant="full">
      <section
        aria-hidden={tableFocused}
        aria-label="Start with the assistant"
        className="absolute inset-0 overflow-hidden"
        inert={tableFocused}
      >
        <ConnectionBackground />
        <div className="assistant-hero-content relative flex min-h-0 flex-col items-center justify-center gap-5 px-6 pt-4 pb-6 sm:gap-7 sm:px-8 sm:pb-10">
          <PageHeader
            className="justify-center [&_h1]:text-4xl [&_h1]:leading-none sm:[&_h1]:text-5xl"
            title={
              <span className="inline-flex items-center gap-2">
                Ready to train
                <span aria-hidden="true" className="assistant-cursor bg-foreground" />
              </span>
            }
          />
          <div className="w-full max-w-3xl">
            <AssistantComposer
              datasets={attached}
              onRemoveDataset={(id) => setRowSelection(({ [id]: _removed, ...rest }) => rest)}
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
        className="assistant-datasets absolute inset-x-0 bottom-0 z-10 flex min-h-0 flex-col border-border border-t bg-card"
        data-expanded={tableFocused}
        id="assistant-datasets"
      >
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-controls="assistant-datasets"
              aria-expanded={tableFocused}
              aria-label={tableFocused ? "Show composer" : "Focus datasets"}
              className="assistant-dataset-handle absolute left-1/2 z-10 w-12 border border-border bg-card hover:bg-control"
              onClick={() => setTableFocused((focused) => !focused)}
              size="icon-xs"
              variant="ghost"
            >
              <Icon.chevronUp
                className={cn(
                  "size-3 transition-transform duration-300 motion-reduce:transition-none",
                  tableFocused && "rotate-180"
                )}
              />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{tableFocused ? "Show composer" : "Focus datasets"}</TooltipContent>
        </Tooltip>
        <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 pt-6 pb-4 md:px-6 md:pb-6">
          <div className="flex shrink-0 items-center gap-2">
            <Icon.dataset className="size-4 text-muted-foreground" />
            <h2 className="text-sm font-medium">Datasets</h2>
            {query.data && <CountChip count={datasets.length} />}
            <div className="ml-auto flex items-center gap-2">
              {attached.length > 0 && (
                <Button
                  className="text-muted-foreground"
                  onClick={() => {
                    setTableFocused(false);
                    requestAnimationFrame(() =>
                      document.getElementById("assistant-intent")?.focus({ preventScroll: true })
                    );
                  }}
                  size="xs"
                  variant="secondary"
                >
                  {attached.length} attached
                  <Icon.chevronUp />
                </Button>
              )}
              <Button onClick={guard(() => setCreateOpen(true))} size="xs" variant="secondary">
                <Icon.datasetAdd />
                New dataset
              </Button>
            </div>
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
          />
        </div>
      </section>
      <NewDatasetDialog onOpenChange={setCreateOpen} open={createOpen} projectId={projectId} />
    </PageShell>
  );
}
