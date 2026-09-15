import { createFileRoute, redirect } from "@tanstack/react-router";

import { ExperimentsTable } from "@/components/optimiser/experiments-table";
import { RunLocallyDialog } from "@/components/optimiser/run-locally-dialog";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { featureFlags } from "@/lib/feature-flags";
import { type OptimiserSearch, optimiserSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/optimiser/")({
  beforeLoad: () => {
    if (!featureFlags.evaluations) throw redirect({ replace: true, to: "/" });
  },
  component: OptimiserPage,
  validateSearch: optimiserSearchSchema,
});

function OptimiserPage() {
  const search = Route.useSearch();
  const {
    projectId,
    page,
    page_size: pageSize,
    optimize: optimizeParam,
    capabilityId,
    datasetId,
  } = search;
  const navigate = Route.useNavigate();
  // URL state, not local: deep-linked from the workshop rail / capability nudge.
  const dialogOpen = optimizeParam === true;

  const clearDialogSearch = () =>
    navigate({
      search: (prev) => ({
        ...prev,
        capabilityId: undefined,
        datasetId: undefined,
        optimize: undefined,
      }),
    });

  const openDialog = () =>
    navigate({
      search: (prev) => ({ ...prev, optimize: true }),
    });

  const patchSearch = (updates: Partial<OptimiserSearch>) =>
    navigate({
      replace: true,
      resetScroll: false,
      search: (prev) => ({ ...prev, ...updates }),
    });

  if (!projectId) {
    return (
      <PageShell
        header={
          <PageHeader
            description="Improve your agent against datasets and evals."
            icon={
              <Icon.optimiserTitle
                aria-hidden
                className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
              />
            }
            title="Optimiser"
          />
        }
        variant="full"
      >
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  return (
    <PageShell
      header={
        <PageHeader
          description="Improve your agent against datasets and evals."
          icon={
            <Icon.optimiserTitle
              aria-hidden
              className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
            />
          }
          title="Optimiser"
        />
      }
      variant="full"
    >
      <div className="flex min-h-0 flex-1 flex-col">
        <ExperimentsTable
          action={
            <Button onClick={openDialog}>
              <Icon.optimiser /> New run
            </Button>
          }
          filters={search}
          onSearchChange={patchSearch}
          page={page}
          pageSize={pageSize}
          projectId={projectId}
        />
      </div>

      <RunLocallyDialog
        capabilityId={capabilityId}
        datasetId={datasetId}
        onOpenChange={(open) => {
          if (!open) clearDialogSearch();
        }}
        open={dialogOpen}
        projectId={projectId}
      />
    </PageShell>
  );
}
